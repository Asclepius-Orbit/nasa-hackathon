"""End to end over the real components (#56): simulator -> app -> real engine -> read API -> MCP server (http) -> agent.

What runs for real, with nothing replaced in between:

- The physiology simulator (`MissionGenerator.live_session` on a `SimDevice`) synthesises the telemetry of AST-01 for the
  first six days of the example mission: three pre-flight days on Earth and the first three days in microgravity.
- The app (`monitor.main.create_app`) is driven through its UI API (profile, daily context, self-reports, sessions) and
  receives the telemetry, the calibrations and the clock anchors over its MQTT bus.
- The engine (`create_default_app`: real feature extractor, baseline builder, pipeline and the provisional triage
  policy shipped in the package) answers /features, /baseline and /assess of the app, and later /simulate.
- The read API (`monitor.readonly.create_read_app`) and the engine are served over real HTTP (uvicorn on loopback);
  the MCP server runs as the real stdio subprocess with `--dataset http`, and the agent launches it as in production.

The doubles are only at the boundaries: the MQTT broker (in memory), the wall clock (the simulated mission dates), the
wearable gateway (the test delivers what the simulator emits and acks the commands) and the LLM (a scripted model that
calls the tools and builds its answer from what they returned).

ASSUMPTION (provisional, to be reviewed with Uri): the engine runs with `risk_saturation` = ENGINE_RISK_SATURATION instead
of its default 3.0. With the default the same simulated data is red with the risk score saturated at 1.0: the cord
elongation of the third day in microgravity is some 70 robust deviations away from a three-session Earth baseline, and
the score is 1 - exp(-sum|z| / risk_saturation). No clinical value, no threshold of the triage policy and no catalog
value is touched: the amber is decided by the real cutoffs of the shipped policy on the score of the real risk model.

The simulated scenario disables the simulator's random faults (quality artefacts, packet loss, reboots), uses four
second chunks and shortens the exercise protocol to one set, so the run is fast. The exercise session streams only the
IMU and the cord (the app decides the streams of a session), the spine checks only the cord. The ids the app draws
(`uuid.uuid4`) come from a seeded generator while the mission is played: the simulator seeds the noise of a live session
with its session id, so with random ids the z-scores would change from run to run.
"""

import asyncio
import contextlib
import itertools
import json
import os
import random
import sys
import threading
import time as time_module
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pydantic_ai.models
import pytest
import spine_physiology
import uvicorn
import yaml
from evals.task import AgentRun, EvalInput, run_chat
from fastapi import FastAPI
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from monitor.main import create_app
from monitor.readonly import create_read_app
from monitor.settings import AppSettings
from pydantic_ai.exceptions import ModelConnectionError
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from spine_agent.agent import build_agent
from spine_agent.fallback import McpStatusSource
from spine_agent.settings import AgentSettings
from spine_contracts import dump, topics, validation
from spine_engine.api import create_default_app
from spine_engine.settings import EngineSettings
from spine_engine.triage import PolicyConfig
from spine_physiology import MissionGenerator, reports
from spine_physiology.config import ExerciseProtocol, MissionConfig
from spine_physiology.device import SimDevice, plan_device_live
from spine_physiology.envelope import EnvelopeKind
from spine_physiology.generator import calibration_envelope
from spine_physiology.rng import RngTree
from spine_physiology.sessions import chunks_for, protocol_duration_s, stream_rate

pytestmark = pytest.mark.integration

ASTRONAUT = "AST-01"
UI_TOKEN = "e2e-ui-token"
UI_HEADERS = {"Authorization": f"Bearer {UI_TOKEN}", "X-Astronaut-Id": ASTRONAUT}
EXERCISE_PROTOCOL = "lat_pulldown_standing_v1"
SPINE_CHECK_PROTOCOL = "spine_length_rest_v1"
EXERCISE_STREAMS = ("imu_t9", "stretch_spine")
SPINE_CHECK_STREAMS = ("stretch_spine",)
# Example mission: days 0-2 are pre-flight on Earth, day 3 is launch. The third spine check in microgravity (day 5) is
# the first one the change detectors can call sustained (they need three points of the same gravity environment).
PREFLIGHT_DAYS = (0, 1, 2)
INFLIGHT_DAYS = (3, 4, 5)
EXERCISE_DAY = 0  # one pre-flight exercise session: its features are in the baseline, so no later set misses them
INCOMPLETE_DATA_RULE = "RULE_INCOMPLETE_DATA"
ELONGATION_DRIVERS = {"stretch.full_spine.elongation_mm", "stretch.full_spine.elongation_pct"}
# See the ASSUMPTION in the module docstring. The two drivers add up to a sum of |z| of about 145; the amber band of the
# policy (0.3 to 0.8) is a sum between 0.36 and 1.61 times the saturation (71 to 322), so the scenario sits mid-band.
ENGINE_RISK_SATURATION = 200.0
SERVER_START_TIMEOUT_S = 20.0
UUID_SEED = 56  # the issue number; any value works, it only has to be the same in every run


def _mission_config() -> MissionConfig:
    """The example mission of the simulator, without random faults, with four second chunks and a one-set exercise."""
    # Located through the installed package: the tests may run from a copy of the tree without physiology/ next to it.
    path = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
    if not path.is_file():
        pytest.skip(f"the simulator's example mission is not available at {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    for fault in raw["faults"]["quality"].values():
        fault["probability"] = 0.0
    raw["faults"]["device"]["packet_loss"]["probability"] = 0.0
    raw["faults"]["device"]["reboot"]["probability_per_hour"] = 0.0
    raw["signals"]["chunk_ms"] = 4000
    raw["protocols"][EXERCISE_PROTOCOL]["planned_sets"] = 1
    return MissionConfig.model_validate(raw)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _Bus:
    """In-memory MQTT broker for the app's single client: feeds it what the test delivers, keeps what it publishes."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()

    def factory(self) -> _Bus:
        return self

    async def __aenter__(self) -> _Bus:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def subscribe(self, topic: str, qos: int) -> None:
        return None

    async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        return None

    def deliver(self, topic: str, payload: dict[str, Any]) -> None:
        self.queue.put_nowait((topic, json.dumps(payload).encode()))

    @property
    def messages(self) -> AsyncIterator[SimpleNamespace]:
        return self._messages()

    async def _messages(self) -> AsyncIterator[SimpleNamespace]:
        while True:
            topic, payload = await self.queue.get()
            try:
                yield SimpleNamespace(topic=topic, payload=payload)
            finally:
                self.queue.task_done()


class _Mission:
    """Plays the simulated mission of one astronaut against the app, as the UI and the wearable gateway would."""

    def __init__(self, http: httpx.AsyncClient, bus: _Bus, runtime: Any, clock: _Clock) -> None:
        self.http, self.bus, self.runtime, self.clock = http, bus, runtime, clock
        self.config = _mission_config()
        self.generator = MissionGenerator(self.config)
        self.tree = RngTree(self.generator.seed)
        self.member = self.config.member(ASTRONAUT)
        self.acks = 0

    async def settle(self) -> None:
        await self.bus.queue.join()
        await self.runtime.control.idle()
        await self.runtime.analysis.idle()
        await self.runtime.control.idle()

    async def ack(self, command: dict[str, Any], status: str) -> None:
        self.acks += 1
        payload = {
            "schema_version": "1.0.0",
            "command_id": command["command_id"],
            "astronaut_id": command["astronaut_id"],
            "device_id": command["device_id"],
            "timestamp": (self.clock() + timedelta(milliseconds=self.acks)).isoformat(),
            "status": status,
        }
        self.bus.deliver(topics.ack(command["astronaut_id"], command["device_id"]), payload)
        await self.settle()

    async def put_profile(self) -> None:
        self.clock.now = self.generator.timeline.day(0).start
        profile = next(e.payload for e in self.generator.profiles() if e.astronaut_id == ASTRONAUT)
        response = await self.http.put(f"/ui/crew/{ASTRONAUT}/profile", json=profile)
        assert response.status_code == 200, response.text

    async def morning(self, day: int) -> None:
        """The daily context of the day, as the simulator reports it (phase, gravity, exercise, medication)."""
        info = self.generator.timeline.day(day)
        self.clock.now = info.at(time(8, 0))
        context = reports.build_daily_context(
            self.tree.rng(ASTRONAUT, day, "daily_context"),
            self.config,
            self.member,
            info,
            activity=self.generator.latents.activity(ASTRONAUT, day),
            state=self.generator.latents.state(ASTRONAUT, day),
        )
        response = await self.http.put(f"/ui/crew/{ASTRONAUT}/daily-context/{info.date.isoformat()}", json=dump(context))
        assert response.status_code == 200, response.text
        await self.settle()

    async def evening(self, day: int) -> None:
        """The daily self-report of the day, as the simulator answers it."""
        info = self.generator.timeline.day(day)
        report = reports.build_daily_self_report(
            self.tree.rng(ASTRONAUT, day, "self_report"),
            self.config,
            self.member,
            info,
            state=self.generator.latents.state(ASTRONAUT, day),
            report_id=self.tree.uuid(ASTRONAUT, day, "self_report"),
        )
        self.clock.now = report.timestamp
        response = await self.http.post(f"/ui/crew/{ASTRONAUT}/self-reports", json=dump(report))
        assert response.status_code == 201, response.text
        await self.settle()

    async def session(self, day: int, at: time, protocol_id: str, stream_ids: tuple[str, ...]) -> str:
        """One whole session: the UI starts it, the simulated wearable streams the protocol, the UI stops it."""
        info = self.generator.timeline.day(day)
        start = info.at(at)
        self.clock.now = start
        protocol = self.config.protocol(protocol_id)
        device_id = self.member.device_id
        boot = plan_device_live(self.config, self.generator.seed, ASTRONAUT, boot_at=start - timedelta(minutes=5), horizon=timedelta(hours=1))
        device = SimDevice(self.config, boot)

        calibrations = {stream_id: self.generator.calibration_in_force(ASTRONAUT, stream_id, day) for stream_id in stream_ids}
        streams: list[dict[str, Any]] = []
        for stream_id in stream_ids:
            declared = self.config.device.stream(stream_id)
            stream: dict[str, Any] = {
                "stream_id": stream_id,
                "stream_kind": declared.stream_kind.value,
                "placement": declared.placement.model_dump(mode="json", exclude_none=True),
                "sample_rate_hz": stream_rate(self.config, stream_id, background=False),
            }
            if (record := calibrations[stream_id]) is not None:
                stream["calibration_id"] = record.calibration_id  # as the simulator's own session records declare it
            streams.append(stream)
        plan: dict[str, Any] = {"device_id": device_id, "session_type": protocol.session_type, "protocol_id": protocol_id, "streams": streams}
        if isinstance(protocol, ExerciseProtocol):
            plan.update(
                load_declared=protocol.load.model_dump(mode="json", exclude_none=True),
                planned_reps=protocol.planned_reps,
                planned_sets=protocol.planned_sets,
                reps_per_set=protocol.reps_per_set,
            )
        response = await self.http.post(f"/ui/crew/{ASTRONAUT}/sessions", json=plan)
        assert response.status_code == 202, response.text
        session_id, start_command = response.json()["session"]["session_id"], response.json()["command"]
        await self.ack(start_command, "accepted")

        # What the gateway publishes around a session: the calibrations in force, a clock anchor and the chunks.
        for record in calibrations.values():
            if record is not None:
                envelope = calibration_envelope(ASTRONAUT, device_id, record, record.created_at)
                assert envelope.topic is not None
                self.bus.deliver(envelope.topic, envelope.payload)
        anchor = device.clock_sync(self.tree.rng(ASTRONAUT, day, "clock_sync", start.isoformat()), start)
        assert anchor is not None
        self.bus.deliver(topics.clock(ASTRONAUT, device_id), dump(anchor))

        live = self.generator.live_session(
            astronaut_id=ASTRONAUT, day=day, device=device, session_id=session_id, session_type=protocol.session_type,
            protocol_id=protocol_id, stream_ids=stream_ids, start=start,
        )  # fmt: skip
        duration_s = protocol_duration_s(protocol)
        # The live iterator never ends (it rests after the protocol): the protocol is its first n chunks of each stream.
        n_chunks = chunks_for(duration_s, chunk_ms=self.config.signals.chunk_ms) * len(stream_ids)
        for envelope in itertools.islice((e for e in live if e.kind is EnvelopeKind.STREAM_CHUNK), n_chunks):
            assert envelope.topic is not None
            self.bus.deliver(envelope.topic, envelope.payload)
        await self.bus.queue.join()

        self.clock.now = start + timedelta(seconds=duration_s)
        response = await self.http.post(f"/ui/sessions/{session_id}/stop", json={"reason": "completed"})
        assert response.status_code == 202, response.text
        stop_command = response.json()["command"]
        await self.ack(stop_command, "accepted")
        await self.ack(start_command, "completed")
        await self.ack(stop_command, "completed")

        state = (await self.http.get(f"/ui/sessions/{session_id}")).json()
        assert state["state"] == "processed", state.get("error")
        return session_id


@dataclass(frozen=True)
class Flight:
    """What the app ended up with after the simulated days, read from its own store before it was closed."""

    now: datetime
    assessment: dict[str, Any]
    baseline: dict[str, Any]
    session_ids: list[str]
    last_session_id: str


async def _fly(db_path: Path, engine_app: FastAPI) -> Flight:
    bus = _Bus()
    clock = _Clock(datetime(2026, 11, 1, tzinfo=UTC))
    settings = AppSettings(_env_file=None, db_path=db_path, reassess_debounce_s=0.0, ui_token=UI_TOKEN)
    app = create_app(settings, mqtt_client_factory=bus.factory, engine_transport=httpx.ASGITransport(app=engine_app), clock=clock)
    runtime = app.state.runtime
    bus_task = asyncio.create_task(runtime.bus.run(runtime.ingest))
    await asyncio.wait_for(runtime.bus.connected.wait(), 2)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://app", headers=UI_HEADERS) as http:
            mission = _Mission(http, bus, runtime, clock)
            await mission.put_profile()
            session_ids: list[str] = []
            for day in (*PREFLIGHT_DAYS, *INFLIGHT_DAYS):
                await mission.morning(day)
                session_ids.append(await mission.session(day, time(9, 50), SPINE_CHECK_PROTOCOL, SPINE_CHECK_STREAMS))
                if day == EXERCISE_DAY:
                    session_ids.append(await mission.session(day, time(10, 0), EXERCISE_PROTOCOL, EXERCISE_STREAMS))
                if day != INFLIGHT_DAYS[-1]:  # the scenario ends right after the last spine check
                    await mission.evening(day)
            assessment = runtime.store.latest_assessment(ASTRONAUT)
            baseline = runtime.store.latest_baseline(ASTRONAUT)
            assert assessment is not None and baseline is not None
            return Flight(clock(), assessment, baseline, session_ids, session_ids[-1])
    finally:
        bus_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await bus_task
        await runtime.analysis.close()
        await runtime.control.idle()
        runtime.control.close()
        runtime.store.close()


@contextlib.contextmanager
def _served(factory: Callable[[], FastAPI]) -> Iterator[str]:
    """Serves the app `factory` builds over real HTTP on a free loopback port; yields its base URL."""
    server = uvicorn.Server(uvicorn.Config(factory, factory=True, host="127.0.0.1", port=0, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time_module.monotonic() + SERVER_START_TIMEOUT_S
        while not server.started:
            assert thread.is_alive() and time_module.monotonic() < deadline, "the HTTP server did not start"
            time_module.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@dataclass(frozen=True)
class Stack:
    flight: Flight
    app_url: str
    engine_url: str


def _engine_app() -> FastAPI:
    return create_default_app(EngineSettings(_env_file=None, risk_saturation=ENGINE_RISK_SATURATION))


def _seeded_uuid4(seed: int) -> Callable[[], uuid.UUID]:
    rng = random.Random(seed)
    return lambda: uuid.UUID(int=rng.getrandbits(128), version=4)


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    """The simulated days played once for the whole module, then the read API and the engine served over HTTP."""
    db_path = tmp_path_factory.mktemp("e2e") / "app.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(uuid, "uuid4", _seeded_uuid4(UUID_SEED))
        flight = asyncio.run(_fly(db_path, _engine_app()))

    def read_app() -> FastAPI:
        return create_read_app(AppSettings(_env_file=None, db_path=db_path), clock=lambda: flight.now)

    with _served(read_app) as app_url, _served(_engine_app) as engine_url:
        yield Stack(flight, app_url, engine_url)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_real_model_requests() -> Iterator[None]:
    """Offline: any request to a real LLM provider fails the test."""
    previous = pydantic_ai.models.ALLOW_MODEL_REQUESTS
    pydantic_ai.models.ALLOW_MODEL_REQUESTS = False
    try:
        yield
    finally:
        pydantic_ai.models.ALLOW_MODEL_REQUESTS = previous


@pytest.fixture
def mcp_env(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented way to point the MCP server at the app and the engine: the environment of whoever launches it."""
    monkeypatch.setenv("SPINE_APP_URL", stack.app_url)
    monkeypatch.setenv("SPINE_ENGINE_URL", stack.engine_url)


def _agent_settings() -> AgentSettings:
    return AgentSettings(_env_file=None, mcp_command=sys.executable, mcp_args=["-m", "spine_mcp_server", "--dataset", "http"])


def _driver_ids(assessment: dict[str, Any]) -> list[str]:
    return [driver["feature_id"] for driver in assessment["drivers"]]


# --- the assessment the app stored ----------------------------------------------------------------------------------


def test_the_simulated_flight_ends_in_an_amber_assessment_decided_by_the_real_policy(stack: Stack) -> None:
    assessment, policy = stack.flight.assessment, PolicyConfig.default()

    assert validation.errors("domain/assessment", assessment) == []
    assert assessment["level"] == "amber"
    # Amber by the risk cutoffs of the shipped policy, not by missing data and not by a rule overriding the model.
    assert policy.amber_cutoff is not None and policy.red_cutoff is not None
    assert policy.amber_cutoff <= assessment["risk_score"] < policy.red_cutoff
    assert assessment["completeness"] == {"score": 1.0, "missing": []}
    assert INCOMPLETE_DATA_RULE not in {rule["rule_id"] for rule in assessment["fired_rules"]}
    assert {rule["rule_id"] for rule in assessment["fired_rules"]} <= {rule.rule_id for rule in policy.rules}
    assert not any(rule["overrides_model"] for rule in assessment["fired_rules"])


def test_the_amber_is_driven_by_the_cord_elongation_measured_in_microgravity(stack: Stack) -> None:
    assessment = stack.flight.assessment

    drivers = {driver["feature_id"]: driver for driver in assessment["drivers"]}
    assert set(drivers) == ELONGATION_DRIVERS
    assert all(driver["deviation_z"] > 0 and driver["contribution"] > 0 for driver in drivers.values())
    assert sum(driver["contribution"] for driver in drivers.values()) == pytest.approx(assessment["risk_score"])
    spine_length = next(domain for domain in assessment["domains"] if domain["domain"] == "spine_length")
    assert spine_length == {"domain": "spine_length", "level": "amber", "evaluated": True}
    # It is the assessment of the whole simulated history against the pre-flight baseline the engine built.
    assert assessment["based_on"]["baseline_id"] == stack.flight.baseline["baseline_id"]
    assert assessment["based_on"]["session_ids"] == stack.flight.session_ids
    assert len(stack.flight.baseline["built_from_session_ids"]) == len(PREFLIGHT_DAYS) + 1
    assert set(stack.flight.baseline["built_from_session_ids"]) == set(stack.flight.session_ids[: len(PREFLIGHT_DAYS) + 1])


# --- the MCP server over stdio, reading the app and the engine over http --------------------------------------------


async def _call_tools(calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Calls the tools on the real MCP server subprocess (`--dataset http`), which inherits the URLs from the environment."""
    env = {name: os.environ[name] for name in ("SPINE_APP_URL", "SPINE_ENGINE_URL")}
    params = StdioServerParameters(command=sys.executable, args=["-m", "spine_mcp_server", "--dataset", "http"], env=env)
    results: list[dict[str, Any]] = []
    with open(os.devnull, "w") as errlog:
        async with Client(stdio_client(params, errlog=errlog)) as client:
            for name, arguments in calls:
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result.content
                assert result.structured_content is not None
                results.append(result.structured_content)
    return results


@pytest.mark.anyio
async def test_the_mcp_tools_return_the_stored_assessment_and_what_it_was_based_on(stack: Stack, mcp_env: None) -> None:
    stored = stack.flight.assessment

    status, explanation = await _call_tools(
        [
            ("get_current_status", {"astronaut_id": ASTRONAUT}),
            ("explain_assessment", {"assessment_id": stored["assessment_id"]}),
        ]
    )

    assert status["latest_assessment"] == stored
    assert status["latest_session"]["session_id"] == stack.flight.last_session_id
    assert explanation["assessment"] == stored
    assert explanation["baseline"]["baseline_id"] == stack.flight.baseline["baseline_id"]
    assert explanation["baseline"]["version"] == stack.flight.baseline["version"]
    assert {entry["feature_id"] for entry in explanation["baseline_entries"]} >= ELONGATION_DRIVERS
    # The explanation carries the feature values behind each driver: the latest spine check, measured in microgravity.
    latest = next(fs for fs in explanation["feature_sets"] if fs["session_id"] == stack.flight.last_session_id)
    assert latest["gravity_environment"] == "microgravity"
    measured = {feature["feature_id"]: feature["value"] for feature in latest["features"]}
    for driver in stored["drivers"]:
        assert measured[driver["feature_id"]] == driver["value"]


@pytest.mark.anyio
async def test_simulate_scenario_starts_from_the_real_amber_and_an_override_of_its_drivers_changes_it(stack: Stack, mcp_env: None) -> None:
    stored = stack.flight.assessment
    entries = {entry["feature_id"]: entry for entry in stack.flight.baseline["entries"] if entry.get("protocol_id") == SPINE_CHECK_PROTOCOL}
    # What if the cord were back at its pre-flight length: each driver set to the median of its baseline entry.
    back_to_baseline = [{"feature_id": feature_id, "mode": "set", "value": entries[feature_id]["median"]} for feature_id in sorted(ELONGATION_DRIVERS)]

    [unchanged, relieved] = await _call_tools(
        [
            ("simulate_scenario", {"astronaut_id": ASTRONAUT, "overrides": []}),
            ("simulate_scenario", {"astronaut_id": ASTRONAUT, "overrides": back_to_baseline}),
        ]
    )

    for simulation in (unchanged, relieved):
        assert simulation["hypothetical"] is True
        reference = simulation["reference"]
        # The reference is the real engine reassessing the app's current data: the same amber, with the same drivers.
        assert reference["level"] == stored["level"]
        assert reference["risk_score"] == pytest.approx(stored["risk_score"])
        assert _driver_ids(reference) == _driver_ids(stored)
        assert reference["based_on"] == stored["based_on"]
    assert unchanged["scenario"]["level"] == "amber"
    assert _driver_ids(unchanged["scenario"]) == _driver_ids(stored)
    assert relieved["scenario"]["level"] == "green"
    assert relieved["scenario"]["drivers"] == []


# --- the agent --------------------------------------------------------------------------------------------------------


def _tool_returns(messages: list[ModelMessage]) -> list[Any]:
    return [part.content for m in messages if isinstance(m, ModelRequest) for part in m.parts if isinstance(part, ToolReturnPart)]


def _as_dict(content: Any) -> dict[str, Any]:
    return json.loads(content) if isinstance(content, str) else content


def _explaining_model() -> FunctionModel:
    """Scripted LLM that knows nothing but the astronaut: it asks for the status, then for the explanation of the assessment
    that status names, and writes its answer only from what the second tool returned."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        returns = _tool_returns(messages)
        if not returns:
            yield {0: DeltaToolCall(name="get_current_status", json_args=json.dumps({"astronaut_id": ASTRONAUT}), tool_call_id="call-0")}
        elif len(returns) == 1:
            assessment_id = _as_dict(returns[0])["latest_assessment"]["assessment_id"]
            yield {0: DeltaToolCall(name="explain_assessment", json_args=json.dumps({"assessment_id": assessment_id}), tool_call_id="call-1")}
        else:
            assessment = _as_dict(returns[1])["assessment"]
            yield f"Your level is {assessment['level']} with a risk score of {assessment['risk_score']:.2f}. "
            for driver in assessment["drivers"]:
                yield f"{driver['feature_id']} is {driver['value']:.2f}, {driver['deviation_z']:.1f} deviations from your baseline. "
            yield "This is monitoring evidence; please review it with the flight surgeon."

    return FunctionModel(stream_function=stream)


async def _ask(model: FunctionModel, flight: Flight) -> AgentRun:
    settings = _agent_settings()
    inputs = EvalInput(question="Why am I amber?", astronaut_id=ASTRONAUT, language="en")
    return await run_chat(build_agent(settings, model=model), McpStatusSource(settings), inputs, wall_clock=lambda: flight.now)


@pytest.mark.anyio
async def test_the_agent_explains_the_real_amber_with_the_drivers_its_tools_returned(stack: Stack, mcp_env: None) -> None:
    stored = stack.flight.assessment

    run = await _ask(_explaining_model(), stack.flight)

    assert run.fell_back is False and run.notices == []
    assert [(call.name, call.args) for call in run.tool_calls] == [
        ("get_current_status", {"astronaut_id": ASTRONAUT}),
        ("explain_assessment", {"assessment_id": stored["assessment_id"]}),
    ]
    status, explanation = (_as_dict(result) for result in run.tool_results)
    assert status["latest_assessment"] == stored
    assert explanation["assessment"] == stored
    # The answer is the stored assessment, word for word: the level, the score and every driver with its value and z.
    assert f"Your level is amber with a risk score of {stored['risk_score']:.2f}." in run.text
    assert stored["drivers"]
    for driver in stored["drivers"]:
        assert f"{driver['feature_id']} is {driver['value']:.2f}, {driver['deviation_z']:.1f} deviations from your baseline." in run.text


@pytest.mark.anyio
async def test_without_the_model_the_fallback_summary_reports_the_same_amber_and_drivers(stack: Stack, mcp_env: None) -> None:
    async def dead(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        raise ModelConnectionError("scripted", "refused")
        yield ""  # pragma: no cover  (makes this an async generator)

    run = await _ask(FunctionModel(stream_function=dead), stack.flight)

    assert run.fell_back is True
    assert "AMBER" in run.text
    for feature_id in _driver_ids(stack.flight.assessment):
        assert feature_id in run.text
