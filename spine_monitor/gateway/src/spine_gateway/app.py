"""Bucle MQTT del gateway real: Arduino del wearable por USB serie -> topics MQTT v1.

Un dispositivo por proceso (``astronaut_id``/``device_id`` de los ajustes) y una conexión MQTT (aiomqtt) con Last Will
``offline`` retained en ``topics.status``. Tres tareas en un ``TaskGroup`` (``Gateway.run``):

- Consumidor serie (``LineSource.events``): ``LinkUp``/``LinkDown`` marcan el enlace; cada ``SerialLine`` se interpreta con
  ``protocol.parse_line``. Una ``BadLine`` se cuenta por razón y se descarta. ``Hello``/``Tick`` dan el arranque
  (``boot_id``): uno nuevo reinicia seq y ``last_seq`` y pide un ancla de reloj; el ``Tick`` es el ancla
  (``gateway_receive_time``). Cada ``Frame`` va al ensamblador: chunk de sesión si hay sesión y su stream participa; si
  no, fondo (IMU y cordón; el EMG sin sesión se descarta). Hasta el primer hello/tick tras abrir el puerto no se publica
  nada del dispositivo (el Uno se reinicia al abrirlo).
- Temporizador: DeviceStatus cada ``status_interval_s``, ClockSync cada ``clock_sync_interval_s`` y la detección de
  streams parados (``stall_timeout_s``). El estado sale además en cuanto cambia (ver ``status``).
- Conexión MQTT con reconexión y backoff exponencial (``mqtt_reconnect_*``). Tras cada conexión se anuncian ancla y
  estado antes que nada y las calibraciones se vuelven a publicar antes del siguiente chunk que las cite.

Todo el estado lo tocan las tareas de una en una (``_lock``). Un error al procesar una línea o un comando se registra y
el bucle sigue. Cancelar ``run`` (SIGTERM) publica el ``offline`` retained y cierra la fuente y la conexión. Reloj y
esperas son inyectables (``clock``, ``sleep``): ninguna lógica lee el reloj de pared.
"""

import asyncio
import contextlib
import json
import logging
import signal
import uuid
from collections import Counter, OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import aiomqtt
from spine_contracts import (
    CalibrateCommand,
    Command,
    EmgMvcCalibration,
    MarkEventCommand,
    StartSessionCommand,
    StopSessionCommand,
    StretchCalibration,
    dump,
    topics,
)
from spine_contracts.models.telemetry.command_schema import CalibrationType

from spine_gateway import commands, status
from spine_gateway.assembler import BACKGROUND_SAMPLE_RATE_HZ, SAMPLE_RATE_HZ, ChunkAssembler, Discarded, DiscardReason, Pending
from spine_gateway.calibration import CalibrationFailure, CalibrationRecorder, CalibrationStatus
from spine_gateway.commands import AckCode, AckStatus
from spine_gateway.ports import LineSource, LinkDown, LinkUp, SerialEvent, SerialLine
from spine_gateway.protocol import BadLine, BadLineReason, Frame, FrameKind, Hello, Tick, parse_line
from spine_gateway.settings import GatewaySettings
from spine_gateway.status import Health, StreamReport, StreamState

_log = logging.getLogger(__name__)

QOS = 1
"""QoS de todos los mensajes (contrato AsyncAPI)."""

_STREAM_KIND = {FrameKind.EMG: "emg", FrameKind.IMU: "imu", FrameKind.CORD: "stretch"}
_HANDLED_MAX = 256
"""Comandos ya tratados que se recuerdan (LRU): una reentrega QoS 1 (DUP) recibe los mismos acks sin volver a ejecutarse."""


CALIBRATION_GRACE_S = 5.0
"""Margen sobre ``duration_s`` tras el que una calibración sin datos suficientes falla (``calibration_timeout``)."""
_CALIBRATED_KIND = {CalibrationType.STRETCH: FrameKind.CORD, CalibrationType.EMG_MVC: FrameKind.EMG}


@dataclass(slots=True)
class _Session:
    session_id: str
    command_id: str  # el del start_session, para su `completed`
    kinds: frozenset[FrameKind]
    timer: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _Calibration:
    command_id: str
    recorders: list[tuple[str, CalibrationRecorder]]  # (stream_id, grabación), en el orden del comando
    timer: asyncio.Task[None] | None = None


class MqttPublisher(Protocol):
    # `def` + Awaitable (no `async def`): aiomqtt devuelve Coroutine, que un `async def` de Protocol no admite.
    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> Awaitable[None]: ...


class IncomingMessage(Protocol):
    """Lo que usa el gateway de ``aiomqtt.Message``."""

    @property
    def topic(self) -> object: ...  # aiomqtt.Topic; str(topic) da el topic

    @property
    def payload(self) -> bytes | bytearray | str | int | float | None: ...


class MqttClient(MqttPublisher, Protocol):
    """Lo que usa el gateway de ``aiomqtt.Client``."""

    def subscribe(self, topic: str, qos: int) -> Awaitable[object]: ...

    @property
    def messages(self) -> AsyncIterator[IncomingMessage]: ...


type ClientFactory = Callable[..., AbstractAsyncContextManager[MqttClient]]
type Clock = Callable[[], datetime]
type Sleep = Callable[[float], Awaitable[object]]


def _relevant(signature: tuple[Any, ...] | None) -> tuple[Any, ...] | None:
    """La parte de la firma de un estado que se publica en cuanto cambia: todo salvo activo/sin datos aún."""
    if signature is None or len(signature) == 1:
        return signature
    health, boot_id, streams = signature
    return health, boot_id, tuple((stream_id, state is StreamState.STALLED, rate) for stream_id, state, rate in streams)


async def _guarded(coro: Coroutine[Any, Any, None]) -> None:
    try:
        await coro
    except Exception:  # noqa: BLE001
        _log.exception("gateway timer task failed")


def _as_bytes(payload: bytes | bytearray | str | float | None) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, bytes | bytearray):
        return bytes(payload)
    return str(payload).encode("utf-8")


def new_uuid() -> str:
    return str(uuid.uuid4())


def encode(payload: dict[str, Any]) -> bytes:
    """JSON compacto en UTF-8; NaN/inf no son JSON y fallan aquí en vez de llegar al bus."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


class Gateway:
    """Estado de un dispositivo y las tareas que lo publican. Sobrevive a las reconexiones MQTT y a los cortes del enlace."""

    def __init__(self, settings: GatewaySettings, *, clock: Clock, sleep: Sleep = asyncio.sleep, new_id: Callable[[], str] = new_uuid) -> None:
        """``clock`` da el instante UTC (aware); ``sleep`` espera esos segundos; ``new_id`` da el id de cada calibración."""
        self._settings = settings
        self._clock = clock
        self._sleep = sleep
        self._new_id = new_id
        self._assembler = ChunkAssembler(settings)
        self._stream_ids = {FrameKind.EMG: settings.emg_stream_id, FrameKind.IMU: settings.imu_stream_id, FrameKind.CORD: settings.stretch_stream_id}
        self._kinds = {stream_id: kind for kind, stream_id in self._stream_ids.items()}
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._lost = asyncio.Event()
        self._client: MqttPublisher | None = None
        # Arranque en curso. `_device_known`: el enlace está arriba y el dispositivo ha hablado (hello o tick) desde que se abrió.
        self._device_known = False
        self._boot_since: datetime | None = None
        self._firmware: str | None = None
        self._uptime_ms = 0
        self._last_tick: tuple[int, datetime] | None = None  # (millis, llegada) del último tick del arranque en curso
        self._clock_pending = False
        self._last_frame_at: dict[FrameKind, datetime] = {}
        self._published_seq: dict[FrameKind, int] = {}
        # Sesión y comandos.
        self._session: _Session | None = None
        self._calibration: _Calibration | None = None
        # Calibraciones vigentes por stream y la que está retained en el broker por esta conexión (se vacía al reconectar).
        self._calibrations: dict[str, StretchCalibration | EmgMvcCalibration] = {}
        self._stretch_calibration: StretchCalibration | None = None
        self._retained_calibrations: dict[str, str] = {}
        self._handled: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()  # command_id -> acks ya enviados
        self._group: asyncio.TaskGroup | None = None
        # Calendario del temporizador.
        now = self._now()
        self._next_status = now
        self._next_clock = now
        self._signature: tuple[Any, ...] | None = None
        self.bad_lines: Counter[BadLineReason] = Counter()
        self.discarded: Counter[DiscardReason] = Counter()

    # --- tiempo ---

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError(f"clock must return an aware UTC datetime, got {now!r}")
        return now

    # --- publicación ---

    def last_will(self) -> aiomqtt.Will:
        """DeviceStatus ``offline`` retained, QoS 1 (con la hora de la conexión)."""
        payload = status.offline_status(self._settings, at=self._now())
        return aiomqtt.Will(topic=self._topic_status(), payload=encode(payload), qos=QOS, retain=True)

    def _topic_status(self) -> str:
        return topics.status(self._settings.astronaut_id, self._settings.device_id)

    async def _publish(self, topic: str, payload: dict[str, Any], *, retain: bool) -> bool:
        """Publica por la conexión en curso; ``False`` si no hay o se ha perdido (la conexión se rehace aparte)."""
        client = self._client
        if client is None:
            return False
        try:
            await client.publish(topic, encode(payload), qos=QOS, retain=retain)
        except aiomqtt.MqttError as error:
            _log.warning("MQTT publish on %s failed (%s); reconnecting", topic, error)
            self._client = None
            self._lost.set()
            return False
        return True

    async def _publish_clock(self) -> None:
        """Ancla del último tick del arranque en curso; si aún no hay ninguno, queda pendiente para el primero."""
        boot_id = self._assembler.boot_id
        if self._last_tick is None or boot_id is None:
            self._clock_pending = True
            return
        millis, at = self._last_tick
        self._clock_pending = False
        s = self._settings
        await self._publish(topics.clock(s.astronaut_id, s.device_id), status.clock_sync(s, boot_id=boot_id, device_ms=millis, utc=at), retain=True)

    # --- estado ---

    def _in_session(self, kind: FrameKind) -> bool:
        return self._session is not None and kind in self._session.kinds

    def _rate(self, kind: FrameKind) -> float | None:
        """Frecuencia a la que se publica el stream: la nativa en sesión, 10 Hz de fondo y nada (EMG) fuera de sesión."""
        if self._in_session(kind):
            return SAMPLE_RATE_HZ[kind]
        return None if kind is FrameKind.EMG else BACKGROUND_SAMPLE_RATE_HZ

    def _timing(self, kind: FrameKind, now: datetime) -> dict[str, Any]:
        """Argumentos de ``status.stream_state``/``stall_deadline`` para un stream en ``now``."""
        return {
            "expected": self._rate(kind) is not None,
            "last_frame_at": self._last_frame_at.get(kind),
            "since": self._boot_since or now,
            "now": now,
            "stall_timeout_s": self._settings.stall_timeout_s,
        }

    def _reports(self, now: datetime) -> list[StreamReport]:
        return [
            StreamReport(stream_id, _STREAM_KIND[kind], status.stream_state(**self._timing(kind, now)), self._rate(kind), self._published_seq.get(kind))
            for kind, stream_id in self._stream_ids.items()
        ]

    def _status(self, now: datetime) -> tuple[dict[str, Any], tuple[Any, ...]]:
        """El DeviceStatus de ``now`` y su firma (lo que, si cambia, merece publicarse ya)."""
        reports = self._reports(now)
        health = status.health(device_known=self._device_known, states=[r.state for r in reports])
        boot_id = self._assembler.boot_id
        if health is Health.OFFLINE or boot_id is None:
            return status.offline_status(self._settings, at=now), (Health.OFFLINE,)
        payload = status.device_status(
            self._settings, at=now, health=health, boot_id=boot_id, uptime_ms=self._uptime_ms, firmware_version=self._firmware, streams=reports
        )
        return payload, (health, boot_id, tuple((r.stream_id, r.state, r.sample_rate_hz) for r in reports))

    async def _publish_status(self, now: datetime) -> None:
        payload, self._signature = self._status(now)
        await self._publish(self._topic_status(), payload, retain=True)

    async def _status_if_changed(self, now: datetime, *, any_change: bool) -> None:
        """Publica el estado si cambió. Con ``any_change=False`` (cada línea serie) solo cuentan los cambios relevantes:
        estado global, arranque, streams que se paran o vuelven y frecuencias; el resto (p. ej. el primer lote de un stream
        tras arrancar) lo publica el temporizador, que despierta como mucho ``stall_timeout_s`` después."""
        payload, signature = self._status(now)
        changed = signature != self._signature if any_change else _relevant(signature) != _relevant(self._signature)
        if changed:
            self._signature = signature
            self._wake.set()  # el temporizador recalcula cuándo puede pararse un stream
            await self._publish(self._topic_status(), payload, retain=True)

    def _stall_deadlines(self, now: datetime) -> list[datetime]:
        if not self._device_known:
            return []
        deadlines = (status.stall_deadline(**self._timing(kind, now)) for kind in self._stream_ids)
        return [d for d in deadlines if d is not None]

    # --- enlace serie ---

    async def _on_event(self, event: SerialEvent) -> None:
        match event:
            case LinkUp():
                _log.info("serial link up")
                self._forget_boot()
            case LinkDown(reason=reason):
                _log.warning("serial link down: %s", reason)
                self._forget_boot()
            case SerialLine(data=data, received_at=at):
                await self._on_line(data, at)

    def _forget_boot(self) -> None:
        """Hasta el siguiente hello/tick no se sabe qué arranque habla (abrir el puerto reinicia el Uno)."""
        self._device_known = False
        self._last_tick = None
        self._last_frame_at.clear()

    async def _on_line(self, data: bytes, at: datetime) -> None:
        message = parse_line(data)
        match message:
            case BadLine(reason=reason):
                self._count_bad_line(reason, message.raw)
            case Hello(boot_id=boot_id, millis=millis, firmware_version=firmware):
                self._on_boot(boot_id, millis, at, firmware=firmware)
            case Tick(boot_id=boot_id, millis=millis):
                self._on_boot(boot_id, millis, at)
                self._last_tick = (millis, at)
                if self._clock_pending:
                    await self._publish_clock()
            case Frame():
                await self._on_frame(message, at)

    def _count_bad_line(self, reason: BadLineReason, raw: str) -> None:
        self.bad_lines[reason] += 1
        count = self.bad_lines[reason]
        # Aviso en 1, 2, 4, 8...: un puerto con ruido no inunda el log.
        if count & (count - 1) == 0:
            _log.warning("bad serial line (%s, %d so far): %.80s", reason.value, count, raw)
        else:
            _log.debug("bad serial line (%s): %.80s", reason.value, raw)

    def _on_boot(self, boot_id: int, millis: int, at: datetime, *, firmware: str | None = None) -> None:
        new = self._assembler.on_new_boot(boot_id)
        if new:
            _log.info("device boot %d", boot_id)
            self._published_seq.clear()
            self._last_frame_at.clear()
            self._last_tick = None
            self._firmware = None
            self._clock_pending = True
        first = not self._device_known
        self._device_known = True
        if new or first:
            self._boot_since = at
        if firmware is not None:
            self._firmware = firmware
        self._uptime_ms = millis
        # El estado (offline -> ok, o boot_id nuevo) lo publica `_status_if_changed` al acabar la línea.

    async def _on_frame(self, frame: Frame, at: datetime) -> None:
        if not self._device_known:
            self.discarded[DiscardReason.NO_BOOT] += 1
            return
        self._last_frame_at[frame.kind] = at
        await self._feed_calibration(frame, at)
        session = self._session
        cord = self._stretch_calibration
        if session is not None and frame.kind in session.kinds:
            result = self._assembler.session_chunk(frame, session_id=session.session_id, received_at=at, stretch_calibration=cord)
        else:
            background_id = status.background_session_id(self._settings, at)
            result = self._assembler.background_chunk(frame, session_id=background_id, received_at=at, stretch_calibration=cord)
        match result:
            case Discarded(reason=reason):
                self.discarded[reason] += 1
                _log.debug("frame %s/%d discarded: %s", frame.kind.name, frame.frame_seq, reason.value)
            case Pending():
                pass
            case chunk:
                payload = dump(chunk)
                stream_id = payload["stream_id"]
                if "calibration_id" in payload:
                    await self._ensure_calibration(stream_id)
                if await self._publish(topics.stream(self._settings.astronaut_id, self._settings.device_id, stream_id), payload, retain=False):
                    kind = self._kinds[stream_id]
                    self._published_seq[kind] = max(self._published_seq.get(kind, -1), payload["seq"])

    async def _feed_calibration(self, frame: Frame, at: datetime) -> None:
        calibration = self._calibration
        if calibration is None:
            return
        progress = [recorder.feed(frame) for _, recorder in calibration.recorders]  # todas, sin cortocircuito
        if all(p is CalibrationStatus.DONE for p in progress):
            await self._finish_calibration(calibration, at)

    async def _finish_calibration(self, calibration: _Calibration, now: datetime) -> None:
        """Calibraciones retained (quien lea el ack ya puede resolver su id) y luego el ``completed``; o el ``failed``."""
        self._calibration = None
        if calibration.timer is not None:
            calibration.timer.cancel()
        results = [(stream_id, recorder.finish(now)) for stream_id, recorder in calibration.recorders]
        for _, result in results:
            if isinstance(result, CalibrationFailure):
                message = f"{result.reason.value}: {result.detail}"
                await self._reply(calibration.command_id, AckStatus.FAILED, now, code=AckCode.CALIBRATION_FAILED, message=message)
                return
        done = [(stream_id, result) for stream_id, result in results if not isinstance(result, CalibrationFailure)]
        for stream_id, result in done:
            self._calibrations[stream_id] = result
            if isinstance(result, StretchCalibration):
                self._stretch_calibration = result
            await self._ensure_calibration(stream_id)
        # El contrato admite un calibration_id por ack: el del primer stream (todas quedan vigentes igualmente).
        await self._reply(calibration.command_id, AckStatus.COMPLETED, now, calibration_id=done[0][1].calibration_id)

    async def _ensure_calibration(self, stream_id: str) -> None:
        """Deja retained la calibración vigente de ``stream_id`` si esta conexión aún no la ha publicado."""
        calibration = self._calibrations[stream_id]
        if self._retained_calibrations.get(stream_id) == calibration.calibration_id:
            return
        topic = topics.calibration(self._settings.astronaut_id, self._settings.device_id, stream_id)
        if await self._publish(topic, dump(calibration), retain=True):
            self._retained_calibrations[stream_id] = calibration.calibration_id

    async def _consume(self, source: LineSource) -> None:
        events = source.events()
        try:
            async for event in events:
                try:
                    async with self._lock:
                        await self._on_event(event)
                        await self._status_if_changed(self._now(), any_change=False)
                except Exception:  # noqa: BLE001  (una línea nunca tumba el gateway)
                    _log.exception("serial event %r failed", event)
        finally:
            close = getattr(events, "aclose", None)
            if close is not None:
                await close()

    # --- temporizador ---

    async def _tick(self) -> float:
        """Publica lo que toca (ancla, estado periódico o por cambio) y devuelve los segundos hasta lo siguiente."""
        now = self._now()
        s = self._settings
        if now >= self._next_clock:
            await self._publish_clock()
            self._next_clock = status.next_after(self._next_clock, timedelta(seconds=s.clock_sync_interval_s), now)
        if now >= self._next_status:
            await self._publish_status(now)
            self._next_status = status.next_after(self._next_status, timedelta(seconds=s.status_interval_s), now)
        else:
            await self._status_if_changed(now, any_change=True)
        due = min([self._next_clock, self._next_status, *self._stall_deadlines(now)])
        return max(0.0, (due - now).total_seconds())

    async def _timers(self) -> None:
        while True:
            try:
                async with self._lock:
                    self._wake.clear()
                    delay = await self._tick()
            except Exception:  # noqa: BLE001
                _log.exception("gateway timer failed")
                delay = self._settings.stall_timeout_s
            await self._wait(delay)

    async def _wait(self, seconds: float) -> None:
        """Espera ``seconds`` con ``sleep`` (inyectable) o hasta que algo despierte al temporizador (``_wake``)."""
        if self._wake.is_set():
            return
        sleeper = asyncio.ensure_future(self._sleep(seconds))
        waker = asyncio.ensure_future(self._wake.wait())
        try:
            await asyncio.wait((sleeper, waker), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleeper, waker):
                task.cancel()
            await asyncio.gather(sleeper, waker, return_exceptions=True)

    # --- comandos ---

    async def handle(self, payload: bytes) -> None:
        """Procesa un comando recibido en ``topics.cmd``. Nunca lanza por un comando malo."""
        async with self._lock:
            incoming = commands.read_command(payload, astronaut_id=self._settings.astronaut_id, device_id=self._settings.device_id)
            if incoming is None:
                _log.warning("unreadable command ignored (no command_id)")
                return
            command_id = incoming.command_id
            now = self._now()
            previous = self._handled.get(command_id)
            # Reentrega (DUP): mismos acks, sin volver a ejecutar. Si no llegó a salir ninguno (lista vacía), se ejecuta de nuevo.
            if previous:
                self._handled.move_to_end(command_id)
                _log.info("command %s redelivered, resending its %d acks", command_id, len(previous))
                for ack in list(previous):
                    await self._publish(self._topic_ack(), ack, retain=False)
                return
            self._handled[command_id] = []
            while len(self._handled) > _HANDLED_MAX:
                self._handled.popitem(last=False)
            try:
                if incoming.command is None:
                    rejection = incoming.rejection
                    assert rejection is not None
                    await self._reply(command_id, AckStatus.REJECTED, now, code=rejection.code, message=rejection.message)
                else:
                    await self._dispatch(incoming.command, now)
                await self._status_if_changed(now, any_change=False)
            except Exception:  # noqa: BLE001  (un comando nunca tumba el gateway)
                _log.exception("command %s failed", command_id)
                with contextlib.suppress(Exception):
                    await self._reply(command_id, AckStatus.FAILED, now, code=AckCode.INTERNAL, message="internal gateway error")

    def _topic_ack(self) -> str:
        return topics.ack(self._settings.astronaut_id, self._settings.device_id)

    async def _reply(
        self, command_id: str, status_: AckStatus, at: datetime, *, code: AckCode | None = None, message: str | None = None, calibration_id: str | None = None
    ) -> bool:
        """Publica un ack y lo anota (si su comando sigue en la LRU) para reenviarlo ante una reentrega."""
        ack = commands.ack(self._settings, command_id, status_, at, code=code, message=message, calibration_id=calibration_id)
        sent = await self._publish(self._topic_ack(), ack, retain=False)
        handled = self._handled.get(command_id)
        if sent and handled is not None:
            handled.append(ack)
        return sent

    async def _reject(self, command_id: str, at: datetime, code: AckCode, message: str) -> None:
        await self._reply(command_id, AckStatus.REJECTED, at, code=code, message=message)

    async def _dispatch(self, command: Command, now: datetime) -> None:
        match command:
            case StartSessionCommand():
                await self._start_session(command, now)
            case StopSessionCommand():
                await self._stop_session(command, now)
            case MarkEventCommand():
                await self._mark_event(command, now)
            case CalibrateCommand():
                await self._calibrate(command, now)

    def _unknown_streams(self, stream_ids: Sequence[str] | None) -> list[str]:
        return [s for s in stream_ids or () if s not in self._kinds]

    async def _start_session(self, command: StartSessionCommand, now: datetime) -> None:
        cid = command.command_id
        if self._session is not None:
            await self._reject(cid, now, AckCode.BUSY, f"session {self._session.session_id} is already running on this device")
            return
        unknown = self._unknown_streams(command.stream_ids)
        if unknown:
            await self._reject(cid, now, AckCode.UNKNOWN_STREAM, f"unknown streams: {', '.join(unknown)}")
            return
        if not self._device_known:
            await self._reject(cid, now, AckCode.DEVICE_OFFLINE, "the device is not connected")
            return
        # `accepted` primero: si no sale, no se ha tocado nada y una reentrega lo vuelve a intentar.
        if not await self._reply(cid, AckStatus.ACCEPTED, now):
            return
        kinds = frozenset(self._kinds[s] for s in command.stream_ids) if command.stream_ids else frozenset(FrameKind)
        session = _Session(command.session_id, cid, kinds)
        self._session = session
        self._assembler.reset_background()
        if command.max_duration_s is not None:
            session.timer = self._spawn(self._session_deadline(command.max_duration_s))
        await self._publish_clock()

    async def _session_deadline(self, seconds: float) -> None:
        # Si la sesión acaba antes, `_end_session` cancela esta tarea (también si espera el lock): aquí sigue siendo la suya.
        await self._sleep(seconds)
        async with self._lock:
            now = self._now()
            await self._end_session(now)
            await self._status_if_changed(now, any_change=False)

    async def _end_session(self, now: datetime) -> None:
        """Acaba la sesión en curso: el fondo empieza de cero y sale el ``completed`` de su start_session."""
        session = self._session
        assert session is not None
        self._session = None
        if session.timer is not None and session.timer is not asyncio.current_task():
            session.timer.cancel()
        self._assembler.reset_background()
        await self._reply(session.command_id, AckStatus.COMPLETED, now)

    async def _stop_session(self, command: StopSessionCommand, now: datetime) -> None:
        cid = command.command_id
        if self._session is None or self._session.session_id != command.session_id:
            await self._reject(cid, now, AckCode.NO_SESSION, f"session {command.session_id} is not running")
            return
        if not await self._reply(cid, AckStatus.ACCEPTED, now):
            return
        # Cada lote de sesión ya salió al llegar: no hay nada que vaciar.
        await self._end_session(now)
        await self._reply(cid, AckStatus.COMPLETED, now)

    async def _mark_event(self, command: MarkEventCommand, now: datetime) -> None:
        cid = command.command_id
        if self._session is None or self._session.session_id != command.session_id:
            await self._reject(cid, now, AckCode.NO_SESSION, f"session {command.session_id} is not running")
            return
        # Sin efecto en el dispositivo; si el `accepted` no sale, el `completed` tampoco (la conexión ya no está).
        await self._reply(cid, AckStatus.ACCEPTED, now)
        await self._reply(cid, AckStatus.COMPLETED, now)

    async def _calibrate(self, command: CalibrateCommand, now: datetime) -> None:
        """Como el gateway falso, no exige que ``session_id`` sea la sesión en curso: la calibración la lleva tal cual."""
        cid = command.command_id
        calibration_type = command.calibration_type
        kind = _CALIBRATED_KIND.get(calibration_type)
        if kind is None:
            # El enum de error.code no tiene "no soportado"; invalid_command, como el falso con una calibración que no sabe hacer.
            await self._reject(cid, now, AckCode.INVALID_COMMAND, f"{calibration_type.value} calibration is not supported by this gateway")
            return
        unknown = self._unknown_streams(command.stream_ids)
        if unknown:
            await self._reject(cid, now, AckCode.UNKNOWN_STREAM, f"unknown streams: {', '.join(unknown)}")
            return
        wrong = [s for s in command.stream_ids if self._kinds[s] is not kind]
        if wrong:
            await self._reject(cid, now, AckCode.UNKNOWN_STREAM, f"not {calibration_type.value} streams: {', '.join(wrong)}")
            return
        if not self._device_known:
            await self._reject(cid, now, AckCode.DEVICE_OFFLINE, "the device is not connected")
            return
        if self._calibration is not None:
            await self._reject(cid, now, AckCode.BUSY, f"calibration {self._calibration.command_id} is already running on this device")
            return
        if not await self._reply(cid, AckStatus.ACCEPTED, now):
            return
        duration = command.duration_s if command.duration_s is not None else self._settings.calibration_default_duration_s
        recorders = [
            (s, CalibrationRecorder(calibration_type, s, duration, self._settings, self._new_id, session_id=command.session_id)) for s in command.stream_ids
        ]
        calibration = _Calibration(cid, recorders)
        self._calibration = calibration
        calibration.timer = self._spawn(self._calibration_deadline(calibration, duration + CALIBRATION_GRACE_S))

    async def _calibration_deadline(self, calibration: _Calibration, seconds: float) -> None:
        # Si la calibración acaba antes, `_finish_calibration` cancela esta tarea (también si espera el lock).
        await self._sleep(seconds)
        async with self._lock:
            self._calibration = None
            message = f"calibration_timeout: no complete data within {seconds} s"
            await self._reply(calibration.command_id, AckStatus.FAILED, self._now(), code=AckCode.CALIBRATION_FAILED, message=message)

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """Temporizador de sesión o de calibración dentro del TaskGroup de ``run``; un error suyo no tumba el gateway."""
        assert self._group is not None, "Gateway.run is not running"
        return self._group.create_task(_guarded(coro))

    # --- conexión MQTT ---

    async def _announce(self, client: MqttPublisher) -> None:
        """Conexión nueva: ancla y estado antes que nada (el Last Will pudo dejar ``offline``)."""
        async with self._lock:
            s = self._settings
            now = self._now()
            self._client = client
            self._lost.clear()
            # El broker pudo perder los retained: cada calibración se republica antes del siguiente chunk que la cite.
            self._retained_calibrations.clear()
            if self._last_tick is not None:
                await self._publish_clock()
            self._next_clock = now + timedelta(seconds=s.clock_sync_interval_s)
            await self._publish_status(now)
            self._next_status = now + timedelta(seconds=s.status_interval_s)
        self._wake.set()

    async def _listen(self, client: MqttClient) -> None:
        expected = topics.cmd(self._settings.astronaut_id, self._settings.device_id)
        async for message in client.messages:
            if str(message.topic) != expected:
                _log.warning("message on unexpected topic %s ignored", message.topic)
                continue
            await self.handle(_as_bytes(message.payload))

    async def _serve_connection(self, client: MqttClient) -> None:
        s = self._settings
        try:
            await client.subscribe(topics.cmd(s.astronaut_id, s.device_id), qos=QOS)
            await self._announce(client)
            listen = asyncio.ensure_future(self._listen(client))
            lost = asyncio.ensure_future(self._lost.wait())
            try:
                done, _ = await asyncio.wait((listen, lost), return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (listen, lost):
                    task.cancel()
                await asyncio.gather(listen, lost, return_exceptions=True)
            if listen in done:
                listen.result()
                raise aiomqtt.MqttError("the message stream ended")
            raise aiomqtt.MqttError("a publish failed")
        except aiomqtt.MqttError:
            raise  # conexión perdida: el Last Will ya avisa y no hay por dónde publicar
        except BaseException:
            await self._shutdown(client)
            raise
        finally:
            if self._client is client:
                self._client = None

    async def _shutdown(self, client: MqttPublisher) -> None:
        """Parada limpia: ``offline`` retained (el Last Will solo salta si la conexión se pierde)."""
        self._client = None
        try:
            await client.publish(self._topic_status(), encode(status.offline_status(self._settings, at=self._now())), qos=QOS, retain=True)
        except Exception:  # noqa: BLE001  (el broker puede haberse ido ya)
            _log.warning("could not publish the offline status on shutdown", exc_info=True)

    async def _connect(self, client_factory: ClientFactory) -> None:
        """Conecta con su Last Will y atiende la conexión; si el broker la rechaza o la corta, backoff y otra vez."""
        s = self._settings
        identifier = f"{s.mqtt_client_id}-{s.device_id}"
        delay = s.mqtt_reconnect_initial_s
        while True:
            try:
                async with client_factory(hostname=s.mqtt_host, port=s.mqtt_port, identifier=identifier, will=self.last_will()) as client:
                    _log.info("connected to %s:%d as %s", s.mqtt_host, s.mqtt_port, identifier)
                    delay = s.mqtt_reconnect_initial_s
                    await self._serve_connection(client)
            except aiomqtt.MqttError as error:
                _log.warning("MQTT connection refused or lost (%s); reconnecting in %.1f s", error, delay)
            await self._sleep(delay)
            delay = min(delay * s.mqtt_reconnect_factor, s.mqtt_reconnect_max_s)

    async def run(self, source: LineSource, client_factory: ClientFactory) -> None:
        """Atiende el enlace serie, el temporizador y la conexión MQTT hasta que se cancela."""
        async with asyncio.TaskGroup() as group:
            self._group = group
            group.create_task(self._consume(source))
            group.create_task(self._timers())
            group.create_task(self._connect(client_factory))


# --- composición ---


def utc_now() -> datetime:
    """Reloj real; la lógica lo recibe inyectado (``clock``)."""
    return datetime.now(UTC)


def build_source(settings: GatewaySettings, clock: Clock = utc_now) -> LineSource:
    """El puerto serie real. Se importa aquí para que ``app`` y sus tests no dependan de pyserial."""
    from spine_gateway.serial_source import SerialLineSource

    return SerialLineSource(
        settings.serial_url,
        settings.serial_baud,
        clock,
        reconnect_initial_s=settings.serial_reconnect_initial_s,
        reconnect_factor=settings.serial_reconnect_factor,
        reconnect_max_s=settings.serial_reconnect_max_s,
    )


async def serve(
    settings: GatewaySettings,
    *,
    source: LineSource,
    client_factory: ClientFactory,
    clock: Clock,
    sleep: Sleep = asyncio.sleep,
    new_id: Callable[[], str] = new_uuid,
) -> None:
    """``Gateway.run`` hasta que se cancela. SIGTERM (Docker) para igual de limpio que Ctrl+C (``offline`` retained)."""
    gateway = Gateway(settings, clock=clock, sleep=sleep, new_id=new_id)
    task = asyncio.create_task(gateway.run(source, client_factory))
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


async def main(settings: GatewaySettings) -> None:
    """Arduino real por ``serial_url`` y broker real con aiomqtt."""
    await serve(settings, source=build_source(settings), client_factory=aiomqtt.Client, clock=utc_now)
