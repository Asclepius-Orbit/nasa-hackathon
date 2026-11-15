"""Async loop of the fake gateway: it behaves on MQTT like the real wearable gateway.

For each astronaut in the config (`FakeGateway`, normally one per MQTT connection, see `serve`):
- Subscribes to `topics.cmd(astronaut_id, device_id)`. Each command is validated against the JSON Schema
  (`spine_contracts.validation`) and with `spine_contracts.command_adapter`, and answered on
  `topics.ack(...)` (CommandAck, QoS 1). Every accepted command gets `accepted` and, when done, `completed`:
  - start_session: `accepted` and starts publishing that session's chunks (`MissionGenerator.live_session`);
    `completed` when it ends (stop_session or `max_duration_s`). `rejected` with `busy` if a session already
    exists, `unknown_stream` if it asks for a stream the device does not have and `device_offline` if it is rebooting.
  - stop_session: `accepted`, flushes what the device had already committed, `completed` of the
    start_session and then `completed` of the stop. `rejected`/`no_session` if there is no session or it does not match.
  - mark_event: `accepted` + `completed` (only with the given session running; otherwise `no_session`). It does not
    change the signal: the live protocol follows its own pace.
  - calibrate: `accepted` and `completed` with `result.calibration_id` (`MissionGenerator.live_calibration`)
    after `duration_s` (or immediately). Just before the `completed`, each calibration is published on
    `topics.calibration(...)` (QoS 1, retained), unless another `calibrate` of the same stream has replaced it
    in the meantime. The contract only allows one calibration_id per ack: with several streams the first one's
    is used (all of them become in force anyway).
  - Invalid command: `rejected`/`invalid_command` if its command_id can be read; otherwise it is ignored.
    An unexpected error while processing it gives `failed`/`internal`; the gateway never crashes because of a command.
- Retained DeviceStatus on `topics.status(...)` at startup and every `device.status_interval_s`; retained ClockSync
  on `topics.clock(...)` at startup, at the start of each session and every `device.clock_sync_interval_s`.
  `last_seq` of each stream = last PUBLISHED seq of the current boot (never one not yet sent).
  Retained Last Will with `status: offline` (`last_will`) and the same `offline` on a clean stop.
- Calibrations on the bus: before publishing a chunk with `calibration_id`, if it cites the calibration in force
  for its stream (`MissionGenerator.calibration_in_force`: that of a `calibrate` or, otherwise, the mission's
  scheduled one that day) and it is not the retained one on `topics.calibration(...)`, it is published (retained). This way a
  consumer that connects late resolves the calibration_id of the chunks it receives. After a `calibrate`, the chunks
  left in the current block (at most one status interval) still cite the previous one: it is not republished
  if another one has come out after it, so a stream's retained never goes backwards. Ground truth never
  goes on the bus.
- Background (if `schedule.background.enabled`): IMU + cord from `live_background` whenever there is no session
  (without the config's time window: the gateway is for demos at any hour). A session pauses it
  (same stream_id, seq continues) and when it ends it resumes with the same session_id; each new mission
  day has its own background session_id.
- Mission day = `start_day` + (wall clock - start) × `speed` / 1 day, bounded to the mission. Sessions
  and background use it when they start (a session does not change day mid-session).
- Device: `plan_device_live` from startup, with horizon = the config's daily power-on window
  (one battery charge). When it runs out, and with no session running, it is "recharged": new plan (new boot_id,
  seq and clock at 0, full battery) and status and anchor are published immediately.

Chunks are published at their `received_at` according to the injected clock (`clock`) and waiting is done with `sleep`
(injectable): tests run in virtual time and with a fake MQTT client (Protocol `MqttClient`). The
maximum wait is one chunk of the active source (session: `signals.chunk_ms`; background:
`schedule.background.chunk_ms`) and a command cuts it short immediately.

Flush detail: reading the next chunk from a live iterator commits that chunk's seq on the device
for all streams. When pausing or stopping, the already committed chunks are published (they may
carry a `received_at` a few ms in the future) and the next source starts after the last one, so each
stream's seq continues without gaps and the spans do not overlap.

Synthesis outside the loop: reading from a live iterator can synthesize a whole protocol (filtered EMG,
all streams), so it is done in a thread (`asyncio.to_thread`) while holding `_lock`: commands
wait for the lock as before, but the loop (MQTT keepalive, reception) stays alive. What was read is
kept in the `_Source` and only removed when published; if the task is cancelled mid-read, the thread
finishes and its result is collected on the next one (`_Source.settle`), without losing or repeating chunks.

Reconnection (`_serve`): if the broker rejects or drops the connection (MqttError), it waits with exponential
backoff (`mqtt_reconnect_*` of the settings) and opens another connection (with its Last Will) on the same
`FakeGateway`: session, background, device seq and already read chunks stay as they were. A chunk whose publish
fails is not removed and is published on the next connection (no seq gap; at most a duplicate if the
broker did receive it, normal in QoS 1). On reconnecting, status and anchor are republished immediately
(the Last Will will have left `offline`) and, before the next chunk, its calibration (the broker may have
lost the retained ones). SIGTERM still stops cleanly with the retained `offline`.
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
# The schema's commands are tiny (hundreds of bytes): above this they are not even parsed (a huge or
# absurdly nested JSON must not cost time or bring down the loop). If its command_id is read, `invalid_command`.
_PAYLOAD_MAX_BYTES = 16 * 1024
_COMMAND_ID_PREFIX = re.compile(rb'"command_id"\s*:\s*"([0-9a-f-]{36})"')
# Cap for max_duration_s / duration_s: the schema does not bound it and a huge value overflows timedelta/datetime
# (OverflowError) when state would already have been touched. 30 days is plenty for any demo mission.
_DURATION_MAX_S = 30 * 86400
# Commands already handled per device (LRU): a QoS 1 can be redelivered (DUP) and then the
# same acks are resent instead of running the command again.
_HANDLED_MAX = 256
# Envelopes per read in the synthesis thread (bounds memory if the clock jumps a lot at once).
_PULL_MAX = 256

type AckStatus = Literal["accepted", "rejected", "completed", "failed"]
type AckCode = Literal["busy", "unknown_stream", "no_session", "device_offline", "calibration_failed", "invalid_command", "internal"]


class IncomingMessage(Protocol):
    """What the gateway uses from `aiomqtt.Message`."""

    @property
    def topic(self) -> object: ...  # aiomqtt.Topic; str(topic) gives the topic

    @property
    def payload(self) -> bytes | bytearray | str | int | float | None: ...


class MqttClient(MqttPublisher, Protocol):
    """What the gateway uses from `aiomqtt.Client`."""

    def subscribe(self, topic: str, qos: int) -> Awaitable[object]: ...

    @property
    def messages(self) -> AsyncIterator[IncomingMessage]: ...


def _encode(payload: dict[str, Any]) -> bytes:
    """Compact JSON in UTF-8 (the same the MQTT sink would publish)."""
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
    """First `at + n × step` (n >= 1) after `now`."""
    if at > now:
        return at
    return at + (math.floor((now - at) / step) + 1) * step


def _pull_bus(iterator: Iterator[Envelope], until: datetime | None) -> list[Envelope]:
    """(In a thread.) Reads from the live iterator, synthesizing whatever is needed, up to the first bus envelope
    after `until` (included); with `until` None, just one; at most `_PULL_MAX`. Ground truth
    (no topic) is discarded: JSONL only, never on the bus."""
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
    """Live iterator (session or background) with the bus envelopes already read and not yet published.

    The read (which commits seq on the device and may synthesize a whole block) runs in a thread;
    it must only be launched with the gateway's `_lock` held. An envelope leaves the buffer (`take`) only after
    being published.
    """

    def __init__(
        self, iterator: Iterator[Envelope], *, device: SimDevice, session_id: str, day: int, streams: tuple[str, ...], chunk: timedelta
    ) -> None:
        self._iterator = iterator
        self._buffer: deque[Envelope] = deque()
        # Read in progress in a thread; survives the cancellation of the task that launched it (see `settle`).
        self._pending: asyncio.Future[list[Envelope]] | None = None
        self.device = device
        self.session_id = session_id
        self.day = day
        self.streams = streams
        self.chunk = chunk  # chunk duration of this source (session or background): sets the loop's pace
        self.boot_id: int | None = None  # boot of the last chunk read

    @property
    def buffered(self) -> bool:
        return bool(self._buffer)

    async def settle(self) -> None:
        """Collects a read left in flight (cancelled task, e.g. by a disconnection): on return,
        no thread touches the device or the iterator any more."""
        pending = self._pending
        if pending is None:
            return
        try:
            # shield: if this task is cancelled, the thread continues and its result is collected on the next call.
            got = await asyncio.shield(pending)
        except asyncio.CancelledError:
            if pending.cancelled():  # only if the loop is closing: nothing to collect
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
        """The next unpublished bus envelope; if there is none, reads (in a thread) until past `until`."""
        await self.settle()
        while not self._buffer:
            self._pending = asyncio.ensure_future(asyncio.to_thread(_pull_bus, self._iterator, until))
            await self.settle()
        return self._buffer[0]

    def take(self) -> Envelope:
        """Removes the first one (already published)."""
        return self._buffer.popleft()


@dataclass(slots=True)
class _Session:
    session_id: str
    command_id: str  # the start_session's, for its `completed`
    source: _Source
    deadline: datetime | None


@dataclass(slots=True)
class _Unit:
    """State of a device (one astronaut)."""

    member: CrewMember
    device: SimDevice
    device_until: datetime
    status_rng: np.random.Generator
    clock_rng: np.random.Generator
    next_status: datetime
    next_clock: datetime
    session: _Session | None = None
    background: _Source | None = None
    # Last seq published per (boot_id, stream_id).
    published: dict[tuple[int, str], int] = field(default_factory=dict)
    # What goes out later, in order: calibrations and the `completed` of a calibrate with a duration.
    pending: list[tuple[datetime, Envelope]] = field(default_factory=list)
    # command_id -> acks already sent (LRU bounded to `_HANDLED_MAX`), to answer a redelivery the same way.
    handled: OrderedDict[str, list[Envelope]] = field(default_factory=OrderedDict)
    # calibration topic -> calibration_id retained there by this connection (cleared on reconnect).
    retained_calibrations: dict[str, str] = field(default_factory=dict)
    # calibration topic -> last calibration_id published on any connection (not cleared on reconnect).
    bus_calibrations: dict[str, str] = field(default_factory=dict)
    unresolved_calibrations: set[str] = field(default_factory=set)  # ids without a known calibration (already warned)

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
        """`clock` returns the current UTC instant (aware); `sleep` waits those seconds (virtual time in
        tests). `astronaut_ids` limits the gateway to those astronauts (None = the whole crew)."""
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
        self._wake = asyncio.Event()  # a processed command wakes the loop (a session may have started)
        # Each source at its own pace: sessions are chunked at `signals.chunk_ms` and the background at its own.
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

    # --- time -----------------------------------------------------------------------------------

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError(f"clock must return an aware UTC datetime, got {now!r}")
        return now

    def mission_day(self, now: datetime) -> int:
        """Mission day index at `now`: advances with the wall clock × speed, bounded to the mission."""
        elapsed = max(0.0, (now - self._started_at).total_seconds())
        day = self._start_day + math.floor(elapsed * self._speed / _DAY_S)
        return min(day, self._config.total_days - 1)

    @property
    def command_topics(self) -> list[str]:
        return list(self._by_cmd_topic)

    # --- device ------------------------------------------------------------------------------

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
        """The device's Last Will: retained `offline` DeviceStatus, QoS 1 (with the connection time)."""
        member = self._config.member(astronaut_id)
        status = self._offline_status(member, self._now())
        return aiomqtt.Will(topic=topics.status(member.astronaut_id, member.device_id), payload=_encode(dump(status)), qos=QOS, retain=True)

    # --- publishing ------------------------------------------------------------------------------

    async def _publish(self, client: MqttPublisher, envelope: Envelope) -> None:
        # Everything in MQTT_KINDS has a topic (chunks, status, anchor, acks and calibrations, the latter retained);
        # ground truth does not get here: `_pull_bus` discards it.
        assert envelope.topic is not None, f"{envelope.kind} is not published on the bus"
        await client.publish(envelope.topic, payload=_encode(envelope.payload), qos=QOS, retain=envelope.retained)

    async def _publish_chunk(self, client: MqttPublisher, unit: _Unit, envelope: Envelope) -> None:
        if envelope.payload.get("calibration_id") is not None:
            await self._ensure_calibration(client, unit, envelope)
        await self._publish(client, envelope)
        key = (int(envelope.payload["boot_id"]), str(envelope.payload["stream_id"]))
        unit.published[key] = max(unit.published.get(key, -1), int(envelope.payload["seq"]))

    async def _ensure_calibration(self, client: MqttPublisher, unit: _Unit, chunk: Envelope) -> None:
        """Leaves retained on the bus the calibration a chunk cites before publishing it, without its stream's
        retained ever going back to an earlier one."""
        calibration_id = str(chunk.payload["calibration_id"])
        stream_id = str(chunk.payload["stream_id"])
        day = self.mission_day(chunk.at) if chunk.day is None else chunk.day
        record = self._generator.calibration_in_force(unit.astronaut_id, stream_id, day)
        if record is None or record.calibration_id != calibration_id:
            # Chunk of a block synthesized before a `calibrate`: it cites the previous one. It is only (re)published if
            # no other one has come out on the bus afterwards for that stream (e.g. the calibrate has not finished yet).
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
        """The envelope's calibration is still the one in force for its stream (no later `calibrate` replaced it)."""
        payload = envelope.payload
        day = self.mission_day(envelope.at) if envelope.day is None else envelope.day
        record = self._generator.calibration_in_force(unit.astronaut_id, str(payload["stream_id"]), day)
        return record is not None and record.calibration_id == payload["calibration_id"]

    async def _publish_calibration(self, client: MqttPublisher, unit: _Unit, envelope: Envelope) -> None:
        """Publishes a calibration (retained) unless it is already the retained one on its topic on this connection."""
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
        """stream_id -> last seq published in the boot in progress at `at` (for `last_seq`)."""
        epoch = unit.device.plan.epoch_at(at)
        if epoch is None:
            return {}
        return {stream_id: seq for (boot_id, stream_id), seq in unit.published.items() if boot_id == epoch.boot_id}

    async def _publish_status(self, client: MqttPublisher, unit: _Unit, now: datetime) -> None:
        # What is committed on the device is ahead of what is published (look-ahead reads): the status
        # declares only what is already on the bus.
        status = unit.device.status(unit.status_rng, now, published=self._published_seqs(unit, now))
        await self._publish(
            client, self._envelope(EnvelopeKind.DEVICE_STATUS, status, unit, now, topics.status(unit.astronaut_id, unit.device_id), retained=True)
        )

    async def _publish_clock(self, client: MqttPublisher, unit: _Unit, now: datetime) -> None:
        sync = unit.device.clock_sync(unit.clock_rng, now)
        if sync is not None:  # rebooting: there is no clock to anchor
            await self._publish(
                client, self._envelope(EnvelopeKind.CLOCK_SYNC, sync, unit, now, topics.clock(unit.astronaut_id, unit.device_id), retained=True)
            )

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
        """Publishes an ack and records it (if its command is still in the LRU) so it can be resent on a redelivery."""
        await self._publish(client, envelope)
        sent = unit.handled.get(str(envelope.payload["command_id"]))
        if sent is not None:
            sent.append(envelope)

    async def _reply(self, client: MqttPublisher, unit: _Unit, command_id: str, status: AckStatus, at: datetime, **kw: Any) -> None:
        await self._publish_ack(client, unit, self._ack(unit, command_id, status, at, **kw))

    # --- chunk sources ------------------------------------------------------------------------

    async def _drain(self, client: MqttPublisher, unit: _Unit, source: _Source, until: datetime) -> datetime:
        """Publishes the chunks with `received_at` <= `until`; returns when the next one is due."""
        while True:
            envelope = await source.peek(until)
            if envelope.at > until:
                return envelope.at
            await self._publish_chunk(client, unit, envelope)
            source.take()  # only after publishing: if it fails, it is retried on the next connection

    async def _flush(self, client: MqttPublisher, unit: _Unit, source: _Source) -> datetime | None:
        """Publishes what the device already committed in `source` (see the module docstring). Returns the
        last `received_at` published (None if nothing)."""
        last: datetime | None = None
        pulls = 0
        # A loss right at the cut forces reading one more chunk of that stream; the cap only protects
        # against a loop if something does not add up.
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
            await source.peek()  # reads one more (in a thread)
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
        """Pauses the background; returns from when the next source can start."""
        source = unit.background
        if source is None:
            return now
        # Flush first and then release: if the broker goes down midway, the background stays and is flushed later.
        last = await self._flush(client, unit, source)
        unit.background = None
        return now if last is None else max(now, last)

    def _session_streams(self, command: StartSessionCommand) -> tuple[str, ...]:
        """The streams `live_session` will use (same rule)."""
        if command.stream_ids:
            return tuple(command.stream_ids)
        protocol = self._config.protocols.get(command.protocol_id)
        if protocol is not None:
            return tuple(protocol.streams)
        return tuple(s.stream_id for s in self._config.device.streams)

    async def _end_session(self, client: MqttPublisher, unit: _Unit, now: datetime) -> None:
        """Ends the current session: flushes, `completed` of the start_session and resumes the background."""
        session = unit.session
        assert session is not None
        # Flush before releasing the session (see `_stop_background`); the `completed` last: if it does not go out,
        # the state is already consistent (background running) and only that ack is lost.
        last = await self._flush(client, unit, session.source)
        unit.session = None
        self._start_background(unit, now if last is None else max(now, last), self.mission_day(now))
        await self._reply(client, unit, session.command_id, "completed", now)

    # --- commands ---------------------------------------------------------------------------------

    async def handle(self, client: MqttClient, topic: str, payload: bytes) -> None:
        """Processes a command received on a `.../cmd` topic. Never raises because of a bad command."""
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
                except (ValueError, RecursionError):  # ValueError covers UnicodeDecodeError and JSONDecodeError
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
            if previous:  # redelivery (DUP): same acks, without running again
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
            except Exception:  # noqa: BLE001  (a command never brings the gateway down)
                _log.exception("%s: command %s failed", unit.astronaut_id, command_id)
                with contextlib.suppress(Exception):
                    await self._reply(client, unit, command_id, "failed", now, code="internal", message="internal gateway error")
            if not unit.handled.get(command_id, True):  # no ack went out: a redelivery runs again
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
        # `not <= cap` also rejects NaN/inf (json.loads accepts 1e999); validated before touching state.
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
        # `accepted` first: if publishing it fails nothing has been touched (neither session nor background) and the state
        # stays consistent with the `failed` the client will see.
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
        except Exception:  # noqa: BLE001  (the session is already accepted and running; the periodic ClockSync restores it)
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
        # The generator already treats them as in force (`calibration_in_force`): the next block of the session or
        # background cites them, even before `done`, and `_ensure_calibration` publishes them before their first chunk.
        await self._reply(client, unit, cid, "accepted", now)
        completed = self._ack(unit, cid, "completed", done, calibration_id=calibrations[0].payload["calibration_id"])
        # The (retained) calibrations before the `completed`: whoever reads the ack can already resolve its id.
        if done <= now:
            for calibration in calibrations:
                await self._publish_calibration(client, unit, calibration)
            await self._publish_ack(client, unit, completed)
        else:
            unit.pending += [(done, calibration) for calibration in calibrations]
            unit.pending.append((done, completed))

    # --- loop ------------------------------------------------------------------------------------

    async def tick(self, client: MqttPublisher) -> datetime:
        """Publishes everything due up to now (chunks, deferred acks, ClockSync, DeviceStatus) and
        returns the instant of the next thing."""
        async with self._lock:
            now = self._now()
            return min([await self._tick_unit(client, unit, now) for unit in self._units])

    async def _settle(self, unit: _Unit) -> None:
        """Collects reads that a cancellation left in flight: afterwards no thread touches this device."""
        for source in unit.sources():
            await source.settle()

    async def _tick_unit(self, client: MqttPublisher, unit: _Unit, now: datetime) -> datetime:
        await self._settle(unit)
        due: list[datetime] = []
        # sorted is stable: calibrations before their `completed`. Each one leaves `pending` when published.
        for item in sorted((p for p in unit.pending if p[0] <= now), key=lambda p: p[0]):
            envelope = item[1]
            if envelope.kind is EnvelopeKind.CALIBRATION:
                if self._still_in_force(unit, envelope):  # a later `calibrate` of the stream already replaced it: not published
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
        """No session: recharges the device if it exhausted its plan and keeps the background on the current mission day."""
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
        """Maximum loop wait (s): one chunk of the shortest active source (each device's session
        or, without one, its background; with neither, a session chunk)."""
        chunks = [
            unit.session.source.chunk if unit.session is not None else unit.background.chunk if unit.background is not None else self._session_chunk
            for unit in self._units
        ]
        return min(chunks).total_seconds()

    async def _wait(self, seconds: float) -> None:
        """Waits `seconds` with `sleep` (injectable) or until a command wakes the loop (`_wake`)."""
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
        # At most one chunk of the active source of waiting, and a command cuts it short: a session that starts
        # while the background (longer chunks) sleeps publishes right away.
        while True:
            due = await self.tick(client)
            await self._wait(min(self._step(), max(0.0, (due - self._now()).total_seconds())))

    async def _shutdown(self, client: MqttPublisher) -> None:
        """Clean stop: retained `offline` DeviceStatus (the Last Will only fires if the connection is lost)."""
        now = self._now()
        for unit in self._units:
            status = self._offline_status(unit.member, now, battery_pct=unit.device.battery_pct(now))
            try:
                await self._publish(
                    client, self._envelope(EnvelopeKind.DEVICE_STATUS, status, unit, now, topics.status(unit.astronaut_id, unit.device_id), retained=True)
                )
            except Exception:  # noqa: BLE001  (the broker may already be gone)
                _log.warning("%s: could not publish the offline status on shutdown", unit.astronaut_id, exc_info=True)

    async def _announce(self, client: MqttPublisher) -> None:
        """New connection (also after reconnecting): anchor and status first of all (the Last Will may have left
        `offline`) and the calibrations are republished before their next chunk (the broker may have
        lost its retained ones). Session, background and seq stay as they were."""
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
        """One connection: subscribes to `.../cmd`, publishes the initial status and serves commands and streaming.

        Raises MqttError if the connection is lost (the gateway is left ready for another `run` with a new
        client, see `_serve`). If it is cancelled (SIGTERM) or fails for another reason, it first publishes `offline`.
        """
        try:
            for topic in self._by_cmd_topic:
                await client.subscribe(topic, qos=QOS)
            await self._announce(client)
            tasks = [asyncio.create_task(self._listen(client)), asyncio.create_task(self._pump(client))]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                # Both stops before continuing: neither a chunk nor an `ok` status after the `offline`. If the
                # pump is cut mid-read, `_Source.settle` collects it on the next connection.
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            for task in done:
                task.result()
            raise aiomqtt.MqttError("the message stream ended")
        except aiomqtt.MqttError:
            raise  # connection lost: the Last Will already notifies and there is nothing to publish through
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
    """One MQTT connection per device (each with its Last Will) and one `FakeGateway` per device, which
    survives reconnections (`_connection`). `sleep` also times the backoff waits."""
    config: MissionConfig = generator.config
    _log.info(
        "gateway for %s on %s:%d (day %d, speed %s)",
        [m.astronaut_id for m in config.crew],
        settings.mqtt_host,
        settings.mqtt_port,
        settings.start_day,
        settings.speed,
    )
    gateways = [(m, FakeGateway(generator, settings, clock=clock, sleep=sleep, astronaut_ids=[m.astronaut_id])) for m in config.crew]
    async with asyncio.TaskGroup() as group:
        for member, gateway in gateways:
            group.create_task(_connection(gateway, member, settings, client_factory=client_factory, sleep=sleep))


def _tls_params(settings: GatewaySettings) -> aiomqtt.TLSParameters | None:
    if not settings.mqtt_tls:
        return None
    return aiomqtt.TLSParameters(ca_certs=str(settings.mqtt_tls_ca_file) if settings.mqtt_tls_ca_file is not None else None)


async def _connection(
    gateway: FakeGateway,
    member: CrewMember,
    settings: GatewaySettings,
    *,
    client_factory: ClientFactory,
    sleep: Callable[[float], Awaitable[object]],
) -> None:
    """Connects (with this connection's Last Will) and runs `gateway.run`; if the broker rejects the connection
    or drops it (MqttError), waits with exponential backoff (`mqtt_reconnect_*`, back to the initial one after
    connecting) and tries again on the same gateway. It only ends when cancelled (or on another error)."""
    identifier = f"{settings.mqtt_client_id}-{member.device_id}"
    username = settings.mqtt_username.replace("{device_id}", member.device_id) if settings.mqtt_username is not None else None
    password = settings.mqtt_password.get_secret_value() if settings.mqtt_password is not None else None
    tls = _tls_params(settings)
    delay = settings.mqtt_reconnect_initial_s
    while True:
        try:
            async with client_factory(
                hostname=settings.mqtt_host,
                port=settings.mqtt_port,
                identifier=identifier,
                will=gateway.last_will(member.astronaut_id),
                username=username,
                password=password,
                tls_params=tls,
            ) as client:
                _log.info("%s: connected to %s:%d as %s", member.astronaut_id, settings.mqtt_host, settings.mqtt_port, identifier)
                delay = settings.mqtt_reconnect_initial_s
                await gateway.run(client)
        except aiomqtt.MqttError as error:
            _log.warning("%s: MQTT connection refused or lost (%s); reconnecting in %.1f s", member.astronaut_id, error, delay)
        await sleep(delay)
        delay = min(delay * settings.mqtt_reconnect_factor, settings.mqtt_reconnect_max_s)


async def serve(settings: GatewaySettings) -> None:
    """Loads the YAML, connects with aiomqtt (offline Last Will, reconnection with backoff) and runs
    `FakeGateway.run`. SIGTERM (Docker) stops cleanly, like Ctrl+C, also while waiting to reconnect."""
    generator = MissionGenerator(load_config(settings.config), settings.seed)
    task = asyncio.create_task(_serve(generator, settings, client_factory=aiomqtt.Client, clock=_utc_now))
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
