"""Test harness: the app wired to an in-memory MQTT broker and to the real engine over ASGI.

The engine runs its real baseline builder and assessment pipeline; only the feature extractor is
replaced (the real one is not implemented yet) by one that returns the pre-flight example features
for the requested session.
"""

import asyncio
import contextlib
import copy
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiomqtt
import httpx
import pytest
from spine_contracts import FeatureSet, FeaturesRequest, topics
from spine_engine.api import create_app as create_engine_app
from spine_engine.factory import default_baseline_builder, default_pipeline
from spine_engine.settings import EngineSettings

from monitor.main import create_app
from monitor.settings import AppSettings

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
ASTRONAUT, DEVICE = "AST-01", "wb-01"
NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class FakeMqtt:
    """In-memory broker for one client: records publishes and feeds queued messages."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self.published: list[tuple[str, dict[str, Any], int, bool]] = []
        self.subscriptions: list[tuple[str, int]] = []
        self.fail_publish = False

    def factory(self) -> "_FakeClient":
        return _FakeClient(self)

    def deliver(self, topic: str, payload: dict[str, Any] | bytes) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.queue.put_nowait((topic, body))

    def commands(self, kind: str | None = None) -> list[dict[str, Any]]:
        return [p for t, p, _, _ in self.published if t.endswith("/cmd") and (kind is None or p["type"] == kind)]

    async def _messages(self) -> AsyncIterator[SimpleNamespace]:
        while True:
            topic, payload = await self.queue.get()
            try:
                yield SimpleNamespace(topic=topic, payload=payload)
            finally:
                self.queue.task_done()


class _FakeClient:
    def __init__(self, broker: FakeMqtt) -> None:
        self._broker = broker

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def subscribe(self, topic: str, qos: int) -> None:
        self._broker.subscriptions.append((topic, qos))

    async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        if self._broker.fail_publish:
            raise aiomqtt.MqttError("broker down")
        self._broker.published.append((topic, json.loads(payload), qos, retain))

    @property
    def messages(self) -> AsyncIterator[SimpleNamespace]:
        return self._broker._messages()


class ExampleExtractor:
    """Returns the pre-flight example features for whatever session is requested."""

    def __init__(self) -> None:
        self.requests: list[FeaturesRequest] = []

    def extract(self, request: FeaturesRequest) -> FeatureSet:
        self.requests.append(request)
        session = request.session
        payload = example("domain/feature-set.preflight")
        payload.update(
            session_id=session.session_id,
            astronaut_id=session.astronaut_id,
            protocol_id=session.protocol_id,
            gravity_environment=session.mission_context.gravity_environment.value,
            computed_at=NOW.isoformat(),
        )
        payload.pop("window", None)
        return FeatureSet.model_validate(payload)


@dataclass
class Harness:
    http: httpx.AsyncClient
    mqtt: FakeMqtt
    runtime: Any
    clock: Clock
    extractor: ExampleExtractor
    acks: int = field(default=0)

    # Test modules are imported with importlib, so they reach the helpers through the fixture.
    astronaut_id = ASTRONAUT
    device_id = DEVICE
    example = staticmethod(example)
    plan = staticmethod(lambda **overrides: session_plan(**overrides))

    @property
    def store(self):
        return self.runtime.store

    async def settle(self) -> None:
        """Waits until every delivered message is handled and the background analysis is done."""
        await self.mqtt.queue.join()
        await self.runtime.control.idle()

    async def ack(self, command: dict[str, Any], status: str, **extra: Any) -> None:
        self.acks += 1
        payload = {
            "schema_version": "1.0.0",
            "command_id": command["command_id"],
            "astronaut_id": command["astronaut_id"],
            "device_id": command["device_id"],
            "timestamp": (self.clock() + timedelta(milliseconds=self.acks)).isoformat(),
            "status": status,
            **extra,
        }
        self.mqtt.deliver(topics.ack(command["astronaut_id"], command["device_id"]), payload)
        await self.settle()

    async def setup_astronaut(self, phase: str = "preflight", gravity: str = "earth_1g") -> None:
        profile = example("domain/astronaut-profile")
        assert (await self.http.put(f"/ui/crew/{ASTRONAUT}/profile", json=profile)).status_code == 200
        context = {"mission_day": -30, "phase": phase, "gravity_environment": gravity}
        day = self.clock().date().isoformat()
        assert (await self.http.put(f"/ui/crew/{ASTRONAUT}/daily-context/{day}", json=context)).status_code == 200

    async def start_session(self, **overrides: Any) -> tuple[str, dict[str, Any]]:
        """Creates an exercise session and acks its start_session as accepted."""
        response = await self.http.post(f"/ui/crew/{ASTRONAUT}/sessions", json=session_plan(**overrides))
        assert response.status_code == 202, response.text
        body = response.json()
        await self.ack(body["command"], "accepted")
        return body["session"]["session_id"], body["command"]

    async def send_chunks(self, session_id: str, *, seq_offset: int = 0) -> None:
        for name in ("stream-chunk.emg", "stream-chunk.imu", "stream-chunk.stretch"):
            chunk = example(f"telemetry/{name}")
            chunk["session_id"] = session_id
            chunk["seq"] += seq_offset
            self.mqtt.deliver(topics.stream(chunk["astronaut_id"], chunk["device_id"], chunk["stream_id"]), chunk)
        await self.settle()

    async def stop_session(self, session_id: str, start_command: dict[str, Any], reason: str = "completed") -> None:
        response = await self.http.post(f"/ui/sessions/{session_id}/stop", json={"reason": reason})
        assert response.status_code == 202, response.text
        stop = response.json()["command"]
        await self.ack(stop, "accepted")
        await self.ack(start_command, "completed")
        await self.ack(stop, "completed")


def session_plan(**overrides: Any) -> dict[str, Any]:
    session = example("domain/session.exercise")
    plan = {k: copy.deepcopy(session[k]) for k in ("device_id", "session_type", "protocol_id", "load_declared", "planned_reps", "streams")}
    plan.update(overrides)
    return plan


@pytest.fixture
async def harness(tmp_path: Path, anyio_backend: str) -> AsyncIterator[Harness]:
    mqtt = FakeMqtt()
    clock = Clock(NOW)
    extractor = ExampleExtractor()
    engine_app = create_engine_app(
        pipeline=default_pipeline(EngineSettings(_env_file=None)),
        baseline_builder=default_baseline_builder(),
        feature_extractor=extractor,
    )
    settings = AppSettings(_env_file=None, db_path=tmp_path / "app.db", ack_timeout_s=0.2)
    app = create_app(settings, mqtt_client_factory=mqtt.factory, engine_transport=httpx.ASGITransport(app=engine_app), clock=clock)
    runtime = app.state.runtime
    bus_task = asyncio.create_task(runtime.bus.run(runtime.ingest))
    await asyncio.wait_for(runtime.bus.connected.wait(), 2)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://app") as http:
        yield Harness(http, mqtt, runtime, clock, extractor)
    bus_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await bus_task
    await runtime.control.idle()
    runtime.control.close()
    runtime.store.close()
