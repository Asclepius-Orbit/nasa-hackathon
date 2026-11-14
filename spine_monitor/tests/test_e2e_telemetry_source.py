"""Telemetry source end to end (#130): the backend against a real broker, fed by the simulator or by the device gateway.

Real in every test: the Mosquitto broker on 127.0.0.1:1883, the backend (`monitor.main.create_app`, its production MQTT
client with a persistent-session-free identifier), the engine (`create_default_app`, over ASGI) and ONE telemetry
producer, either the physiology simulator gateway (`source: simulator`) or `spine-gateway` reading the Arduino emulator
over `socket://` (`source: device`). Nothing is replaced but the wall clock of the physiology gateway (it uses the real
one), so each scenario runs at real time: the sessions are a few seconds long and are stopped by the test.

An observer on the bus (MQTT 5 with retain-as-published, so a live message keeps the publisher's retain flag) checks the
contract of everything the producer sends: full schema, ids of the topic, QoS 1, the `source` of the mode, retained
status / clock / calibration and not retained stream / ack. The backend's own commands are recorded at its MQTT client
(QoS 1, no retain). A backend configured for the other mode must store nothing of the producer (`wrong_source`).

Already covered elsewhere, so not repeated here: the ingest guard with in-memory messages (`backend/tests/test_ingest_source.py`),
the gateway emulator chain without a backend (`gateway/tests/test_e2e_emulator_mosquitto.py`), the simulator gateway without a
backend (`physiology/tests/test_gateway_mosquitto.py`) and the full chain with an in-memory bus (`tests/test_e2e_real_engine.py`).

Skipped without a broker, unless SPINE_REQUIRE_BROKER=1 (the Docker gate), where it fails. Device `itest-01` of AST-01
(user = device id, SPINE_TEST_MQTT_DEVICE_PASSWORD), shared by both producers, one at a time; the retained messages they leave are cleared at the end.
"""

import asyncio
import contextlib
import json
import logging
import os
import socket
import sys
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiomqtt
import httpx
import pytest
import spine_physiology
import yaml
from monitor.main import create_app, default_mqtt_client_factory
from monitor.settings import AppSettings
from paho.mqtt.subscribeoptions import SubscribeOptions
from pydantic import SecretStr
from spine_contracts import topics, validation
from spine_engine.api import create_default_app
from spine_engine.settings import EngineSettings
from spine_gateway import app as device_app
from spine_gateway import emulator
from spine_gateway.settings import GatewaySettings as DeviceSettings
from spine_physiology import MissionGenerator
from spine_physiology.config import MissionConfig
from spine_physiology.gateway.app import _serve as serve_simulator
from spine_physiology.gateway.settings import GatewaySettings as SimulatorSettings

pytestmark = [pytest.mark.integration, pytest.mark.mosquitto]

HOST, PORT = "127.0.0.1", 1883
TIMEOUT_S = 40.0
ASTRONAUT = "AST-01"
DEVICE, ABSENT_DEVICE = "itest-01", "itest-09"  # itest-01 is the device user every broker has (itest-02 is AST-02); one producer at a time
UI_TOKEN = "e2e-source-token"
UI_HEADERS = {"Authorization": f"Bearer {UI_TOKEN}", "X-Astronaut-Id": ASTRONAUT}
EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples" / "valid"
MISSION = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
STREAMS = ("emg_lat_left", "imu_t9", "stretch_spine")  # what both producers have
MVC_STREAM = "emg_lat_left"
OTHER = {"simulator": "device", "device": "simulator"}
SCHEMAS = {
    "stream": "telemetry/stream-chunk",
    "calibration": "domain/calibration",
    "status": "telemetry/device-status",
    "clock": "telemetry/clock-sync",
    "ack": "telemetry/command-ack",
}
RETAINED = {"status", "clock", "calibration"}
SOURCED = {"stream", "status", "calibration"}  # the kinds whose payload carries `source`
Observed = tuple[str, dict[str, Any], int, bool]

OBSERVER_PASSWORD = os.environ.get("SPINE_TEST_MQTT_PASSWORD") or None
OBSERVER_USER = os.environ.get("SPINE_TEST_MQTT_USERNAME", "spine-itest") if OBSERVER_PASSWORD else None
DEVICE_PASSWORD = os.environ.get("SPINE_TEST_MQTT_DEVICE_PASSWORD") or None


def _require_broker() -> None:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return
    except OSError:
        pass
    reason = f"no MQTT broker on {HOST}:{PORT}"
    if os.environ.get("SPINE_REQUIRE_BROKER") == "1":
        pytest.fail(f"SPINE_REQUIRE_BROKER=1 but {reason}: the mosquitto test must not be skipped in the gate")
    pytest.skip(reason)


def run(scenario: Coroutine[Any, Any, None]) -> None:
    _require_broker()
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None  # paho needs add_reader
    asyncio.run(scenario, loop_factory=loop_factory)


async def until(predicate: Callable[[], bool], what: str, timeout: float = TIMEOUT_S) -> None:
    """Polls the state of the system under test (store, observer) until it holds; a tick, not a fixed wait."""

    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.02)

    try:
        await asyncio.wait_for(wait(), timeout)
    except TimeoutError:
        raise AssertionError(f"timed out after {timeout} s waiting for {what}") from None


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def session_plan(device_id: str) -> dict[str, Any]:
    session = example("domain/session.exercise")
    plan = {k: session[k] for k in ("session_type", "protocol_id", "load_declared", "planned_reps", "planned_sets", "reps_per_set")}
    return {**plan, "device_id": device_id, "streams": [s for s in session["streams"] if s["stream_id"] in STREAMS]}


# --- the broker side: observer and recording client -----------------------------------------------------------------


class Observer:
    """Everything one device publishes, with the retain flag as published (MQTT 5, retain-as-published)."""

    def __init__(self, device_id: str, mode: str | None = None) -> None:
        self.device_id, self.mode = device_id, mode
        self.root = f"{topics.ROOT}/crew/{ASTRONAUT}/device/{device_id}"
        self.seen: list[Observed] = []
        self._stack = contextlib.AsyncExitStack()
        self._consumer: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Observer:
        client = await self._stack.enter_async_context(
            aiomqtt.Client(
                HOST,
                PORT,
                identifier=f"itest-obs-{uuid.uuid4().hex[:8]}",
                protocol=aiomqtt.ProtocolVersion.V5,
                username=OBSERVER_USER,
                password=OBSERVER_PASSWORD,
            )
        )
        for suffix in ("stream/+", "status", "ack", "clock", "calibration/+"):
            await client.subscribe(f"{self.root}/{suffix}", options=SubscribeOptions(qos=1, retainAsPublished=True))
        self._consumer = asyncio.create_task(self._consume(client))
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._consumer is not None:
            self._consumer.cancel()
            await asyncio.gather(self._consumer, return_exceptions=True)
        await self._stack.aclose()

    async def _consume(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            payload = message.payload if isinstance(message.payload, bytes | bytearray) else b""
            self.seen.append((str(message.topic), json.loads(payload) if payload else {}, message.qos, message.retain))

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [p for t, p, _, _ in self.seen if p and _kind(t) == kind]

    def contract_problems(self) -> list[str]:
        """Schema, ids of the topic, QoS 1, retain exactly where asyncapi.yaml says and `source` where it exists."""
        problems: list[str] = []
        for topic, payload, qos, retain in self.seen:
            if not payload:  # a retained message being cleared
                continue
            kind = _kind(topic)
            problems += [f"{topic}: {e}" for e in validation.errors(SCHEMAS[kind], payload)]
            if topic != _topic_of(kind, payload):
                problems.append(f"{topic}: the ids of the payload do not match the topic")
            if qos != 1:
                problems.append(f"{topic}: QoS {qos}")
            if retain != (kind in RETAINED):
                problems.append(f"{topic}: retain={retain} for a {kind}")
            if kind in SOURCED and self.mode is not None and payload.get("source") != self.mode:
                problems.append(f"{topic}: source {payload.get('source')!r}, expected {self.mode!r}")
            if kind not in SOURCED and "source" in payload:
                problems.append(f"{topic}: a {kind} must not carry a source")
        return problems


def _kind(topic: str) -> str:
    parts = topic.split("/")
    return parts[-2] if parts[-2] in ("stream", "calibration") else parts[-1]


def _topic_of(kind: str, payload: dict[str, Any]) -> str:
    a, d = payload["astronaut_id"], payload["device_id"]
    match kind:
        case "stream":
            return topics.stream(a, d, payload["stream_id"])
        case "calibration":
            return topics.calibration(a, d, payload["stream_id"])
        case "status":
            return topics.status(a, d)
        case "clock":
            return topics.clock(a, d)
        case _:
            return topics.ack(a, d)


class RecordingClient(aiomqtt.Client):
    """The backend's production client with a clean session (no residue in the shared broker) that notes what it publishes."""

    published: list[tuple[str, int, bool]] = []  # noqa: RUF012  (shared by the clients of one backend: reset in Backend)

    async def publish(self, topic: str, payload: Any = None, qos: int = 0, retain: bool = False, **kwargs: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        RecordingClient.published.append((topic, qos, retain))
        await super().publish(topic, payload, qos, retain, **kwargs)


class DroppableClient(aiomqtt.Client):
    """A producer's MQTT client whose connection can be cut without a DISCONNECT, so the broker publishes the Last Will."""

    def drop(self) -> None:
        sock = self._client.socket()  # pyright: ignore[reportPrivateUsage]
        assert isinstance(sock, socket.socket)
        sock.shutdown(socket.SHUT_RDWR)


async def clear_retained(device_id: str, stream_ids: tuple[str, ...]) -> None:
    """Only the device itself may write on its topics: leaves the shared broker as it was."""
    async with aiomqtt.Client(
        HOST, PORT, identifier=f"itest-clean-{uuid.uuid4().hex[:8]}", username=device_id if DEVICE_PASSWORD else None, password=DEVICE_PASSWORD
    ) as client:
        for topic in (
            topics.status(ASTRONAUT, device_id),
            topics.clock(ASTRONAUT, device_id),
            *(topics.calibration(ASTRONAUT, device_id, s) for s in stream_ids),
        ):
            await client.publish(topic, payload=b"", qos=1, retain=True)


# --- the backend ----------------------------------------------------------------------------------------------------


class Backend:
    """The real backend wired to the real broker and to the real engine, driven through its UI API like the app does."""

    def __init__(self, tmp_path: Path, source: str, *, ack_timeout_s: float = 20.0) -> None:
        self.source, self._tmp, self._ack_timeout_s = source, tmp_path, ack_timeout_s
        self._bus: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Backend:
        RecordingClient.published = []
        settings = AppSettings(
            _env_file=None, db_path=self._tmp / "app.db", ui_token=UI_TOKEN, telemetry_source=self.source,
            ack_timeout_s=self._ack_timeout_s, reassess_debounce_s=0.0, mqtt_host=HOST, mqtt_port=PORT,
            mqtt_client_id=f"itest-backend-{uuid.uuid4().hex[:8]}", mqtt_username=OBSERVER_USER,
            mqtt_password=SecretStr(OBSERVER_PASSWORD) if OBSERVER_PASSWORD else None,
        )  # fmt: skip
        factory = default_mqtt_client_factory(settings, client=lambda *a, **kw: RecordingClient(*a, **{**kw, "clean_session": True}))
        engine = create_default_app(EngineSettings(_env_file=None))
        self.app = create_app(settings, mqtt_client_factory=factory, engine_transport=httpx.ASGITransport(app=engine))
        self.runtime = self.app.state.runtime
        self._bus = asyncio.create_task(self.runtime.bus.run(self.runtime.ingest))
        await asyncio.wait_for(self.runtime.bus.connected.wait(), TIMEOUT_S)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://app", headers=UI_HEADERS)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.http.aclose()
        assert self._bus is not None
        self._bus.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._bus
        await self.runtime.control.idle()
        self.runtime.control.close()
        self.runtime.store.close()

    @property
    def store(self) -> Any:
        return self.runtime.store

    @property
    def counts(self) -> dict[str, int]:
        return self.runtime.ingest.counts

    async def onboard(self) -> None:
        assert (await self.http.put(f"/ui/crew/{ASTRONAUT}/profile", json=example("domain/astronaut-profile"))).status_code == 200
        day = datetime.now(UTC).date().isoformat()
        context = {"mission_day": -30, "phase": "preflight", "gravity_environment": "earth_1g"}
        assert (await self.http.put(f"/ui/crew/{ASTRONAUT}/daily-context/{day}", json=context)).status_code == 200

    def device_status(self, device_id: str) -> dict[str, Any] | None:
        return self.store.device_status(ASTRONAUT, device_id)

    async def online(self, device_id: str) -> None:
        await until(lambda: (s := self.device_status(device_id)) is not None and s["status"] == "ok", f"{device_id} online in the backend")

    async def post(self, path: str, body: dict[str, Any], status: int = 202) -> dict[str, Any]:
        response = await self.http.post(path, json=body)
        assert response.status_code == status, response.text
        return response.json()

    async def command_state(self, command_id: str) -> str:
        return (await self.http.get(f"/ui/commands/{command_id}")).json()["state"]

    async def command_reaches(self, command_id: str, wanted: str) -> None:
        state = ""

        async def wait() -> None:
            nonlocal state
            while (state := await self.command_state(command_id)) != wanted:
                await asyncio.sleep(0.02)

        try:
            await asyncio.wait_for(wait(), TIMEOUT_S)
        except TimeoutError:
            raise AssertionError(f"command {command_id} is {state!r}, expected {wanted!r}") from None

    async def session(self, session_id: str) -> dict[str, Any]:
        return (await self.http.get(f"/ui/sessions/{session_id}")).json()

    async def session_reaches(self, session_id: str, states: set[str]) -> dict[str, Any]:
        body: dict[str, Any] = {}

        async def wait() -> None:
            nonlocal body
            while (body := await self.session(session_id))["state"] not in states:
                await asyncio.sleep(0.02)

        try:
            await asyncio.wait_for(wait(), TIMEOUT_S)
        except TimeoutError:
            raise AssertionError(f"session {session_id} is {body.get('state')!r} ({body.get('error')}), expected one of {sorted(states)}") from None
        return body

    def chunks(self, session_id: str, stream_id: str | None = None) -> list[dict[str, Any]]:
        return [c for c in self.store.session_chunks(ASTRONAUT, session_id) if stream_id in (None, c["stream_id"])]

    def commands_published(self) -> list[tuple[str, int, bool]]:
        return [p for p in RecordingClient.published if p[0].endswith("/cmd")]


# --- the producers ----------------------------------------------------------------------------------------------------


def simulator_mission() -> MissionConfig:
    """The example mission with only AST-01, on the test device, and no random faults (they would only add noise)."""
    raw = yaml.safe_load(MISSION.read_text(encoding="utf-8"))
    raw["crew"] = [{**raw["crew"][0], "device_id": DEVICE}]
    for fault in raw["faults"]["quality"].values():
        fault["probability"] = 0.0
    raw["faults"]["device"]["packet_loss"]["probability"] = 0.0
    raw["faults"]["device"]["reboot"]["probability_per_hour"] = 0.0
    return MissionConfig.model_validate(raw)


class SimulatorProducer:
    """The physiology gateway (`FakeGateway`) on the real broker; `drop()` cuts its connection to trigger the Last Will."""

    device_id, stream_ids = DEVICE, ("emg_lat_left", "emg_lat_right", "imu_t9", "stretch_spine")

    def __init__(self, *, reconnect_s: float = 1.0) -> None:
        self.reconnect_s = reconnect_s
        self.clients: list[DroppableClient] = []
        self._task: asyncio.Task[None] | None = None

    def _client(self, **kwargs: Any) -> DroppableClient:
        client = DroppableClient(**kwargs)
        self.clients.append(client)
        return client

    async def __aenter__(self) -> SimulatorProducer:
        config = simulator_mission()
        settings = SimulatorSettings(
            _env_file=None, config=MISSION, speed=0.0, start_day=4, mqtt_host=HOST, mqtt_port=PORT, mqtt_client_id=f"itest-sim-{uuid.uuid4().hex[:8]}",
            mqtt_username="{device_id}" if DEVICE_PASSWORD else None, mqtt_password=SecretStr(DEVICE_PASSWORD) if DEVICE_PASSWORD else None,
            mqtt_reconnect_initial_s=self.reconnect_s, mqtt_reconnect_max_s=max(self.reconnect_s, 30.0),
        )  # fmt: skip
        self._task = asyncio.create_task(serve_simulator(MissionGenerator(config), settings, client_factory=self._client, clock=lambda: datetime.now(UTC)))
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._task is not None
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(self._task, TIMEOUT_S)
        await clear_retained(self.device_id, self.stream_ids)

    def drop(self) -> None:
        self.clients[-1].drop()


class DeviceProducer:
    """`spine-gateway` reading the Arduino emulator over `socket://`, on the real broker."""

    device_id, stream_ids = DEVICE, ("emg_lat_left", "imu_t9", "stretch_spine")

    def __init__(self, *, reconnect_s: float = 1.0) -> None:
        self.reconnect_s = reconnect_s
        self.clients: list[DroppableClient] = []
        self._tasks: list[asyncio.Task[None]] = []

    def _client(self, **kwargs: Any) -> DroppableClient:
        client = DroppableClient(**kwargs)
        self.clients.append(client)
        return client

    async def __aenter__(self) -> DeviceProducer:
        ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._tasks.append(asyncio.create_task(emulator.serve("127.0.0.1", 0, on_listening=ready.set_result, seed=7)))
        port = await asyncio.wait_for(ready, TIMEOUT_S)
        settings = DeviceSettings(
            _env_file=None, astronaut_id=ASTRONAUT, device_id=self.device_id, serial_url=f"socket://127.0.0.1:{port}", mqtt_host=HOST, mqtt_port=PORT,
            mqtt_client_id=f"itest-dev-{uuid.uuid4().hex[:8]}", mqtt_username=self.device_id if DEVICE_PASSWORD else None,
            mqtt_password=SecretStr(DEVICE_PASSWORD) if DEVICE_PASSWORD else None, mqtt_reconnect_initial_s=self.reconnect_s, serial_reconnect_initial_s=0.2,
        )  # fmt: skip
        self.settings = settings
        self._tasks.append(
            asyncio.create_task(device_app.serve(settings, source=device_app.build_source(settings), client_factory=self._client, clock=device_app.utc_now))
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        for task in reversed(self._tasks):  # the gateway first: it publishes `offline` before leaving
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, TIMEOUT_S)
        await clear_retained(self.device_id, self.stream_ids)

    def drop(self) -> None:
        self.clients[-1].drop()


type Producer = SimulatorProducer | DeviceProducer


def producer_for(mode: str, **options: Any) -> Producer:
    return SimulatorProducer(**options) if mode == "simulator" else DeviceProducer(**options)


# --- scenarios ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["simulator", "device"])
def test_a_full_session_cycle_reaches_the_engine_in_each_mode(tmp_path: Path, mode: str) -> None:
    """start -> cmd -> ack -> chunks -> event -> MVC calibration -> stop -> processed by the real engine, with the contract of the bus."""

    async def scenario() -> None:
        device = DEVICE
        async with Observer(device, mode) as observer, Backend(tmp_path, mode) as backend, producer_for(mode):
            await backend.onboard()
            await backend.online(device)
            status = backend.device_status(device)
            assert status is not None and status["source"] == mode

            started = await backend.post(f"/ui/crew/{ASTRONAUT}/sessions", session_plan(device))
            session_id, start_id = started["session"]["session_id"], started["command"]["command_id"]
            assert started["session"]["source"] == mode
            await backend.session_reaches(session_id, {"running"})
            await backend.command_reaches(start_id, "accepted")
            await until(lambda: all(len(backend.chunks(session_id, s)) >= 5 for s in STREAMS), "5 stored chunks of every stream")

            artifact = await backend.post(f"/ui/sessions/{session_id}/events", {"event_type": "artifact", "note": "electrode moved"})
            await backend.command_reaches(artifact["command"]["command_id"], "completed")

            mvc = await backend.post(f"/ui/sessions/{session_id}/calibrations", {"calibration_type": "emg_mvc", "stream_ids": [MVC_STREAM], "duration_s": 1})
            await backend.command_reaches(mvc["command"]["command_id"], "completed")
            ack = (await backend.http.get(f"/ui/commands/{mvc['command']['command_id']}")).json()["acks"][-1]
            calibration_id = ack["result"]["calibration_id"]
            await until(lambda: bool(backend.store.calibrations({calibration_id})), "the MVC calibration stored")
            [calibration] = backend.store.calibrations({calibration_id})
            assert (calibration["calibration_type"], calibration["stream_id"], calibration["source"]) == ("emg_mvc", MVC_STREAM, mode)

            stopped = await backend.post(f"/ui/sessions/{session_id}/stop", {"reason": "completed"})
            await backend.command_reaches(stopped["command"]["command_id"], "completed")
            body = await backend.session_reaches(session_id, {"processed", "processing_failed"})
            assert body["state"] == "processed", body["error"]
            assert body["feature_set"]["session_id"] == session_id
            assert [e["event_type"] for e in body["session"]["events"]] == ["artifact"]
            assert body["session"]["end_reason"] == "completed"
            assert {c["source"] for c in backend.chunks(session_id)} == {mode}

            commands = backend.commands_published()
            assert len(commands) >= 4 and {(q, r) for _, q, r in commands} == {(1, False)}  # start, event, calibrate, stop
            await until(lambda: any(p["status"] == "completed" for p in observer.of("ack")), "the acks on the bus")
            assert observer.contract_problems() == []

    run(scenario())


@pytest.mark.parametrize("mode", ["simulator", "device"])
def test_a_command_nobody_answers_times_out_and_rejects_the_session(tmp_path: Path, mode: str) -> None:
    async def scenario() -> None:
        async with Backend(tmp_path, mode, ack_timeout_s=0.5) as backend:
            await backend.onboard()
            started = await backend.post(f"/ui/crew/{ASTRONAUT}/sessions", session_plan(ABSENT_DEVICE))
            session_id, command_id = started["session"]["session_id"], started["command"]["command_id"]
            assert started["state"] == "starting"
            assert started["session"]["source"] == mode  # no status of the device yet: the session takes the installation's source
            await backend.command_reaches(command_id, "timeout")
            body = await backend.session_reaches(session_id, {"rejected"})
            assert body["error"] == "ack_timeout"
            assert (await backend.http.get(f"/ui/commands/{command_id}")).json()["acks"] == []
            assert [(q, r) for _, q, r in backend.commands_published()] == [(1, False)]

    run(scenario())


@pytest.mark.parametrize("mode", ["simulator", "device"])
def test_a_device_that_drops_leaves_the_last_will_and_the_backend_refuses_commands(tmp_path: Path, mode: str) -> None:
    """The connection is cut without DISCONNECT: the broker publishes the retained `offline`; the producer reconnects and is back `ok`."""

    async def scenario() -> None:
        device = DEVICE
        async with Observer(device, mode) as observer, Backend(tmp_path, mode) as backend, producer_for(mode, reconnect_s=2.0) as producer:
            await backend.onboard()
            await backend.online(device)
            producer.drop()
            await until(lambda: (s := backend.device_status(device)) is not None and s["status"] == "offline", "the offline status in the backend")
            offline = backend.device_status(device)
            assert offline is not None and offline["source"] == mode
            refused = await backend.http.post(f"/ui/crew/{ASTRONAUT}/sessions", json=session_plan(device))
            assert refused.status_code in (409, 503), refused.text
            assert "offline" in refused.text
            assert [(q, r) for _, q, r in backend.commands_published()] == []  # nothing was sent to a device that is gone

            await backend.online(device)  # the producer reconnected and announced itself again
            will = [p for p in observer.of("status") if p["status"] == "offline"]
            assert will and all(p["source"] == mode for p in will)
            assert observer.contract_problems() == []

    run(scenario())


@pytest.mark.parametrize("mode", ["simulator", "device"])
def test_a_producer_of_the_other_mode_leaves_nothing_in_the_backend(tmp_path: Path, mode: str, caplog: pytest.LogCaptureFixture) -> None:
    """Backend `mode` and the producer of `OTHER[mode]`: its streams, status and calibrations are dropped and logged, and no session gets its data."""

    async def scenario() -> None:
        foreign = OTHER[mode]
        device = DEVICE
        session_id = str(uuid.uuid4())
        async with Observer(device, foreign) as observer, Backend(tmp_path, mode) as backend, producer_for(foreign):
            await backend.onboard()
            await until(lambda: bool(observer.of("status")) and bool(observer.of("clock")), "the foreign producer on the bus")
            # What the backend would do, but straight on the bus: the foreign producer streams a session and a calibration the backend must not take.
            async with aiomqtt.Client(HOST, PORT, identifier=f"itest-cmd-{uuid.uuid4().hex[:8]}", username=OBSERVER_USER, password=OBSERVER_PASSWORD) as sender:
                now = datetime.now(UTC).isoformat()
                base = {"schema_version": "1.0.0", "astronaut_id": ASTRONAUT, "device_id": device, "issued_at": now}
                start = {
                    **base,
                    "command_id": str(uuid.uuid4()),
                    "type": "start_session",
                    "session_id": session_id,
                    "session_type": "exercise",
                    "protocol_id": "lat_pulldown_standing_v1",
                }
                calibrate = {
                    **base,
                    "command_id": str(uuid.uuid4()),
                    "type": "calibrate",
                    "session_id": session_id,
                    "calibration_type": "emg_mvc",
                    "stream_ids": [MVC_STREAM],
                    "duration_s": 1,
                }
                for command in (start, calibrate):
                    await sender.publish(topics.cmd(ASTRONAUT, device), json.dumps(command), qos=1)
            await until(
                lambda: len([p for p in observer.of("stream") if p["session_id"] == session_id]) >= 5 and bool(observer.of("calibration")),
                "the foreign session and its calibration on the bus",
            )
            # QoS 1 order per client: the backend has handled the foreign status, clock and calibration by the time a later foreign chunk is dropped.

            def dropped(kind: str) -> list[logging.LogRecord]:
                return [
                    r for r in caplog.records if r.name == "monitor.ingest" and f"/{device}/{kind}" in r.getMessage() and "dropped message" in r.getMessage()
                ]

            await until(lambda: all(dropped(k) for k in ("stream/", "status", "calibration/")), "the foreign messages dropped as wrong_source")

            assert backend.device_status(device) is None
            assert backend.chunks(session_id) == []
            assert backend.store.calibrations({p["calibration_id"] for p in observer.of("calibration")}) == []
            assert backend.store.rejected_count() == 0
            assert all(f"source {foreign!r}" in r.getMessage() and mode in r.getMessage() for r in dropped(""))
            assert observer.contract_problems() == []

    run(scenario())
