"""MQTT loop of the real gateway: wearable Arduino over USB serial -> MQTT v1 topics.

One device per process (``astronaut_id``/``device_id`` from the settings) and one MQTT connection (aiomqtt) with a Last Will
``offline`` retained on ``topics.status``. Three tasks in a ``TaskGroup`` (``Gateway.run``):

- Serial consumer (``LineSource.events``): ``LinkUp``/``LinkDown`` mark the link; each ``SerialLine`` is interpreted with
  ``protocol.parse_line``. A ``BadLine`` is counted by reason and discarded. ``Hello``/``Tick`` give the boot
  (``boot_id``): a new one resets seq and ``last_seq`` and requests a clock anchor; the ``Tick`` is the anchor
  (``gateway_receive_time``). Each ``Frame`` goes to the assembler: session chunk if there is a session and its stream participates;
  otherwise background (IMU and cord; EMG without a session is discarded). Nothing from the device is published until the first
  hello/tick after opening the port (the Uno resets when opened).
- Timer: DeviceStatus every ``status_interval_s``, ClockSync every ``clock_sync_interval_s`` and detection of
  stalled streams (``stall_timeout_s``). The status also goes out as soon as it changes (see ``status``).
- MQTT connection with reconnection and exponential backoff (``mqtt_reconnect_*``). After each connection the anchor and
  status are announced first of all and the calibrations are republished before the next chunk that cites them.

All state is touched by the tasks one at a time (``_lock``). An error while processing a line or a command is logged and
the loop continues. Cancelling ``run`` (SIGTERM) publishes the retained ``offline`` and closes the source and the connection. Clock and
waits are injectable (``clock``, ``sleep``): no logic reads the wall clock.
"""

import asyncio
import contextlib
import json
import logging
import signal
import ssl
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

# Prefix of the firmware debug lines and their codec, as module constants (mutmut does not mutate the module).
_DEBUG_PREFIX = b"#"
_ASCII = "ascii"

QOS = 1
"""QoS of all messages (AsyncAPI contract)."""

_STREAM_KIND = {FrameKind.EMG: "emg", FrameKind.IMU: "imu", FrameKind.CORD: "stretch"}
_HANDLED_MAX = 256
"""Already handled commands that are remembered (LRU): a QoS 1 redelivery (DUP) receives the same acks without running again."""


CALIBRATION_GRACE_S = 5.0
"""Margin over ``duration_s`` after which a calibration without enough data fails (``calibration_timeout``)."""
_CALIBRATED_KIND = {CalibrationType.STRETCH: FrameKind.CORD, CalibrationType.EMG_MVC: FrameKind.EMG}


@dataclass(slots=True)
class _Session:
    session_id: str
    command_id: str  # that of the start_session, for its `completed`
    kinds: frozenset[FrameKind]
    timer: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _Calibration:
    command_id: str
    recorders: list[tuple[str, CalibrationRecorder]]  # (stream_id, recording), in the order of the command
    timer: asyncio.Task[None] | None = None


class MqttPublisher(Protocol):
    # `def` + Awaitable (not `async def`): aiomqtt returns a Coroutine, which a Protocol `async def` does not accept.
    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> Awaitable[None]: ...


class IncomingMessage(Protocol):
    """What the gateway uses from ``aiomqtt.Message``."""

    @property
    def topic(self) -> object: ...  # aiomqtt.Topic; str(topic) gives the topic

    @property
    def payload(self) -> bytes | bytearray | str | int | float | None: ...


class MqttClient(MqttPublisher, Protocol):
    """What the gateway uses from ``aiomqtt.Client``."""

    def subscribe(self, topic: str, qos: int) -> Awaitable[object]: ...

    @property
    def messages(self) -> AsyncIterator[IncomingMessage]: ...


type ClientFactory = Callable[..., AbstractAsyncContextManager[MqttClient]]
type Clock = Callable[[], datetime]
type Sleep = Callable[[float], Awaitable[object]]


def _relevant(signature: tuple[Any, ...] | None) -> tuple[Any, ...] | None:
    """The part of a status signature that is published as soon as it changes: everything except active/no data yet."""
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
    """Compact UTF-8 JSON; NaN/inf are not JSON and fail here instead of reaching the bus."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


class Gateway:
    """State of a device and the tasks that publish it. Survives MQTT reconnections and link outages."""

    def __init__(self, settings: GatewaySettings, *, clock: Clock, sleep: Sleep = asyncio.sleep, new_id: Callable[[], str] = new_uuid) -> None:
        """``clock`` gives the UTC instant (aware); ``sleep`` waits those seconds; ``new_id`` gives the id of each calibration."""
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
        # Current boot. `_device_known`: the link is up and the device has spoken (hello or tick) since it was opened.
        self._device_known = False
        self._boot_since: datetime | None = None
        self._firmware: str | None = None
        self._uptime_ms = 0
        self._last_tick: tuple[int, datetime] | None = None  # (millis, arrival) of the last tick of the current boot
        self._clock_pending = False
        self._last_frame_at: dict[FrameKind, datetime] = {}
        self._published_seq: dict[FrameKind, int] = {}
        # Session and commands.
        self._session: _Session | None = None
        self._calibration: _Calibration | None = None
        # Current calibrations per stream and the one retained on the broker by this connection (cleared on reconnect).
        self._calibrations: dict[str, StretchCalibration | EmgMvcCalibration] = {}
        self._stretch_calibration: StretchCalibration | None = None
        self._retained_calibrations: dict[str, str] = {}
        self._handled: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()  # command_id -> acks already sent
        self._group: asyncio.TaskGroup | None = None
        # Timer schedule.
        now = self._now()
        self._next_status = now
        self._next_clock = now
        self._signature: tuple[Any, ...] | None = None
        self.bad_lines: Counter[BadLineReason] = Counter()
        self.discarded: Counter[DiscardReason] = Counter()

    # --- time ---

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError(f"clock must return an aware UTC datetime, got {now!r}")
        return now

    # --- publishing ---

    def last_will(self) -> aiomqtt.Will:
        """``offline`` DeviceStatus retained, QoS 1 (with the connection time)."""
        payload = status.offline_status(self._settings, at=self._now())
        return aiomqtt.Will(topic=self._topic_status(), payload=encode(payload), qos=QOS, retain=True)

    def _topic_status(self) -> str:
        return topics.status(self._settings.astronaut_id, self._settings.device_id)

    async def _publish(self, topic: str, payload: dict[str, Any], *, retain: bool) -> bool:
        """Publishes over the current connection; ``False`` if there is none or it was lost (the connection is rebuilt separately)."""
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
        """Anchor of the last tick of the current boot; if there is none yet, it stays pending for the first one."""
        boot_id = self._assembler.boot_id
        if self._last_tick is None or boot_id is None:
            self._clock_pending = True
            return
        millis, at = self._last_tick
        self._clock_pending = False
        s = self._settings
        await self._publish(topics.clock(s.astronaut_id, s.device_id), status.clock_sync(s, boot_id=boot_id, device_ms=millis, utc=at), retain=True)

    # --- state ---

    def _in_session(self, kind: FrameKind) -> bool:
        return self._session is not None and kind in self._session.kinds

    def _rate(self, kind: FrameKind) -> float | None:
        """Frequency at which the stream is published: native in session, 10 Hz in background and nothing (EMG) outside a session."""
        if self._in_session(kind):
            return SAMPLE_RATE_HZ[kind]
        return None if kind is FrameKind.EMG else BACKGROUND_SAMPLE_RATE_HZ

    def _timing(self, kind: FrameKind, now: datetime) -> dict[str, Any]:
        """Arguments of ``status.stream_state``/``stall_deadline`` for a stream at ``now``."""
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
        """The DeviceStatus at ``now`` and its signature (what, if it changes, deserves publishing right away)."""
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
        """Publishes the status if it changed. With ``any_change=False`` (each serial line) only relevant changes count:
        global status, boot, streams that stall or come back and frequencies; the rest (e.g. the first batch of a stream
        after booting) is published by the timer, which wakes at most ``stall_timeout_s`` later."""
        payload, signature = self._status(now)
        changed = signature != self._signature if any_change else _relevant(signature) != _relevant(self._signature)
        if changed:
            self._signature = signature
            self._wake.set()  # the timer recomputes when a stream may stall
            await self._publish(self._topic_status(), payload, retain=True)

    def _stall_deadlines(self, now: datetime) -> list[datetime]:
        if not self._device_known:
            return []
        deadlines = (status.stall_deadline(**self._timing(kind, now)) for kind in self._stream_ids)
        return [d for d in deadlines if d is not None]

    # --- serial link ---

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
        """Until the next hello/tick it is not known which boot is speaking (opening the port resets the Uno)."""
        self._device_known = False
        self._last_tick = None
        self._last_frame_at.clear()

    async def _on_line(self, data: bytes, at: datetime) -> None:
        if data.startswith(_DEBUG_PREFIX):
            # Firmware debug text (``#imu ok``, ``#overrun``...): not protocol, carries no CRC and does not count as a bad line.
            _log.info("arduino: %s", data.decode(_ASCII, errors="replace"))
            return
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
        # Warning at 1, 2, 4, 8...: a noisy port does not flood the log.
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
        # The status (offline -> ok, or new boot_id) is published by `_status_if_changed` at the end of the line.

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
        progress = [recorder.feed(frame) for _, recorder in calibration.recorders]  # all of them, no short-circuit
        if all(p is CalibrationStatus.DONE for p in progress):
            await self._finish_calibration(calibration, at)

    async def _finish_calibration(self, calibration: _Calibration, now: datetime) -> None:
        """Retained calibrations (whoever reads the ack can already resolve its id) and then the ``completed``; or the ``failed``."""
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
        # The contract admits one calibration_id per ack: that of the first stream (all remain current anyway).
        await self._reply(calibration.command_id, AckStatus.COMPLETED, now, calibration_id=done[0][1].calibration_id)

    async def _ensure_calibration(self, stream_id: str) -> None:
        """Leaves the current calibration of ``stream_id`` retained if this connection has not published it yet."""
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
                except Exception:  # noqa: BLE001  (one line never takes down the gateway)
                    _log.exception("serial event %r failed", event)
        finally:
            close = getattr(events, "aclose", None)
            if close is not None:
                await close()

    # --- timer ---

    async def _tick(self) -> float:
        """Publishes what is due (anchor, periodic or on-change status) and returns the seconds until the next one."""
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
        """Waits ``seconds`` with ``sleep`` (injectable) or until something wakes the timer (``_wake``)."""
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

    # --- commands ---

    async def handle(self, payload: bytes) -> None:
        """Processes a command received on ``topics.cmd``. Never raises because of a bad command."""
        async with self._lock:
            incoming = commands.read_command(payload, astronaut_id=self._settings.astronaut_id, device_id=self._settings.device_id)
            if incoming is None:
                _log.warning("unreadable command ignored (no command_id)")
                return
            command_id = incoming.command_id
            now = self._now()
            previous = self._handled.get(command_id)
            # Redelivery (DUP): same acks, without running again. If none had gone out (empty list), it runs again.
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
            except Exception:  # noqa: BLE001  (one command never takes down the gateway)
                _log.exception("command %s failed", command_id)
                with contextlib.suppress(Exception):
                    await self._reply(command_id, AckStatus.FAILED, now, code=AckCode.INTERNAL, message="internal gateway error")

    def _topic_ack(self) -> str:
        return topics.ack(self._settings.astronaut_id, self._settings.device_id)

    async def _reply(
        self, command_id: str, status_: AckStatus, at: datetime, *, code: AckCode | None = None, message: str | None = None, calibration_id: str | None = None
    ) -> bool:
        """Publishes an ack and records it (if its command is still in the LRU) to resend it on a redelivery."""
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
        # `accepted` first: if it does not go out, nothing has been touched and a redelivery tries again.
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
        # If the session ends earlier, `_end_session` cancels this task (also if it waits for the lock): here it is still its own.
        await self._sleep(seconds)
        async with self._lock:
            now = self._now()
            await self._end_session(now)
            await self._status_if_changed(now, any_change=False)

    async def _end_session(self, now: datetime) -> None:
        """Ends the current session: the background starts from zero and the ``completed`` of its start_session goes out."""
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
        # Each session batch already went out on arrival: there is nothing to flush.
        await self._end_session(now)
        await self._reply(cid, AckStatus.COMPLETED, now)

    async def _mark_event(self, command: MarkEventCommand, now: datetime) -> None:
        cid = command.command_id
        if self._session is None or self._session.session_id != command.session_id:
            await self._reject(cid, now, AckCode.NO_SESSION, f"session {command.session_id} is not running")
            return
        # No effect on the device; if the `accepted` does not go out, neither does the `completed` (the connection is gone).
        await self._reply(cid, AckStatus.ACCEPTED, now)
        await self._reply(cid, AckStatus.COMPLETED, now)

    async def _calibrate(self, command: CalibrateCommand, now: datetime) -> None:
        """Like the fake gateway, it does not require ``session_id`` to be the current session: the calibration carries it as is."""
        cid = command.command_id
        calibration_type = command.calibration_type
        kind = _CALIBRATED_KIND.get(calibration_type)
        if kind is None:
            # The error.code enum has no "unsupported"; invalid_command, like the fake with a calibration it cannot do.
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
            (s, CalibrationRecorder(
                calibration_type, s, duration, self._settings, self._new_id,
                session_id=command.session_id, reference_posture=command.reference_posture,
            ))
            for s in command.stream_ids
        ]
        calibration = _Calibration(cid, recorders)
        self._calibration = calibration
        calibration.timer = self._spawn(self._calibration_deadline(calibration, duration + CALIBRATION_GRACE_S))

    async def _calibration_deadline(self, calibration: _Calibration, seconds: float) -> None:
        # If the calibration ends earlier, `_finish_calibration` cancels this task (also if it waits for the lock).
        await self._sleep(seconds)
        async with self._lock:
            self._calibration = None
            message = f"calibration_timeout: no complete data within {seconds} s"
            await self._reply(calibration.command_id, AckStatus.FAILED, self._now(), code=AckCode.CALIBRATION_FAILED, message=message)

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """Session or calibration timer inside the ``run`` TaskGroup; an error of its own does not take down the gateway."""
        assert self._group is not None, "Gateway.run is not running"
        return self._group.create_task(_guarded(coro))

    # --- MQTT connection ---

    async def _announce(self, client: MqttPublisher) -> None:
        """New connection: anchor and status first of all (the Last Will may have left ``offline``)."""
        async with self._lock:
            s = self._settings
            now = self._now()
            self._client = client
            self._lost.clear()
            # The broker may have lost the retained: each calibration is republished before the next chunk that cites it.
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
            raise  # connection lost: the Last Will already warns and there is nowhere to publish
        except BaseException:
            await self._shutdown(client)
            raise
        finally:
            if self._client is client:
                self._client = None

    async def _shutdown(self, client: MqttPublisher) -> None:
        """Clean stop: retained ``offline`` (the Last Will only fires if the connection is lost)."""
        self._client = None
        try:
            await client.publish(self._topic_status(), encode(status.offline_status(self._settings, at=self._now())), qos=QOS, retain=True)
        except Exception:  # noqa: BLE001  (the broker may already be gone)
            _log.warning("could not publish the offline status on shutdown", exc_info=True)

    async def _connect(self, client_factory: ClientFactory) -> None:
        """Connects with its Last Will and serves the connection; if the broker rejects or drops it, backoff and again."""
        s = self._settings
        identifier = f"{s.mqtt_client_id}-{s.device_id}"
        options: dict[str, Any] = {}
        if s.mqtt_username:
            options["username"] = s.mqtt_username
        if s.mqtt_password is not None:
            options["password"] = s.mqtt_password.get_secret_value()
        if s.mqtt_tls:
            options["tls_context"] = ssl.create_default_context(cafile=str(s.mqtt_tls_ca_file) if s.mqtt_tls_ca_file else None)
        delay = s.mqtt_reconnect_initial_s
        while True:
            try:
                async with client_factory(hostname=s.mqtt_host, port=s.mqtt_port, identifier=identifier, will=self.last_will(), **options) as client:
                    _log.info("connected to %s:%d as %s", s.mqtt_host, s.mqtt_port, identifier)
                    delay = s.mqtt_reconnect_initial_s
                    await self._serve_connection(client)
            except aiomqtt.MqttError as error:
                _log.warning("MQTT connection refused or lost (%s); reconnecting in %.1f s", error, delay)
            await self._sleep(delay)
            delay = min(delay * s.mqtt_reconnect_factor, s.mqtt_reconnect_max_s)

    async def run(self, source: LineSource, client_factory: ClientFactory) -> None:
        """Serves the serial link, the timer and the MQTT connection until cancelled."""
        async with asyncio.TaskGroup() as group:
            self._group = group
            group.create_task(self._consume(source))
            group.create_task(self._timers())
            group.create_task(self._connect(client_factory))


# --- composition ---


def utc_now() -> datetime:
    """Real clock; the logic receives it injected (``clock``)."""
    return datetime.now(UTC)


def build_source(settings: GatewaySettings, clock: Clock = utc_now) -> LineSource:
    """The real serial port. Imported here so that ``app`` and its tests do not depend on pyserial."""
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
    """``Gateway.run`` until cancelled. SIGTERM (Docker) stops as cleanly as Ctrl+C (retained ``offline``)."""
    gateway = Gateway(settings, clock=clock, sleep=sleep, new_id=new_id)
    task = asyncio.create_task(gateway.run(source, client_factory))
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError, RuntimeError, AttributeError):  # Windows has no SIGTERM in the loop
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
    """Real Arduino via ``serial_url`` and real broker with aiomqtt."""
    await serve(settings, source=build_source(settings), client_factory=aiomqtt.Client, clock=utc_now)
