"""Bucle async del gateway falso: se comporta en MQTT como el gateway real del wearable.

Por cada astronauta del config (`FakeGateway`, normalmente uno por conexión MQTT, ver `serve`):
- Se suscribe a `topics.cmd(astronaut_id, device_id)`. Cada comando se valida contra el JSON Schema
  (`spine_contracts.validation`) y con `spine_contracts.command_adapter`, y se responde en
  `topics.ack(...)` (CommandAck, QoS 1). Todo comando aceptado recibe `accepted` y, al acabar, `completed`:
  - start_session: `accepted` y empieza a publicar los chunks de esa sesión (`MissionGenerator.live_session`);
    `completed` cuando acaba (stop_session o `max_duration_s`). `rejected` con `busy` si ya hay una sesión,
    `unknown_stream` si pide un stream que el dispositivo no tiene y `device_offline` si está reiniciando.
  - stop_session: `accepted`, vacía lo que el dispositivo ya había comprometido, `completed` del
    start_session y luego `completed` del stop. `rejected`/`no_session` si no hay sesión o no coincide.
  - mark_event: `accepted` + `completed` (solo con la sesión indicada en curso; si no, `no_session`). No
    cambia la señal: el protocolo en vivo sigue su propio ritmo.
  - calibrate: `accepted` y `completed` con `result.calibration_id` (`MissionGenerator.live_calibration`)
    pasados `duration_s` (o al momento). Justo antes del `completed` se publica cada calibración en
    `topics.calibration(...)` (QoS 1, retained), salvo que otro `calibrate` del mismo stream la haya
    sustituido mientras tanto. El contrato solo admite un calibration_id por ack: con varios streams va el
    del primero (todas pasan a estar vigentes igualmente).
  - Comando inválido: `rejected`/`invalid_command` si se puede leer su command_id; si no, se ignora.
    Un error inesperado al procesarlo da `failed`/`internal`; el gateway nunca se cae por un comando.
- DeviceStatus retained en `topics.status(...)` al arrancar y cada `device.status_interval_s`; ClockSync
  retained en `topics.clock(...)` al arrancar, al empezar cada sesión y cada `device.clock_sync_interval_s`.
  `last_seq` de cada stream = último seq PUBLICADO del arranque en curso (nunca uno aún no enviado).
  Last Will retained con `status: offline` (`last_will`) y el mismo `offline` al pararse limpiamente.
- Calibraciones en el bus: antes de publicar un chunk con `calibration_id`, si cita la calibración vigente
  de su stream (`MissionGenerator.calibration_in_force`: la de un `calibrate` o, si no, la programada de la
  misión ese día) y no es la retained de `topics.calibration(...)`, se publica (retained). Así un consumidor
  que se conecta tarde resuelve el calibration_id de los chunks que recibe. Tras un `calibrate`, los chunks
  que quedan del bloque en curso (como mucho un intervalo de estado) aún citan la anterior: no se republica
  si ya salió otra después, de modo que la retained de un stream nunca vuelve atrás. El ground truth nunca
  va al bus.
- Fondo (si `schedule.background.enabled`): IMU + cordón de `live_background` siempre que no hay sesión
  (sin la ventana horaria del config: el gateway es para demos a cualquier hora). Una sesión lo pausa
  (mismo stream_id, el seq sigue) y al acabar se reanuda con el mismo session_id; cada día de misión
  nuevo tiene su session_id de fondo.
- Día de misión = `start_day` + (reloj de pared − arranque) × `speed` / 1 día, acotado a la misión. Lo
  usan las sesiones y el fondo al empezar (una sesión no cambia de día a media sesión).
- Dispositivo: `plan_device_live` desde el arranque, con horizonte = la ventana diaria de encendido del
  config (una carga de batería). Al agotarse, y sin sesión en curso, se "recarga": plan nuevo (boot_id
  nuevo, seq y reloj a 0, batería llena) y se publica estado y anclaje al momento.

Los chunks se publican a su `received_at` según el reloj inyectado (`clock`) y se espera con `sleep`
(inyectable): los tests corren en tiempo virtual y con un cliente MQTT falso (Protocol `MqttClient`). La
espera máxima es un chunk de la fuente activa (sesión: `signals.chunk_ms`; fondo:
`schedule.background.chunk_ms`) y un comando la corta al momento.

Detalle del vaciado: leer el siguiente chunk de un iterador en vivo compromete en el dispositivo el seq
de ese chunk en todos los streams. Al pausar o parar, se publican los chunks ya comprometidos (pueden
llevar `received_at` unos ms en el futuro) y la siguiente fuente empieza tras el último, así el seq de
cada stream sigue sin huecos y los tramos no se solapan.

Síntesis fuera del bucle: leer de un iterador en vivo puede sintetizar un protocolo entero (EMG filtrado,
todos los streams), así que se hace en un hilo (`asyncio.to_thread`) mientras se tiene `_lock`: los
comandos esperan al lock como antes, pero el bucle (keepalive de MQTT, recepción) sigue vivo. Lo leído se
guarda en el `_Source` y solo se quita al publicarse; si la tarea se cancela a media lectura, el hilo
acaba y su resultado se recoge en la siguiente (`_Source.settle`), sin perder ni repetir chunks.

Reconexión (`_serve`): si el broker rechaza la conexión o la corta (MqttError), se espera con backoff
exponencial (`mqtt_reconnect_*` de los settings) y se abre otra conexión (con su Last Will) sobre el mismo
`FakeGateway`: sesión, fondo, seq del dispositivo y chunks ya leídos siguen igual. Un chunk cuyo publish
falla no se quita y se publica en la conexión siguiente (sin hueco de seq; como mucho un duplicado si el
broker llegó a recibirlo, lo normal en QoS 1). Al reconectar se republican estado y anclaje al momento
(el Last Will habrá dejado `offline`) y, antes del siguiente chunk, su calibración (el broker puede haber
perdido los retained). SIGTERM sigue parando limpio con el `offline` retained.
"""

import asyncio
import contextlib
import json
import logging
import math
import re
import signal
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal, Protocol, cast

import aiomqtt
import numpy as np
from spine_contracts import (
    SCHEMA_VERSION,
    CalibrateCommand,
    CommandAck,
    DeviceStatus,
    MarkEventCommand,
    StartSessionCommand,
    StopSessionCommand,
    command_adapter,
    dump,
    topics,
    validation,
)

from spine_physiology import sessions as sess
from spine_physiology.config import CrewMember, MissionConfig, load_config
from spine_physiology.device import SimDevice, plan_device_live
from spine_physiology.envelope import Envelope, EnvelopeKind
from spine_physiology.gateway.settings import GatewaySettings
from spine_physiology.generator import MissionGenerator, calibration_envelope
from spine_physiology.rng import derive_uuid
from spine_physiology.rng import generator as rng_generator
from spine_physiology.sinks.mqtt import QOS, MqttPublisher

_log = logging.getLogger(__name__)

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DAY_S = 86400.0
_MESSAGE_MAX = 300  # maxLength de error.message en command-ack.schema.json
# Los comandos del esquema son diminutos (cientos de bytes): por encima de esto ni se parsea (un JSON enorme o
# anidado hasta el absurdo no debe costar tiempo ni tumbar el bucle). Si se lee su command_id, `invalid_command`.
_PAYLOAD_MAX_BYTES = 16 * 1024
_COMMAND_ID_PREFIX = re.compile(rb'"command_id"\s*:\s*"([0-9a-f-]{36})"')
# Tope de max_duration_s / duration_s: el esquema no lo acota y un valor enorme desborda timedelta/datetime
# (OverflowError) cuando ya se habría tocado el estado. 30 días cubre de sobra cualquier misión de demo.
_DURATION_MAX_S = 30 * 86400
# Comandos ya tratados por dispositivo (LRU): un QoS 1 puede reentregarse (DUP) y entonces se reenvían los
# mismos acks en vez de volver a ejecutar el comando.
_HANDLED_MAX = 256
# Envelopes por lectura en el hilo de síntesis (acota memoria si el reloj salta mucho de golpe).
_PULL_MAX = 256

type AckStatus = Literal["accepted", "rejected", "completed", "failed"]
type AckCode = Literal["busy", "unknown_stream", "no_session", "device_offline", "calibration_failed", "invalid_command", "internal"]


class IncomingMessage(Protocol):
    """Lo que usa el gateway de `aiomqtt.Message`."""

    @property
    def topic(self) -> object: ...  # aiomqtt.Topic; str(topic) da el topic

    @property
    def payload(self) -> bytes | bytearray | str | int | float | None: ...


class MqttClient(MqttPublisher, Protocol):
    """Lo que usa el gateway de `aiomqtt.Client`."""

    async def subscribe(self, topic: str, qos: int) -> object: ...

    @property
    def messages(self) -> AsyncIterator[IncomingMessage]: ...


def _encode(payload: dict[str, Any]) -> bytes:
    """JSON compacto en UTF-8 (lo mismo que publicaría el sink MQTT)."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _as_bytes(payload: bytes | bytearray | str | float | None) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, bytes | bytearray):
        return bytes(payload)
    return str(payload).encode("utf-8")


def _seconds_of_day(t: time) -> float:
    return t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6


def _next_after(at: datetime, step: timedelta, now: datetime) -> datetime:
    """Primer `at + n × step` (n >= 1) posterior a `now`."""
    if at > now:
        return at
    return at + (math.floor((now - at) / step) + 1) * step


def _pull_bus(iterator: Iterator[Envelope], until: datetime | None) -> list[Envelope]:
    """(En un hilo.) Lee del iterador en vivo, sintetizando lo que haga falta, hasta el primer envelope del
    bus posterior a `until` (incluido); con `until` None, uno solo; como mucho `_PULL_MAX`. El ground truth
    (sin topic) se descarta: solo JSONL, nunca al bus."""
    out: list[Envelope] = []
    while len(out) < _PULL_MAX:
        envelope = next(iterator)
        if envelope.topic is None:
            continue
        out.append(envelope)
        if until is None or envelope.at > until:
            break
    return out


class _Source:
    """Iterador en vivo (sesión o fondo) con los envelopes del bus ya leídos y aún sin publicar.

    La lectura (que compromete seq en el dispositivo y puede sintetizar un bloque entero) corre en un hilo;
    solo debe lanzarse con el `_lock` del gateway tomado. Un envelope sale del buffer (`take`) solo tras
    publicarse.
    """

    def __init__(
        self, iterator: Iterator[Envelope], *, device: SimDevice, session_id: str, day: int, streams: tuple[str, ...], chunk: timedelta
    ) -> None:
        self._iterator = iterator
        self._buffer: deque[Envelope] = deque()
        # Lectura en curso en un hilo; sobrevive a la cancelación de la tarea que la lanzó (ver `settle`).
        self._pending: asyncio.Future[list[Envelope]] | None = None
        self.device = device
        self.session_id = session_id
        self.day = day
        self.streams = streams
        self.chunk = chunk  # duración de chunk de esta fuente (sesión o fondo): marca el ritmo del bucle
        self.boot_id: int | None = None  # arranque del último chunk leído

    @property
    def buffered(self) -> bool:
        return bool(self._buffer)

    async def settle(self) -> None:
        """Recoge una lectura que quedó en vuelo (tarea cancelada, p. ej. por una desconexión): al volver,
        ningún hilo toca ya el dispositivo ni el iterador."""
        pending = self._pending
        if pending is None:
            return
        try:
            # shield: si cancelan esta tarea, el hilo sigue y su resultado se recoge en la próxima llamada.
            got = await asyncio.shield(pending)
        except asyncio.CancelledError:
            if pending.cancelled():  # solo si el bucle se cierra: nada que recoger
                self._pending = None
            raise
        except BaseException:
            self._pending = None
            raise
        self._pending = None
        self._buffer.extend(got)
        if got:
            self.boot_id = int(got[-1].payload["boot_id"])

    async def peek(self, until: datetime | None = None) -> Envelope:
        """El siguiente envelope del bus sin publicar; si no hay, lee (en un hilo) hasta pasar `until`."""
        await self.settle()
        while not self._buffer:
            self._pending = asyncio.ensure_future(asyncio.to_thread(_pull_bus, self._iterator, until))
            await self.settle()
        return self._buffer[0]

    def take(self) -> Envelope:
        """Quita el primero (ya publicado)."""
        return self._buffer.popleft()


@dataclass(slots=True)
class _Session:
    session_id: str
    command_id: str  # el del start_session, para su `completed`
    source: _Source
    deadline: datetime | None


@dataclass(slots=True)
class _Unit:
    """Estado de un dispositivo (un astronauta)."""

    member: CrewMember
    device: SimDevice
    device_until: datetime
    status_rng: np.random.Generator
    clock_rng: np.random.Generator
    next_status: datetime
    next_clock: datetime
    session: _Session | None = None
    background: _Source | None = None
    # Último seq publicado por (boot_id, stream_id).
    published: dict[tuple[int, str], int] = field(default_factory=dict)
    # Lo que sale más tarde, en orden: calibraciones y el `completed` de un calibrate con duración.
    pending: list[tuple[datetime, Envelope]] = field(default_factory=list)
    # command_id -> acks ya enviados (LRU acotada a `_HANDLED_MAX`), para responder igual a una reentrega.
    handled: OrderedDict[str, list[Envelope]] = field(default_factory=OrderedDict)
    # topic de calibración -> calibration_id retained ahí por esta conexión (se vacía al reconectar).
    retained_calibrations: dict[str, str] = field(default_factory=dict)
    # topic de calibración -> último calibration_id publicado en cualquier conexión (no se vacía al reconectar).
    bus_calibrations: dict[str, str] = field(default_factory=dict)
    unresolved_calibrations: set[str] = field(default_factory=set)  # ids sin calibración conocida (avisados ya)

    def sources(self) -> list[_Source]:
        return [s for s in (self.session.source if self.session is not None else None, self.background) if s is not None]

    @property
    def astronaut_id(self) -> str:
        return self.member.astronaut_id

    @property
    def device_id(self) -> str:
        return self.member.device_id


class FakeGateway:
    def __init__(
        self,
        generator: MissionGenerator,
        settings: GatewaySettings,
        *,
        clock: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        astronaut_ids: Sequence[str] | None = None,
    ) -> None:
        """`clock` devuelve el instante UTC actual (aware); `sleep` espera esos segundos (tiempo virtual en
        tests). `astronaut_ids` limita el gateway a esos astronautas (None = toda la tripulación)."""
        config = generator.config
        if not 0 <= settings.start_day < config.total_days:
            raise ValueError(f"start_day {settings.start_day} outside the mission (0..{config.total_days - 1})")
        self._generator = generator
        self._config = config
        self._speed = settings.speed
        self._start_day = settings.start_day
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()  # un comando procesado despierta al bucle (puede haber empezado una sesión)
        # Cada fuente a su ritmo: las sesiones trocean en `signals.chunk_ms` y el fondo en el suyo.
        self._session_chunk = sess.chunk_delta(sess.chunk_ms_for(config, background=False))
        self._background_chunk = sess.chunk_delta(sess.chunk_ms_for(config, background=True))
        self._stream_ids = {s.stream_id for s in config.device.streams}
        device = config.device
        self._horizon = timedelta(seconds=_seconds_of_day(device.power_off_time_utc) - _seconds_of_day(device.power_on_time_utc))
        self._status_every = timedelta(seconds=device.status_interval_s)
        self._clock_every = timedelta(seconds=device.clock_sync_interval_s)
        self._started_at = self._now()
        members = config.crew if astronaut_ids is None else [config.member(a) for a in astronaut_ids]
        self._units = [self._boot(member, self._started_at) for member in members]
        self._by_cmd_topic = {topics.cmd(u.astronaut_id, u.device_id): u for u in self._units}

    # --- tiempo -----------------------------------------------------------------------------------

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError(f"clock must return an aware UTC datetime, got {now!r}")
        return now

    def mission_day(self, now: datetime) -> int:
        """Índice de día de misión en `now`: avanza con el reloj de pared × speed, acotado a la misión."""
        elapsed = max(0.0, (now - self._started_at).total_seconds())
        day = self._start_day + math.floor(elapsed * self._speed / _DAY_S)
        return min(day, self._config.total_days - 1)

    @property
    def command_topics(self) -> list[str]:
        return list(self._by_cmd_topic)

    # --- dispositivo ------------------------------------------------------------------------------

    def _boot(self, member: CrewMember, now: datetime) -> _Unit:
        seed = self._generator.seed
        plan = plan_device_live(self._config, seed, member.astronaut_id, boot_at=now, horizon=self._horizon)
        keys = (member.astronaut_id, "live", now.isoformat())
        return _Unit(
            member=member, device=SimDevice(self._config, plan), device_until=now + self._horizon,
            status_rng=rng_generator(seed, *keys, "status"), clock_rng=rng_generator(seed, *keys, "clock_sync"),
            next_status=now, next_clock=now,
        )  # fmt: skip

    def _offline_status(self, member: CrewMember, at: datetime, *, battery_pct: float | None = None) -> DeviceStatus:
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION, "astronaut_id": member.astronaut_id, "device_id": member.device_id,
            "timestamp": at, "status": "offline", "source": "simulator", "firmware_version": self._config.device.firmware_version,
        }  # fmt: skip
        if battery_pct is not None:
            payload["battery_pct"] = battery_pct
        return DeviceStatus.model_validate(payload)

    def last_will(self, astronaut_id: str) -> aiomqtt.Will:
        """Last Will del dispositivo: DeviceStatus `offline` retained, QoS 1 (con la hora de la conexión)."""
        member = self._config.member(astronaut_id)
        status = self._offline_status(member, self._now())
        return aiomqtt.Will(topic=topics.status(member.astronaut_id, member.device_id), payload=_encode(dump(status)), qos=QOS, retain=True)

    # --- publicación ------------------------------------------------------------------------------

    async def _publish(self, client: MqttPublisher, envelope: Envelope) -> None:
        # Todo lo de MQTT_KINDS lleva topic (chunks, estado, anclaje, acks y calibraciones, estas retained);
        # el ground truth no llega aquí: `_pull_bus` lo descarta.
        assert envelope.topic is not None, f"{envelope.kind} is not published on the bus"
        await client.publish(envelope.topic, payload=_encode(envelope.payload), qos=QOS, retain=envelope.retained)

    async def _publish_chunk(self, client: MqttPublisher, unit: _Unit, envelope: Envelope) -> None:
        if envelope.payload.get("calibration_id") is not None:
            await self._ensure_calibration(client, unit, envelope)
        await self._publish(client, envelope)
        key = (int(envelope.payload["boot_id"]), str(envelope.payload["stream_id"]))
        unit.published[key] = max(unit.published.get(key, -1), int(envelope.payload["seq"]))

    async def _ensure_calibration(self, client: MqttPublisher, unit: _Unit, chunk: Envelope) -> None:
        """Deja retained en el bus la calibración que cita un chunk antes de publicarlo, sin que la retained de
        su stream vuelva nunca a una anterior."""
        calibration_id = str(chunk.payload["calibration_id"])
        stream_id = str(chunk.payload["stream_id"])
        day = self.mission_day(chunk.at) if chunk.day is None else chunk.day
        record = self._generator.calibration_in_force(unit.astronaut_id, stream_id, day)
        if record is None or record.calibration_id != calibration_id:
            # Chunk de un bloque sintetizado antes de un `calibrate`: cita la anterior. Solo se (re)publica si
            # en el bus no ha salido otra después para ese stream (p. ej. el calibrate aún no ha acabado).
            topic = topics.calibration(unit.astronaut_id, unit.device_id, stream_id)
            if unit.bus_calibrations.get(topic, calibration_id) != calibration_id:
                return
            record = self._generator.calibration(unit.astronaut_id, calibration_id, day)
        if record is None:
            if calibration_id not in unit.unresolved_calibrations:
                unit.unresolved_calibrations.add(calibration_id)
                _log.warning("%s: chunks reference unknown calibration %s; not published on the bus", unit.astronaut_id, calibration_id)
            return
        await self._publish_calibration(client, unit, calibration_envelope(unit.astronaut_id, unit.device_id, record, record.created_at))

    def _still_in_force(self, unit: _Unit, envelope: Envelope) -> bool:
        """La calibración del sobre sigue siendo la vigente de su stream (ningún `calibrate` posterior la sustituyó)."""
        payload = envelope.payload
        day = self.mission_day(envelope.at) if envelope.day is None else envelope.day
        record = self._generator.calibration_in_force(unit.astronaut_id, str(payload["stream_id"]), day)
        return record is not None and record.calibration_id == payload["calibration_id"]

    async def _publish_calibration(self, client: MqttPublisher, unit: _Unit, envelope: Envelope) -> None:
        """Publica una calibración (retained) salvo que ya sea la retained de su topic en esta conexión."""
        assert envelope.kind is EnvelopeKind.CALIBRATION and envelope.retained and envelope.topic is not None
        calibration_id = str(envelope.payload["calibration_id"])
        if unit.retained_calibrations.get(envelope.topic) == calibration_id:
            return
        await self._publish(client, envelope)
        unit.retained_calibrations[envelope.topic] = calibration_id
        unit.bus_calibrations[envelope.topic] = calibration_id

    def _envelope(self, kind: EnvelopeKind, model: Any, unit: _Unit, at: datetime, topic: str, *, retained: bool = False) -> Envelope:
        return Envelope.of(kind, model, at=at, astronaut_id=unit.astronaut_id, day=self.mission_day(at), topic=topic, retained=retained)

    def _published_seqs(self, unit: _Unit, at: datetime) -> dict[str, int]:
        """stream_id -> último seq publicado en el arranque en curso en `at` (para `last_seq`)."""
        epoch = unit.device.plan.epoch_at(at)
        if epoch is None:
            return {}
        return {stream_id: seq for (boot_id, stream_id), seq in unit.published.items() if boot_id == epoch.boot_id}

    async def _publish_status(self, client: MqttPublisher, unit: _Unit, now: datetime) -> None:
        # Lo comprometido en el dispositivo va por delante de lo publicado (lecturas de anticipo): el estado
        # declara solo lo que ya está en el bus.
        status = unit.device.status(unit.status_rng, now, published=self._published_seqs(unit, now))
        await self._publish(client, self._envelope(EnvelopeKind.DEVICE_STATUS, status, unit, now, topics.status(unit.astronaut_id, unit.device_id), retained=True))

    async def _publish_clock(self, client: MqttPublisher, unit: _Unit, now: datetime) -> None:
        sync = unit.device.clock_sync(unit.clock_rng, now)
        if sync is not None:  # reiniciando: no hay reloj que anclar
            await self._publish(client, self._envelope(EnvelopeKind.CLOCK_SYNC, sync, unit, now, topics.clock(unit.astronaut_id, unit.device_id), retained=True))

    def _ack(
        self,
        unit: _Unit,
        command_id: str,
        status: AckStatus,
        at: datetime,
        *,
        code: AckCode | None = None,
        message: str | None = None,
        calibration_id: str | None = None,
    ) -> Envelope:
        body: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION, "command_id": command_id, "astronaut_id": unit.astronaut_id,
            "device_id": unit.device_id, "timestamp": at, "status": status,
        }  # fmt: skip
        if code is not None:
            body["error"] = {"code": code} if message is None else {"code": code, "message": message[:_MESSAGE_MAX]}
        if calibration_id is not None:
            body["result"] = {"calibration_id": calibration_id}
        return self._envelope(EnvelopeKind.COMMAND_ACK, CommandAck.model_validate(body), unit, at, topics.ack(unit.astronaut_id, unit.device_id))

    async def _publish_ack(self, client: MqttPublisher, unit: _Unit, envelope: Envelope) -> None:
        """Publica un ack y lo anota (si su comando sigue en la LRU) para poder reenviarlo ante una reentrega."""
        await self._publish(client, envelope)
        sent = unit.handled.get(str(envelope.payload["command_id"]))
        if sent is not None:
            sent.append(envelope)

    async def _reply(self, client: MqttPublisher, unit: _Unit, command_id: str, status: AckStatus, at: datetime, **kw: Any) -> None:
        await self._publish_ack(client, unit, self._ack(unit, command_id, status, at, **kw))

    # --- fuentes de chunks ------------------------------------------------------------------------

    async def _drain(self, client: MqttPublisher, unit: _Unit, source: _Source, until: datetime) -> datetime:
        """Publica los chunks con `received_at` <= `until`; devuelve cuándo toca el siguiente."""
        while True:
            envelope = await source.peek(until)
            if envelope.at > until:
                return envelope.at
            await self._publish_chunk(client, unit, envelope)
            source.take()  # solo tras publicarse: si falla, se reintenta en la conexión siguiente

    async def _flush(self, client: MqttPublisher, unit: _Unit, source: _Source) -> datetime | None:
        """Publica lo que el dispositivo ya comprometió en `source` (ver docstring del módulo). Devuelve el
        último `received_at` publicado (None si nada)."""
        last: datetime | None = None
        pulls = 0
        # Una pérdida justo en el corte obliga a leer un chunk más de ese stream; el tope solo protege
        # de un bucle si algo no cuadra.
        max_pulls = 4 * len(source.streams)
        while True:
            await source.settle()
            if source.buffered:
                envelope = await source.peek()
                await self._publish_chunk(client, unit, envelope)
                source.take()
                last = envelope.at if last is None else max(last, envelope.at)
                continue
            boot = source.boot_id
            if boot is None:
                break
            if not any(source.device.next_seq(boot, s) - 1 > unit.published.get((boot, s), -1) for s in source.streams):
                break
            if pulls >= max_pulls:
                _log.warning("%s: flush of session %s stopped after %d chunks", unit.astronaut_id, source.session_id, pulls)
                break
            await source.peek()  # lee uno más (en un hilo)
            pulls += 1
        return last

    def _background_session_id(self, unit: _Unit, day: int) -> str:
        return derive_uuid(self._generator.seed, unit.astronaut_id, "live", "background", self._started_at.isoformat(), day)

    def _start_background(self, unit: _Unit, start: datetime, day: int) -> None:
        background = self._config.schedule.background
        if not background.enabled:
            return
        session_id = self._background_session_id(unit, day)
        iterator = self._generator.live_background(astronaut_id=unit.astronaut_id, day=day, device=unit.device, session_id=session_id, start=start)
        unit.background = _Source(
            iterator, device=unit.device, session_id=session_id, day=day, streams=tuple(background.streams), chunk=self._background_chunk
        )

    async def _stop_background(self, client: MqttPublisher, unit: _Unit, now: datetime) -> datetime:
        """Pausa el fondo; devuelve desde cuándo puede empezar la siguiente fuente."""
        source = unit.background
        if source is None:
            return now
        # Primero vaciar y luego soltar: si el broker se cae a medias, el fondo sigue y se vacía después.
        last = await self._flush(client, unit, source)
        unit.background = None
        return now if last is None else max(now, last)

    def _session_streams(self, command: StartSessionCommand) -> tuple[str, ...]:
        """Los streams que usará `live_session` (misma regla)."""
        if command.stream_ids:
            return tuple(command.stream_ids)
        protocol = self._config.protocols.get(command.protocol_id)
        if protocol is not None:
            return tuple(protocol.streams)
        return tuple(s.stream_id for s in self._config.device.streams)

    async def _end_session(self, client: MqttPublisher, unit: _Unit, now: datetime) -> None:
        """Acaba la sesión en curso: vacía, `completed` del start_session y reanuda el fondo."""
        session = unit.session
        assert session is not None
        # Vaciar antes de soltar la sesión (ver `_stop_background`); el `completed` al final: si no sale,
        # el estado ya es coherente (fondo en marcha) y solo se pierde ese ack.
        last = await self._flush(client, unit, session.source)
        unit.session = None
        self._start_background(unit, now if last is None else max(now, last), self.mission_day(now))
        await self._reply(client, unit, session.command_id, "completed", now)

    # --- comandos ---------------------------------------------------------------------------------

    async def handle(self, client: MqttClient, topic: str, payload: bytes) -> None:
        """Procesa un comando recibido en un topic `.../cmd`. Nunca lanza por un comando malo."""
        try:
            await self._handle(client, topic, payload)
        finally:
            self._wake.set()

    async def _handle(self, client: MqttPublisher, topic: str, payload: bytes) -> None:
        async with self._lock:
            unit = self._by_cmd_topic.get(topic)
            if unit is None:
                _log.warning("message on unexpected topic %s ignored", topic)
                return
            await self._settle(unit)
            oversized = len(payload) > _PAYLOAD_MAX_BYTES
            data: Any = None
            if not oversized:
                try:
                    data = json.loads(payload)
                except (ValueError, RecursionError):  # ValueError cubre UnicodeDecodeError y JSONDecodeError
                    data = None
            command_id = data.get("command_id") if isinstance(data, dict) else None
            if oversized:
                found = _COMMAND_ID_PREFIX.search(payload[:_PAYLOAD_MAX_BYTES])
                command_id = found.group(1).decode("ascii") if found else None
            if not isinstance(command_id, str) or not _UUID.fullmatch(command_id):
                _log.warning("unreadable command on %s ignored (no command_id)", topic)
                return
            now = self._now()
            previous = unit.handled.get(command_id)
            if previous:  # reentrega (DUP): mismos acks, sin volver a ejecutar
                unit.handled.move_to_end(command_id)
                _log.info("%s: command %s redelivered, resending its %d acks", unit.astronaut_id, command_id, len(previous))
                try:
                    for envelope in list(previous):
                        await self._publish(client, envelope)
                except Exception:  # noqa: BLE001
                    _log.exception("%s: could not resend the acks of %s", unit.astronaut_id, command_id)
                return
            unit.handled[command_id] = []
            while len(unit.handled) > _HANDLED_MAX:
                unit.handled.popitem(last=False)
            try:
                if oversized:
                    message = f"command payload of {len(payload)} bytes exceeds the {_PAYLOAD_MAX_BYTES} byte limit"
                    await self._reply(client, unit, command_id, "rejected", now, code="invalid_command", message=message)
                else:
                    await self._dispatch(client, unit, command_id, data, now)
            except Exception:  # noqa: BLE001  (un comando nunca tumba el gateway)
                _log.exception("%s: command %s failed", unit.astronaut_id, command_id)
                with contextlib.suppress(Exception):
                    await self._reply(client, unit, command_id, "failed", now, code="internal", message="internal gateway error")
            if not unit.handled.get(command_id, True):  # no llegó a salir ningún ack: una reentrega se ejecuta de nuevo
                del unit.handled[command_id]

    async def _dispatch(self, client: MqttPublisher, unit: _Unit, command_id: str, data: Any, now: datetime) -> None:
        problems = validation.errors("telemetry/command", data)
        if problems:
            await self._reply(client, unit, command_id, "rejected", now, code="invalid_command", message="; ".join(problems))
            return
        command = command_adapter.validate_python(data)
        if (command.astronaut_id, command.device_id) != (unit.astronaut_id, unit.device_id):
            message = f"command for {command.astronaut_id}/{command.device_id} received on the topic of {unit.astronaut_id}/{unit.device_id}"
            await self._reply(client, unit, command_id, "rejected", now, code="invalid_command", message=message)
            return
        # `not <= tope` también rechaza NaN/inf (json.loads acepta 1e999); se valida antes de tocar estado.
        duration = command.max_duration_s if isinstance(command, StartSessionCommand) else command.duration_s if isinstance(command, CalibrateCommand) else None
        if duration is not None and not duration <= _DURATION_MAX_S:
            message = f"duration exceeds the {_DURATION_MAX_S} s limit of this gateway"
            await self._reply(client, unit, command_id, "rejected", now, code="invalid_command", message=message)
            return
        match command:
            case StartSessionCommand():
                await self._start_session(client, unit, command, now)
            case StopSessionCommand():
                await self._stop_session(client, unit, command, now)
            case MarkEventCommand():
                await self._mark_event(client, unit, command, now)
            case CalibrateCommand():
                await self._calibrate(client, unit, command, now)

    def _unknown_streams(self, stream_ids: Sequence[str] | None) -> list[str]:
        return [s for s in stream_ids or () if s not in self._stream_ids]

    async def _start_session(self, client: MqttPublisher, unit: _Unit, command: StartSessionCommand, now: datetime) -> None:
        cid = command.command_id
        if unit.session is not None:
            await self._reply(client, unit, cid, "rejected", now, code="busy", message=f"session {unit.session.session_id} is already running on this device")
            return
        unknown = self._unknown_streams(command.stream_ids)
        if unknown:
            await self._reply(client, unit, cid, "rejected", now, code="unknown_stream", message=f"unknown streams: {', '.join(unknown)}")
            return
        if unit.device.plan.epoch_at(now) is None:
            await self._reply(client, unit, cid, "rejected", now, code="device_offline", message="the device is rebooting")
            return
        # `accepted` primero: si publicarlo falla no se ha tocado nada (ni sesión ni fondo) y el estado
        # sigue coherente con el `failed` que verá el cliente.
        await self._reply(client, unit, cid, "accepted", now)
        start = await self._stop_background(client, unit, now)
        iterator = self._generator.live_session(
            astronaut_id=unit.astronaut_id, day=self.mission_day(now), device=unit.device, session_id=command.session_id,
            session_type=cast(Literal["exercise", "spine_check"], command.session_type.value), protocol_id=command.protocol_id,
            stream_ids=list(command.stream_ids or ()), start=start,
        )  # fmt: skip
        source = _Source(
            iterator, device=unit.device, session_id=command.session_id, day=self.mission_day(now), streams=self._session_streams(command),
            chunk=self._session_chunk,
        )  # fmt: skip
        deadline = None if command.max_duration_s is None else start + timedelta(seconds=command.max_duration_s)
        unit.session = _Session(command.session_id, cid, source, deadline)
        try:
            await self._publish_clock(client, unit, now)
        except Exception:  # noqa: BLE001  (la sesión ya está aceptada y en marcha; el ClockSync periódico lo repone)
            _log.warning("%s: could not publish the ClockSync at session start", unit.astronaut_id, exc_info=True)

    async def _stop_session(self, client: MqttPublisher, unit: _Unit, command: StopSessionCommand, now: datetime) -> None:
        cid = command.command_id
        if unit.session is None or unit.session.session_id != command.session_id:
            await self._reply(client, unit, cid, "rejected", now, code="no_session", message=f"session {command.session_id} is not running")
            return
        await self._reply(client, unit, cid, "accepted", now)
        await self._end_session(client, unit, now)
        await self._reply(client, unit, cid, "completed", now)

    async def _mark_event(self, client: MqttPublisher, unit: _Unit, command: MarkEventCommand, now: datetime) -> None:
        cid = command.command_id
        if unit.session is None or unit.session.session_id != command.session_id:
            await self._reply(client, unit, cid, "rejected", now, code="no_session", message=f"session {command.session_id} is not running")
            return
        await self._reply(client, unit, cid, "accepted", now)
        await self._reply(client, unit, cid, "completed", now)

    async def _calibrate(self, client: MqttPublisher, unit: _Unit, command: CalibrateCommand, now: datetime) -> None:
        cid = command.command_id
        unknown = self._unknown_streams(command.stream_ids)
        if unknown:
            await self._reply(client, unit, cid, "rejected", now, code="unknown_stream", message=f"unknown streams: {', '.join(unknown)}")
            return
        if unit.device.plan.epoch_at(now) is None:
            await self._reply(client, unit, cid, "rejected", now, code="device_offline", message="the device is rebooting")
            return
        done = now if command.duration_s is None else now + timedelta(seconds=command.duration_s)
        try:
            calibrations = self._generator.live_calibration(
                astronaut_id=unit.astronaut_id, day=self.mission_day(now), session_id=command.session_id,
                calibration_type=cast(Literal["stretch", "emg_mvc", "imu"], command.calibration_type.value),
                stream_ids=list(command.stream_ids), at=done,
            )  # fmt: skip
        except ValueError as error:
            await self._reply(client, unit, cid, "rejected", now, code="invalid_command", message=str(error))
            return
        # El generador ya las da por vigentes (`calibration_in_force`): el siguiente bloque de la sesión o del
        # fondo las cita, aunque sea antes de `done`, y `_ensure_calibration` las publica antes de su primer chunk.
        await self._reply(client, unit, cid, "accepted", now)
        completed = self._ack(unit, cid, "completed", done, calibration_id=calibrations[0].payload["calibration_id"])
        # Las calibraciones (retained) antes del `completed`: quien lea el ack ya puede resolver su id.
        if done <= now:
            for calibration in calibrations:
                await self._publish_calibration(client, unit, calibration)
            await self._publish_ack(client, unit, completed)
        else:
            unit.pending += [(done, calibration) for calibration in calibrations]
            unit.pending.append((done, completed))

    # --- bucle ------------------------------------------------------------------------------------

    async def tick(self, client: MqttPublisher) -> datetime:
        """Publica todo lo que toca hasta ahora (chunks, acks diferidos, ClockSync, DeviceStatus) y
        devuelve el instante de lo siguiente."""
        async with self._lock:
            now = self._now()
            return min([await self._tick_unit(client, unit, now) for unit in self._units])

    async def _settle(self, unit: _Unit) -> None:
        """Recoge lecturas que una cancelación dejó en vuelo: después ningún hilo toca este dispositivo."""
        for source in unit.sources():
            await source.settle()

    async def _tick_unit(self, client: MqttPublisher, unit: _Unit, now: datetime) -> datetime:
        await self._settle(unit)
        due: list[datetime] = []
        # sorted es estable: calibraciones antes que su `completed`. Cada una sale de `pending` al publicarse.
        for item in sorted((p for p in unit.pending if p[0] <= now), key=lambda p: p[0]):
            envelope = item[1]
            if envelope.kind is EnvelopeKind.CALIBRATION:
                if self._still_in_force(unit, envelope):  # otro `calibrate` posterior del stream ya la sustituyó: no se publica
                    await self._publish_calibration(client, unit, envelope)
            else:
                await self._publish_ack(client, unit, envelope)
            unit.pending = [p for p in unit.pending if p is not item]

        session = unit.session
        if session is not None:
            until = now if session.deadline is None else min(now, session.deadline)
            due.append(await self._drain(client, unit, session.source, until))
            if session.deadline is not None:
                if now >= session.deadline:
                    await self._end_session(client, unit, now)
                    due.clear()
                else:
                    due.append(session.deadline)
        if unit.session is None:
            await self._idle_upkeep(client, unit, now)
            if unit.background is not None:
                due.append(await self._drain(client, unit, unit.background, now))

        if now >= unit.next_clock:
            await self._publish_clock(client, unit, now)
            unit.next_clock = _next_after(unit.next_clock, self._clock_every, now)
        if now >= unit.next_status:
            await self._publish_status(client, unit, now)
            unit.next_status = _next_after(unit.next_status, self._status_every, now)
        due += [unit.next_clock, unit.next_status, *(p[0] for p in unit.pending)]
        return min(due)

    async def _idle_upkeep(self, client: MqttPublisher, unit: _Unit, now: datetime) -> None:
        """Sin sesión: recarga el dispositivo si agotó su plan y mantiene el fondo en el día de misión actual."""
        start = now
        if now >= unit.device_until:
            start = await self._stop_background(client, unit, now)
            fresh = self._boot(unit.member, now)
            unit.device, unit.device_until = fresh.device, fresh.device_until
            unit.status_rng, unit.clock_rng = fresh.status_rng, fresh.clock_rng
            unit.next_status = unit.next_clock = now
            _log.info("%s: device recharged and rebooted (boot_id %d)", unit.astronaut_id, fresh.device.plan.boots[0].boot_id)
        day = self.mission_day(now)
        if unit.background is not None and unit.background.day != day:
            start = max(start, await self._stop_background(client, unit, now))
        if unit.background is None:
            self._start_background(unit, start, day)

    async def _listen(self, client: MqttClient) -> None:
        async for message in client.messages:
            await self.handle(client, str(message.topic), _as_bytes(message.payload))

    def _step(self) -> float:
        """Espera máxima del bucle (s): un chunk de la fuente activa más corta (la sesión de cada dispositivo
        o, sin ella, su fondo; sin ninguna, un chunk de sesión)."""
        chunks = [unit.session.source.chunk if unit.session is not None else unit.background.chunk if unit.background is not None else self._session_chunk for unit in self._units]
        return min(chunks).total_seconds()

    async def _wait(self, seconds: float) -> None:
        """Espera `seconds` con `sleep` (inyectable) o hasta que un comando despierte al bucle (`_wake`)."""
        if not self._wake.is_set():
            sleeper = asyncio.ensure_future(self._sleep(seconds))
            waker = asyncio.ensure_future(self._wake.wait())
            try:
                await asyncio.wait((sleeper, waker), return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (sleeper, waker):
                    task.cancel()
                await asyncio.gather(sleeper, waker, return_exceptions=True)
        self._wake.clear()

    async def _pump(self, client: MqttPublisher) -> None:
        # Como mucho un chunk de la fuente activa de espera, y un comando la corta: una sesión que empieza
        # mientras el fondo (chunks más largos) duerme publica enseguida.
        while True:
            due = await self.tick(client)
            await self._wait(min(self._step(), max(0.0, (due - self._now()).total_seconds())))

    async def _shutdown(self, client: MqttPublisher) -> None:
        """Parada limpia: DeviceStatus `offline` retained (el Last Will solo salta si la conexión se pierde)."""
        now = self._now()
        for unit in self._units:
            status = self._offline_status(unit.member, now, battery_pct=unit.device.battery_pct(now))
            try:
                await self._publish(client, self._envelope(EnvelopeKind.DEVICE_STATUS, status, unit, now, topics.status(unit.astronaut_id, unit.device_id), retained=True))
            except Exception:  # noqa: BLE001  (el broker puede haberse ido ya)
                _log.warning("%s: could not publish the offline status on shutdown", unit.astronaut_id, exc_info=True)

    async def _announce(self, client: MqttPublisher) -> None:
        """Conexión nueva (también tras reconectar): anclaje y estado antes que nada (el Last Will pudo dejar
        `offline`) y las calibraciones se vuelven a publicar antes de su siguiente chunk (el broker pudo
        perder sus retained). Sesión, fondo y seq siguen como estaban."""
        async with self._lock:
            now = self._now()
            for unit in self._units:
                await self._settle(unit)
                unit.retained_calibrations.clear()
                await self._publish_clock(client, unit, now)
                unit.next_clock = now + self._clock_every
                await self._publish_status(client, unit, now)
                unit.next_status = now + self._status_every

    async def run(self, client: MqttClient) -> None:
        """Una conexión: suscribe a `.../cmd`, publica estado inicial y atiende comandos y streaming.

        Lanza MqttError si la conexión se pierde (el gateway queda listo para otra `run` con un cliente
        nuevo, ver `_serve`). Si se cancela (SIGTERM) o falla por otra cosa, publica antes el `offline`.
        """
        try:
            for topic in self._by_cmd_topic:
                await client.subscribe(topic, qos=QOS)
            await self._announce(client)
            tasks = [asyncio.create_task(self._listen(client)), asyncio.create_task(self._pump(client))]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                # Las dos paradas antes de seguir: ni un chunk ni un estado `ok` después del `offline`. Si el
                # bombeo se corta a media lectura, `_Source.settle` la recoge en la conexión siguiente.
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            for task in done:
                task.result()
            raise aiomqtt.MqttError("the message stream ended")
        except aiomqtt.MqttError:
            raise  # conexión perdida: el Last Will ya avisa y no hay por dónde publicar
        except BaseException:
            await self._shutdown(client)
            raise


type ClientFactory = Callable[..., AbstractAsyncContextManager[MqttClient]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _serve(
    generator: MissionGenerator,
    settings: GatewaySettings,
    *,
    client_factory: ClientFactory,
    clock: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> None:
    """Una conexión MQTT por dispositivo (cada una con su Last Will) y un `FakeGateway` por dispositivo, que
    sobrevive a las reconexiones (`_connection`). `sleep` también marca las esperas del backoff."""
    config: MissionConfig = generator.config
    _log.info("gateway for %s on %s:%d (day %d, speed %s)", [m.astronaut_id for m in config.crew], settings.mqtt_host, settings.mqtt_port, settings.start_day, settings.speed)
    gateways = [(m, FakeGateway(generator, settings, clock=clock, sleep=sleep, astronaut_ids=[m.astronaut_id])) for m in config.crew]
    async with asyncio.TaskGroup() as group:
        for member, gateway in gateways:
            group.create_task(_connection(gateway, member, settings, client_factory=client_factory, sleep=sleep))


async def _connection(
    gateway: FakeGateway,
    member: CrewMember,
    settings: GatewaySettings,
    *,
    client_factory: ClientFactory,
    sleep: Callable[[float], Awaitable[object]],
) -> None:
    """Conecta (con el Last Will de esta conexión) y ejecuta `gateway.run`; si el broker rechaza la conexión
    o la corta (MqttError), espera con backoff exponencial (`mqtt_reconnect_*`, de vuelta a la inicial tras
    conectar) y lo intenta de nuevo sobre el mismo gateway. Solo acaba al cancelarse (o por otro error)."""
    identifier = f"{settings.mqtt_client_id}-{member.device_id}"
    delay = settings.mqtt_reconnect_initial_s
    while True:
        try:
            async with client_factory(
                hostname=settings.mqtt_host, port=settings.mqtt_port, identifier=identifier, will=gateway.last_will(member.astronaut_id)
            ) as client:
                _log.info("%s: connected to %s:%d as %s", member.astronaut_id, settings.mqtt_host, settings.mqtt_port, identifier)
                delay = settings.mqtt_reconnect_initial_s
                await gateway.run(client)
        except aiomqtt.MqttError as error:
            _log.warning("%s: MQTT connection refused or lost (%s); reconnecting in %.1f s", member.astronaut_id, error, delay)
        await sleep(delay)
        delay = min(delay * settings.mqtt_reconnect_factor, settings.mqtt_reconnect_max_s)


async def serve(settings: GatewaySettings) -> None:
    """Carga el YAML, conecta con aiomqtt (Last Will offline, reconexión con backoff) y ejecuta
    `FakeGateway.run`. SIGTERM (Docker) para limpiamente, como Ctrl+C, también mientras espera para reconectar."""
    generator = MissionGenerator(load_config(settings.config), settings.seed)
    task = asyncio.create_task(_serve(generator, settings, client_factory=aiomqtt.Client, clock=_utc_now))
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError, RuntimeError, AttributeError):  # Windows no tiene SIGTERM en el bucle
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        _log.info("gateway stopped (SIGTERM)")
    finally:
        with contextlib.suppress(NotImplementedError, RuntimeError, AttributeError):
            loop.remove_signal_handler(signal.SIGTERM)
