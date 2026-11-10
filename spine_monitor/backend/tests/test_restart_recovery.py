"""Restart the app with a database left in each state: the real lifespan recovers it (issue #59)."""

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from monitor.db import Store
from monitor.main import create_app
from monitor.settings import AppSettings
from spine_engine.api import create_app as create_engine_app
from spine_engine.factory import default_baseline_builder, default_pipeline
from spine_engine.settings import EngineSettings

from .test_edges import _FlakyClient  # pyright: ignore[reportAttributeAccessIssue]

pytestmark = pytest.mark.integration

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
START_COMMAND = "c0ffee00-0000-4000-8000-000000000001"
SESSION = "00000000-0000-4000-8000-0000000000a1"


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


class IdleClient(_FlakyClient):
    async def __aenter__(self):
        return self


class Clock:
    """Starts at the real time and only moves when the test says so."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Engine(httpx.AsyncBaseTransport):
    """The real engine app behind a switch: while `up` is False it refuses connections. Counts the calls."""

    def __init__(self, *, up: bool = True) -> None:
        from .conftest import ExampleExtractor

        app = create_engine_app(
            pipeline=default_pipeline(EngineSettings(_env_file=None)),
            baseline_builder=default_baseline_builder(),
            feature_extractor=ExampleExtractor(),
        )
        self._inner = httpx.ASGITransport(app=app)
        self.up = up
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if not self.up:
            raise httpx.ConnectError("engine down", request=request)
        return await self._inner.handle_async_request(request)


def wait_for(condition: Callable[[], object], timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.02)


def seed_session(db_path: Path, state: str, *, chunks: bool = True) -> None:
    store = Store(db_path)
    store.insert_session(example("domain/session.exercise") | {"session_id": SESSION}, state)
    if chunks:
        store.add_chunk(example("telemetry/stream-chunk.emg") | {"session_id": SESSION})
    store.close()


def seed_failure(db_path: Path, *, attempts: int, next_at: datetime) -> None:
    store = Store(db_path)
    for _ in range(attempts):
        store.record_processing_failure(SESSION, "engine unreachable", next_at)
    store.close()


def seed_start_command(db_path: Path, state: str, *, age_s: float) -> None:
    store = Store(db_path)
    command = example("telemetry/command.start-session")
    command["issued_at"] = (datetime.now(UTC) - timedelta(seconds=age_s)).isoformat()
    command["session_id"] = SESSION
    store.insert_command(command, state)
    store.close()


def build(tmp_path: Path, engine: Engine | None = None, clock: Callable[[], datetime] | None = None, **settings):
    return create_app(
        AppSettings(_env_file=None, db_path=tmp_path / "app.db", processing_retry_tick_s=0.05, **settings),
        mqtt_client_factory=lambda: IdleClient([]),  # pyright: ignore[reportArgumentType]
        engine_transport=engine,
        **({"clock": clock} if clock else {}),
    )


class Probe:
    """Reads the store on the app's event loop thread: its SQLite connection must not be shared with the test thread."""

    def __init__(self, client: TestClient, app) -> None:
        self._client = client
        self._store: Store = app.state.runtime.store

    def _on_loop(self, read: Callable[[], object]):
        return self._client.portal.call(read)  # pyright: ignore[reportOptionalMemberAccess]

    def session(self):
        return self._on_loop(lambda: self._store.session(SESSION))

    def state(self) -> str:
        return self.session().state  # pyright: ignore[reportAttributeAccessIssue]

    def command_state(self) -> str:
        return self._on_loop(lambda: self._store.command(START_COMMAND)).state  # pyright: ignore[reportAttributeAccessIssue]

    def attempt(self):
        return self._on_loop(lambda: self._store.processing_attempt(SESSION))

    def attempts(self) -> int:
        attempt = self.attempt()
        return attempt.attempts if attempt else 0  # pyright: ignore[reportAttributeAccessIssue]


def test_sent_start_session_within_its_timeout_waits_the_rest_then_expires(tmp_path):
    seed_session(tmp_path / "app.db", "starting", chunks=False)
    seed_start_command(tmp_path / "app.db", "sent", age_s=0.2)
    app = build(tmp_path, ack_timeout_s=1.5)

    with TestClient(app) as client:
        probe = Probe(client, app)
        assert probe.command_state() == "sent"
        assert probe.state() == "starting"
        wait_for(lambda: probe.command_state() == "timeout")
        wait_for(lambda: probe.state() == "rejected")
        assert probe.session().error == "ack_timeout"


def test_starting_session_without_a_sent_start_command_is_rejected(tmp_path):
    seed_session(tmp_path / "app.db", "starting", chunks=False)
    app = build(tmp_path)

    with TestClient(app) as client:
        row = Probe(client, app).session()
        assert (row.state, row.error) == ("rejected", "ack_timeout")


def test_ended_session_never_processed_is_processed_after_startup(tmp_path):
    seed_session(tmp_path / "app.db", "ended")
    app = build(tmp_path, Engine())

    with TestClient(app) as client:
        probe = Probe(client, app)
        wait_for(lambda: probe.state() == "processed")


def test_due_failed_session_is_retried_when_the_engine_is_reachable(tmp_path):
    seed_session(tmp_path / "app.db", "processing_failed")
    seed_failure(tmp_path / "app.db", attempts=1, next_at=datetime.now(UTC) - timedelta(seconds=5))
    app = build(tmp_path, Engine())

    with TestClient(app) as client:
        probe = Probe(client, app)
        wait_for(lambda: probe.state() == "processed")
        assert probe.attempt() is None


def test_retry_fails_while_the_engine_is_down_then_succeeds_once_it_is_back_and_due(tmp_path):
    seed_session(tmp_path / "app.db", "processing_failed")
    clock = Clock()
    seed_failure(tmp_path / "app.db", attempts=1, next_at=clock.now - timedelta(seconds=1))
    engine = Engine(up=False)
    app = build(tmp_path, engine, clock, processing_retry_min_s=10, processing_retry_max_s=100)

    with TestClient(app) as client:
        probe = Probe(client, app)
        wait_for(lambda: probe.attempts() == 2)
        assert datetime.fromisoformat(probe.attempt().next_at) == clock.now + timedelta(seconds=20)  # pyright: ignore[reportOptionalMemberAccess]
        assert probe.state() == "processing_failed"
        calls = engine.calls

        engine.up = True
        time.sleep(0.2)  # several ticks: not due yet, so no new attempt although the engine answers
        assert engine.calls == calls
        clock.advance(25)
        wait_for(lambda: probe.state() == "processed")
        assert probe.attempt() is None


def test_failed_session_that_reached_max_attempts_is_not_retried(tmp_path):
    seed_session(tmp_path / "app.db", "processing_failed")
    seed_failure(tmp_path / "app.db", attempts=2, next_at=datetime.now(UTC) - timedelta(seconds=5))
    engine = Engine()
    app = build(tmp_path, engine, processing_max_attempts=2)

    with TestClient(app) as client:
        time.sleep(0.3)  # several retry ticks
        assert Probe(client, app).state() == "processing_failed"
        assert engine.calls == 0


def test_final_failure_is_left_alone_at_startup_until_a_reprocess_is_asked(tmp_path):
    seed_session(tmp_path / "app.db", "processing_failed")
    engine = Engine()
    app = build(tmp_path, engine)

    with TestClient(app) as client:
        probe = Probe(client, app)
        time.sleep(0.3)
        assert probe.state() == "processing_failed"
        assert engine.calls == 0

        response = client.post(f"/ui/sessions/{SESSION}/reprocess")

        assert response.status_code == 202
        wait_for(lambda: probe.state() == "processed")
