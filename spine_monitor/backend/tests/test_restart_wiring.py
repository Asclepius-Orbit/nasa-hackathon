"""The lifespan recovers a database left by a dead process: command timers, interrupted sessions, the retry loop."""

import asyncio
import json
import time
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


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


class IdleClient(_FlakyClient):
    async def __aenter__(self):
        return self


def seed(db_path: Path) -> tuple[dict, str]:
    """What a dead process leaves: a `sent` start_session long past its timeout and a session left `processing`."""
    store = Store(db_path)
    start = example("telemetry/command.start-session")
    start["issued_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    store.insert_command(start, "sent")
    interrupted = example("domain/session.exercise")
    interrupted["session_id"] = "00000000-0000-4000-8000-0000000000cc"
    store.insert_session(interrupted, "processing")
    store.add_chunk(example("telemetry/stream-chunk.emg") | {"session_id": interrupted["session_id"]})
    store.close()
    return start, interrupted["session_id"]


def wait_for(condition, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.02)


def build(tmp_path: Path, *, engine_ok: bool = True):
    extractor_app = None
    if engine_ok:
        from .conftest import ExampleExtractor

        extractor_app = create_engine_app(
            pipeline=default_pipeline(EngineSettings(_env_file=None)),
            baseline_builder=default_baseline_builder(),
            feature_extractor=ExampleExtractor(),
        )
    return create_app(
        AppSettings(_env_file=None, db_path=tmp_path / "app.db", processing_retry_tick_s=0.05),
        mqtt_client_factory=lambda: IdleClient([]),  # pyright: ignore[reportArgumentType]
        engine_transport=httpx.ASGITransport(app=extractor_app) if extractor_app else None,
    )


def test_startup_expires_stale_commands_and_reprocesses_interrupted_sessions(tmp_path):
    start, interrupted = seed(tmp_path / "app.db")
    app = build(tmp_path)

    with TestClient(app) as client:
        runtime = app.state.runtime
        wait_for(lambda: runtime.store.session(interrupted).state != "processing")
        wait_for(lambda: runtime.store.session(interrupted).state in ("processed", "processing_failed"))
        assert runtime.store.command(start["command_id"]).state == "timeout"
        assert client.get("/health").status_code == 200


class HangingEngine(httpx.AsyncBaseTransport):
    """Never answers /features: startup must not wait for it."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def test_startup_does_not_wait_for_the_engine_and_shutdown_cancels_the_recovery_task(tmp_path):
    _, interrupted = seed(tmp_path / "app.db")
    app = create_app(
        AppSettings(_env_file=None, db_path=tmp_path / "app.db"),
        mqtt_client_factory=lambda: IdleClient([]),  # pyright: ignore[reportArgumentType]
        engine_transport=HangingEngine(),
    )

    started = time.monotonic()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert time.monotonic() - started < 3
        task = next(t for t in _tasks(client) if t.get_name() == "analysis-recovery")
        assert not task.done()

    assert task.cancelled() or task.done()


def _tasks(client: TestClient):
    return client.portal.call(_all_tasks)  # pyright: ignore[reportOptionalMemberAccess]


async def _all_tasks():
    return asyncio.all_tasks()
