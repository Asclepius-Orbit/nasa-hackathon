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
    pasados `duration_s` (o al momento). El contrato solo admite un calibration_id por ack: con varios
    streams va el del primero (todas pasan a estar vigentes igualmente).
  - Comando inválido: `rejected`/`invalid_command` si se puede leer su command_id; si no, se ignora.
    Un error inesperado al procesarlo da `failed`/`internal`; el gateway nunca se cae por un comando.
- DeviceStatus retained en `topics.status(...)` al arrancar y cada `device.status_interval_s`; ClockSync
  retained en `topics.clock(...)` al arrancar, al empezar cada sesión y cada `device.clock_sync_interval_s`.
  Last Will retained con `status: offline` (`last_will`) y el mismo `offline` al pararse limpiamente.
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
(inyectable): los tests corren en tiempo virtual y con un cliente MQTT falso (Protocol `MqttClient`).

Detalle del vaciado: leer el siguiente chunk de un iterador en vivo compromete en el dispositivo el seq
de ese chunk en todos los streams. Al pausar o parar, se publican los chunks ya comprometidos (pueden
llevar `received_at` unos ms en el futuro) y la siguiente fuente empieza tras el último, así el seq de
cada stream sigue sin huecos y los tramos no se solapan.
"""

import asyncio
import contextlib
import json
import logging
import math
import re
import signal
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

from spine_physiology.config import CrewMember, MissionConfig, load_config
from spine_physiology.device import SimDevice, plan_device_live
from spine_physiology.envelope import Envelope, EnvelopeKind
from spine_physiology.gateway.settings import GatewaySettings
from spine_physiology.generator import MissionGenerator
from spine_physiology.rng import derive_uuid
from spine_physiology.rng import generator as rng_generator
from spine_physiology.sinks.mqtt import QOS, MqttPublisher

_log = logging.getLogger(__name__)

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DAY_S = 86400.0
_MESSAGE_MAX = 300  # maxLength de error.message en command-ack.schema.json

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


class _Source:
    """Iterador en vivo (sesión o fondo) con un envelope de anticipo; descarta lo que no va al bus."""

    def __init__(self, iterator: Iterator[Envelope], *, device: SimDevice, session_id: str, day: int, streams: tuple[str, ...]) -> None:
        self._iterator = iterator
        self._ahead: Envelope | None = None
        self.device = device
        self.session_id = session_id
        self.day = day
        self.streams = streams
        self.boot_id: int | None = None  # arranque del último chunk leído

    @property
    def has_ahead(self) -> bool:
        return self._ahead is not None

    def peek(self) -> Envelope:
        while self._ahead is None:
            envelope = next(self._iterator)
            if envelope.topic is None:  # ground truth: solo JSONL, nunca al bus
                continue
            self._ahead = envelope
            self.boot_id = int(envelope.payload["boot_id"])
        return self._ahead

    def take(self) -> Envelope:
        envelope = self.peek()
        self._ahead = None
        return envelope


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
    # Acks que salen más tarde (completed de una calibración con duración).
    pending: list[tuple[datetime, Envelope]] = field(default_factory=list)

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
        self._chunk = timedelta(milliseconds=config.signals.chunk_ms)
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
        assert envelope.topic is not None
        await client.publish(envelope.topic, payload=_encode(envelope.payload), qos=QOS, retain=envelope.retained)

    async def _publish_chunk(self, client: MqttPublisher, unit: _Unit, envelope: Envelope) -> None:
        await self._publish(client, envelope)
        key = (int(envelope.payload["boot_id"]), str(envelope.payload["stream_id"]))
        unit.published[key] = max(unit.published.get(key, -1), int(envelope.payload["seq"]))

    def _envelope(self, kind: EnvelopeKind, model: Any, unit: _Unit, at: datetime, topic: str, *, retained: bool = False) -> Envelope:
        return Envelope.of(kind, model, at=at, astronaut_id=unit.astronaut_id, day=self.mission_day(at), topic=topic, retained=retained)

    async def _publish_status(self, client: MqttPublisher, unit: _Unit, now: datetime) -> None:
        status = unit.device.status(unit.status_rng, now)
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

    async def _reply(self, client: MqttPublisher, unit: _Unit, command_id: str, status: AckStatus, at: datetime, **kw: Any) -> None:
        await self._publish(client, self._ack(unit, command_id, status, at, **kw))

    # --- fuentes de chunks ------------------------------------------------------------------------

    async def _drain(self, client: MqttPublisher, unit: _Unit, source: _Source, until: datetime) -> datetime:
        """Publica los chunks con `received_at` <= `until`; devuelve cuándo toca el siguiente."""
        while True:
            envelope = source.peek()
            if envelope.at > until:
                return envelope.at
            source.take()
            await self._publish_chunk(client, unit, envelope)

    async def _flush(self, client: MqttPublisher, unit: _Unit, source: _Source) -> datetime | None:
        """Publica lo que el dispositivo ya comprometió en `source` (ver docstring del módulo). Devuelve el
        último `received_at` publicado (None si nada)."""
        last: datetime | None = None
        pulls = 0
        # Una pérdida justo en el corte obliga a leer un chunk más de ese stream; el tope solo protege
        # de un bucle si algo no cuadra.
        max_pulls = 4 * len(source.streams)
        while True:
            if source.has_ahead:
                envelope = source.take()
                await self._publish_chunk(client, unit, envelope)
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
            source.peek()
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
        unit.background = _Source(iterator, device=unit.device, session_id=session_id, day=day, streams=tuple(background.streams))

    async def _stop_background(self, client: MqttPublisher, unit: _Unit, now: datetime) -> datetime:
        """Pausa el fondo; devuelve desde cuándo puede empezar la siguiente fuente."""
        source, unit.background = unit.background, None
        if source is None:
            return now
        last = await self._flush(client, unit, source)
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
        unit.session = None
        last = await self._flush(client, unit, session.source)
        await self._reply(client, unit, session.command_id, "completed", now)
        self._start_background(unit, now if last is None else max(now, last), self.mission_day(now))

    # --- comandos ---------------------------------------------------------------------------------

    async def handle(self, client: MqttClient, topic: str, payload: bytes) -> None:
        """Procesa un comando recibido en un topic `.../cmd`. Nunca lanza por un comando malo."""
        async with self._lock:
            unit = self._by_cmd_topic.get(topic)
            if unit is None:
                _log.warning("message on unexpected topic %s ignored", topic)
                return
            try:
                data = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = None
            command_id = data.get("command_id") if isinstance(data, dict) else None
            if not isinstance(command_id, str) or not _UUID.fullmatch(command_id):
                _log.warning("unreadable command on %s ignored (no command_id)", topic)
                return
            now = self._now()
            try:
                await self._dispatch(client, unit, command_id, data, now)
            except Exception:  # noqa: BLE001  (un comando nunca tumba el gateway)
                _log.exception("%s: command %s failed", unit.astronaut_id, command_id)
                with contextlib.suppress(Exception):
                    await self._reply(client, unit, command_id, "failed", now, code="internal", message="internal gateway error")

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
        start = await self._stop_background(client, unit, now)
        iterator = self._generator.live_session(
            astronaut_id=unit.astronaut_id, day=self.mission_day(now), device=unit.device, session_id=command.session_id,
            session_type=cast(Literal["exercise", "spine_check"], command.session_type.value), protocol_id=command.protocol_id,
            stream_ids=list(command.stream_ids or ()), start=start,
        )  # fmt: skip
        source = _Source(iterator, device=unit.device, session_id=command.session_id, day=self.mission_day(now), streams=self._session_streams(command))
        deadline = None if command.max_duration_s is None else start + timedelta(seconds=command.max_duration_s)
        unit.session = _Session(command.session_id, cid, source, deadline)
        await self._reply(client, unit, cid, "accepted", now)
        await self._publish_clock(client, unit, now)

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
        await self._reply(client, unit, cid, "accepted", now)
        completed = self._ack(unit, cid, "completed", done, calibration_id=calibrations[0].payload["calibration_id"])
        if done <= now:
            await self._publish(client, completed)
        else:
            unit.pending.append((done, completed))

    # --- bucle ------------------------------------------------------------------------------------

    async def tick(self, client: MqttPublisher) -> datetime:
        """Publica todo lo que toca hasta ahora (chunks, acks diferidos, ClockSync, DeviceStatus) y
        devuelve el instante de lo siguiente."""
        async with self._lock:
            now = self._now()
            return min([await self._tick_unit(client, unit, now) for unit in self._units])

    async def _tick_unit(self, client: MqttPublisher, unit: _Unit, now: datetime) -> datetime:
        due: list[datetime] = []
        ready = sorted((p for p in unit.pending if p[0] <= now), key=lambda p: p[0])
        unit.pending = [p for p in unit.pending if p[0] > now]
        for _, envelope in ready:
            await self._publish(client, envelope)

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

    async def _pump(self, client: MqttPublisher) -> None:
        # Como mucho un chunk de espera: un comando que llega mientras duerme se nota enseguida.
        step = self._chunk.total_seconds()
        while True:
            due = await self.tick(client)
            await self._sleep(min(step, max(0.0, (due - self._now()).total_seconds())))

    async def _shutdown(self, client: MqttPublisher) -> None:
        """Parada limpia: DeviceStatus `offline` retained (el Last Will solo salta si la conexión se pierde)."""
        now = self._now()
        for unit in self._units:
            status = self._offline_status(unit.member, now, battery_pct=unit.device.battery_pct(now))
            try:
                await self._publish(client, self._envelope(EnvelopeKind.DEVICE_STATUS, status, unit, now, topics.status(unit.astronaut_id, unit.device_id), retained=True))
            except Exception:  # noqa: BLE001  (el broker puede haberse ido ya)
                _log.warning("%s: could not publish the offline status on shutdown", unit.astronaut_id, exc_info=True)

    async def run(self, client: MqttClient) -> None:
        """Suscribe, publica estado inicial y atiende comandos y streaming hasta que se cancele."""
        for topic in self._by_cmd_topic:
            await client.subscribe(topic, qos=QOS)
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._listen(client))
                group.create_task(self._pump(client))
        finally:
            await self._shutdown(client)


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
    """Una conexión MQTT por dispositivo (cada una con su Last Will) y un `FakeGateway` por conexión."""
    config: MissionConfig = generator.config
    gateways = [(m, FakeGateway(generator, settings, clock=clock, sleep=sleep, astronaut_ids=[m.astronaut_id])) for m in config.crew]
    async with contextlib.AsyncExitStack() as stack:
        clients: list[MqttClient] = []
        for member, gateway in gateways:
            client = client_factory(
                hostname=settings.mqtt_host, port=settings.mqtt_port, identifier=f"{settings.mqtt_client_id}-{member.device_id}",
                will=gateway.last_will(member.astronaut_id),
            )  # fmt: skip
            clients.append(await stack.enter_async_context(client))
        _log.info("gateway up on %s:%d for %s (day %d, speed %s)", settings.mqtt_host, settings.mqtt_port, [m.astronaut_id for m in config.crew], settings.start_day, settings.speed)
        async with asyncio.TaskGroup() as group:
            for (_, gateway), client in zip(gateways, clients, strict=True):
                group.create_task(gateway.run(client))


async def serve(settings: GatewaySettings) -> None:
    """Carga el YAML, conecta con aiomqtt (Last Will offline) y ejecuta `FakeGateway.run`. SIGTERM (Docker)
    para limpiamente, como Ctrl+C."""
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
