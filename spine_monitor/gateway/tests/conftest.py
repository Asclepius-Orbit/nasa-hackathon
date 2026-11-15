"""Shared fixtures for the gateway tests."""

import asyncio
import contextlib
import heapq
import itertools
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import aiomqtt
import pytest
from spine_contracts import Muscle, Side, topics, validation
from spine_gateway.assembler import SAMPLES_PER_BATCH
from spine_gateway.ports import LinkDown, LinkUp, SerialEvent, SerialLine
from spine_gateway.protocol import Frame, FrameKind, Hello, Tick, encode_frame, encode_hello, encode_tick
from spine_gateway.settings import GatewaySettings


@pytest.fixture(scope="session")
def settings() -> GatewaySettings:
    """Reference hardware settings (Arduino Uno, 10 bits at 5 V, Rs 10 kΩ, MPU-6050 at ±4 g / ±500 °/s).

    All the fields used by the pure logic are explicit: init arguments win over the
    SPINE_GATEWAY_* environment variables and ``_env_file=None`` ignores the ``.env`` of the current directory. Session-scoped so it can be
    used in Hypothesis tests; whoever needs another value does ``settings.model_copy(update=...)``.
    """
    return GatewaySettings(
        _env_file=None,
        astronaut_id="AST-01",
        device_id="wb-01",
        emg_stream_id="emg_lat_left",
        imu_stream_id="imu_t9",
        stretch_stream_id="stretch_spine",
        emg_muscle=Muscle.LATISSIMUS_DORSI,
        emg_side=Side.LEFT,
        adc_bits=10,
        vref_v=5.0,
        emg_input_pin="A0",
        stretch_input_pin="A1",
        stretch_series_resistor_ohm=10000.0,
        imu_accel_range_g=4,
        imu_gyro_range_dps=500,
        imu_dlpf_hz=44,
        emg_gain=1.0,
        stretch_rest_length_mm=650.0,
        emg_mvc_window_ms=250,
    )


# --- harness for the gateway async loop (app.py): fake MQTT, serial port and time ---

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
AST, DEV = "AST-01", "wb-01"
# channel -> (schema, retained) per asyncapi.yaml; QoS 1 for all.
CHANNELS = {
    "stream": ("telemetry/stream-chunk", False),
    "status": ("telemetry/device-status", True),
    "clock": ("telemetry/clock-sync", True),
    "ack": ("telemetry/command-ack", False),
    "calibration": ("domain/calibration", True),
}
_SETTLE_YIELDS = 50
_WAIT_S = 5.0


async def settle() -> None:
    """Lets all ready tasks run until they block (yields the loop without waiting real time)."""
    for _ in range(_SETTLE_YIELDS):
        await asyncio.sleep(0)


class VirtualTime:
    """Injectable clock and ``sleep``: time only advances with ``advance``, which wakes sleepers in order."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now
        self._waiters: list[tuple[datetime, int, asyncio.Future[None]]] = []
        self._order = itertools.count()
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (self.now + timedelta(seconds=seconds), next(self._order), future))
        await future

    def pending(self) -> int:
        """How many ``sleep`` calls are still waiting (those of cancelled tasks do not count)."""
        return sum(1 for _, _, future in self._waiters if not future.done())

    async def advance(self, seconds: float) -> None:
        target = self.now + timedelta(seconds=seconds)
        while True:
            await settle()
            while self._waiters and self._waiters[0][2].done():
                heapq.heappop(self._waiters)
            if not self._waiters or self._waiters[0][0] > target:
                break
            at, _, future = heapq.heappop(self._waiters)
            self.now = max(self.now, at)
            future.set_result(None)
        self.now = target
        await settle()


class Idle:
    """Queue that signals when its consumer has drained it and waits again (everything before is already processed)."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self._idle = asyncio.Event()

    async def get(self) -> Any:
        if self.queue.empty():
            self._idle.set()
        return await self.queue.get()

    async def put(self, *items: Any) -> None:
        self._idle.clear()
        for item in items:
            self.queue.put_nowait(item)
        await asyncio.wait_for(self._idle.wait(), _WAIT_S)
        await settle()


class FakeLineSource:
    """``LineSource`` that hands out the events the test gives it."""

    def __init__(self) -> None:
        self.feed = Idle()
        self.closed = False

    def events(self) -> AsyncIterator[SerialEvent]:
        # A reference is kept: this way the garbage collector does not close it on its own and only the explicit close counts.
        self.generator = self._events()
        return self.generator

    async def _events(self) -> AsyncIterator[SerialEvent]:
        try:
            while True:
                yield await self.feed.get()
        finally:
            self.closed = True  # like the real port, which closes when the generator closes


@dataclass
class Published:
    topic: str
    payload: dict[str, Any]
    qos: int
    retain: bool

    @property
    def channel(self) -> str:
        parts = self.topic.split("/")
        return parts[-2] if parts[-2] in ("stream", "calibration") else parts[-1]


@dataclass
class Message:
    topic: str
    payload: bytes


@dataclass
class FakeClient:
    """Fake MQTT client. Each publish is checked against the contract (schema, topic, QoS, retain, compact JSON)
    and failures accumulate in ``problems`` (the gateway swallows exceptions: an assert inside would go unseen)."""

    published: list[Published] = field(default_factory=list)
    subscriptions: list[tuple[str, int]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    inbox: Idle = field(default_factory=Idle)
    fail_publish: bool = False
    drop_messages: bool = False
    # Custom failures: topic -> exception to raise on that publish (or None to publish normally).
    fault: Callable[[str], BaseException | None] | None = None

    async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        if self.fail_publish:
            raise aiomqtt.MqttError("Disconnected")
        error = self.fault(topic) if self.fault is not None else None
        if error is not None:
            raise error
        data = json.loads(payload)
        p = Published(topic, data, qos, retain)
        schema, retained = CHANNELS[p.channel]
        self.problems += [f"{topic}: {e}" for e in validation.errors(schema, data)]
        if qos != 1 or retain is not retained:
            self.problems.append(f"{topic}: qos={qos} retain={retain}")
        if payload != json.dumps(data, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode():
            self.problems.append(f"{topic}: not compact UTF-8 JSON")
        if topic != _expected_topic(p):
            self.problems.append(f"{topic}: expected {_expected_topic(p)}")
        self.published.append(p)

    async def subscribe(self, topic: str, qos: int) -> None:
        self.subscriptions.append((topic, qos))

    @property
    def messages(self) -> AsyncIterator[Message]:
        return self._messages()

    async def _messages(self) -> AsyncIterator[Message]:
        while True:
            if self.drop_messages:
                raise aiomqtt.MqttError("Disconnected during message iteration")
            message = await self.inbox.get()
            if message is None:  # connection drop requested by the test
                raise aiomqtt.MqttError("Disconnected during message iteration")
            if message == "end":  # the message iterator ends without error
                return
            yield message

    def on(self, channel: str) -> list[Published]:
        return [p for p in self.published if p.channel == channel]

    def payloads(self, channel: str) -> list[dict[str, Any]]:
        return [p.payload for p in self.on(channel)]

    def acks(self, command_id: str) -> list[dict[str, Any]]:
        return [a for a in self.payloads("ack") if a["command_id"] == command_id]

    def statuses(self) -> list[str]:
        return [s["status"] for s in self.payloads("status")]


def _expected_topic(p: Published) -> str:
    a, d = p.payload.get("astronaut_id", ""), p.payload.get("device_id", "")
    match p.channel:
        case "stream":
            return topics.stream(a, d, p.payload["stream_id"])
        case "calibration":
            return topics.calibration(a, d, p.payload["stream_id"])
        case "status":
            return topics.status(a, d)
        case "clock":
            return topics.clock(a, d)
        case _:
            return topics.ack(a, d)


class FakeConnection:
    """A connection attempt: entering yields the client, or MqttError if None (the broker rejects it)."""

    def __init__(self, client: FakeClient | None) -> None:
        self.client = client

    async def __aenter__(self) -> FakeClient:
        if self.client is None:
            raise aiomqtt.MqttError("[code:111] Connection refused")
        return self.client

    async def __aexit__(self, *exc: object) -> None:
        return None


class Connections:
    """``client_factory``: records each attempt (kwargs) and returns the client that ``plan(attempt)`` says (None = rejection)."""

    def __init__(self, plan: Callable[[int], FakeClient | None] | None = None) -> None:
        self.plan = plan or (lambda n: FakeClient())
        self.attempts: list[dict[str, Any]] = []
        self.clients: list[FakeClient] = []
        self.clock: Callable[[], datetime] | None = None  # set by the harness: each attempt records its time in "at"

    def __call__(self, **kw: Any) -> FakeConnection:
        self.attempts.append({**kw, "at": self.clock() if self.clock is not None else None})
        client = self.plan(len(self.attempts))
        if client is not None:
            self.clients.append(client)
        return FakeConnection(client)


def calibration_ids() -> Callable[[], str]:
    counter = itertools.count(1)
    return lambda: f"ca11b000-0000-4000-8000-{next(counter):012d}"


class Harness:
    """Gateway with everything injected. ``async with`` starts it (``Gateway.run``) and on exit cancels it and checks that
    everything published meets the contract. The device emits one batch of each kind every 100 ms (``run_device``)."""

    def __init__(self, settings: GatewaySettings, connections: Connections | None = None) -> None:
        from spine_gateway.app import Gateway  # late import: the rest of the tests do not depend on app.py

        self.settings = settings
        self.time = VirtualTime()
        self.source = FakeLineSource()
        self.connections = connections or Connections()
        self.connections.clock = self.time
        self.gateway = Gateway(settings, clock=self.time, sleep=self.time.sleep, new_id=calibration_ids())
        self.boot_id = 7
        self.device_ms = 0
        self.seq = dict.fromkeys(FrameKind, 0)
        self.values: dict[FrameKind, Any] = {FrameKind.EMG: 300, FrameKind.IMU: (0, 0, 8192, 0, 0, 0), FrameKind.CORD: 512}
        self._task: asyncio.Task[None] | None = None

    @property
    def client(self) -> FakeClient:
        return self.connections.clients[-1]

    async def __aenter__(self) -> Harness:
        self._task = asyncio.create_task(self.gateway.run(self.source, self.connections))
        await settle()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def stop(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, _WAIT_S)
        if task is not None and task.done() and not task.cancelled():
            task.result()  # a loop failure must not go unnoticed
        for client in self.connections.clients:
            assert client.problems == []

    # --- device ---

    def line(self, text: str | bytes) -> SerialLine:
        return SerialLine(text.encode("ascii") if isinstance(text, str) else text, self.time.now)

    async def feed(self, *events: SerialEvent) -> None:
        await self.source.feed.put(*events)

    async def link_up(self) -> None:
        await self.feed(LinkUp(self.time.now))

    async def link_down(self, reason: str = "SerialException: device disconnected") -> None:
        await self.feed(LinkDown(self.time.now, reason))

    async def hello(self, boot_id: int | None = None, firmware: str = "fw-1.0.0") -> None:
        if boot_id is not None and boot_id != self.boot_id:
            self.reboot(boot_id)
        await self.feed(self.line(encode_hello(Hello(1, self.boot_id, firmware, self.device_ms))))

    async def tick(self) -> None:
        await self.feed(self.line(encode_tick(Tick(self.boot_id, self.device_ms))))

    def reboot(self, boot_id: int) -> None:
        self.boot_id, self.device_ms = boot_id, 0
        self.seq = dict.fromkeys(FrameKind, 0)

    def frame_line(self, kind: FrameKind, *, seq: int | None = None, value: Any = None) -> SerialLine:
        n = SAMPLES_PER_BATCH[kind]
        sample = self.values[kind] if value is None else value
        frame = Frame(kind, self.seq[kind] if seq is None else seq, self.device_ms, tuple([sample] * n))
        if seq is None:
            self.seq[kind] = (self.seq[kind] + 1) % 0x10000
        return self.line(encode_frame(frame))

    async def frames(self, *kinds: FrameKind) -> None:
        await self.feed(*(self.frame_line(k) for k in kinds))

    async def boot(self, boot_id: int | None = None) -> None:
        """Link up, hello and first tick, as when opening the port of an Uno."""
        await self.link_up()
        await self.hello(boot_id)
        await self.tick()

    async def run_device(self, seconds: float, kinds: tuple[FrameKind, ...] = tuple(FrameKind)) -> None:
        """Batches of ``kinds`` every 100 ms and a tick every device second, with time advancing in step."""
        for _ in range(round(seconds * 10)):
            await self.time.advance(0.1)
            self.device_ms += 100
            if kinds:
                await self.frames(*kinds)
            if self.device_ms % 1000 == 0:
                await self.tick()

    # --- MQTT ---

    async def send(self, payload: dict[str, Any] | bytes, client: FakeClient | None = None) -> None:
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        await (client or self.client).inbox.put(Message(topics.cmd(AST, DEV), raw))

    async def disconnect(self) -> None:
        """The broker drops the current connection (message reception fails)."""
        self.client.inbox.queue.put_nowait(None)
        await settle()


@pytest.fixture
def app_settings(settings: GatewaySettings) -> GatewaySettings:
    """``settings`` with explicit loop timings (so no environment variable changes them)."""
    return settings.model_copy(
        update={
            "mqtt_host": "broker.test", "mqtt_port": 1884, "mqtt_client_id": "spine-gateway",
            "mqtt_reconnect_initial_s": 1.0, "mqtt_reconnect_factor": 2.0, "mqtt_reconnect_max_s": 30.0,
            "status_interval_s": 30.0, "clock_sync_interval_s": 60.0, "clock_uncertainty_ms": 20.0,
            "stall_timeout_s": 2.0, "calibration_default_duration_s": 5.0,
        }
    )  # fmt: skip


@pytest.fixture
def fakes() -> SimpleNamespace:
    """The harness fake classes, for tests that build their own connections or messages."""
    return SimpleNamespace(Connections=Connections, FakeClient=FakeClient, Message=Message, FakeLineSource=FakeLineSource)


@pytest.fixture
def harness(app_settings: GatewaySettings) -> Callable[..., Harness]:
    def make(connections: Connections | None = None, **overrides: Any) -> Harness:
        return Harness(app_settings.model_copy(update=overrides), connections)

    return make
