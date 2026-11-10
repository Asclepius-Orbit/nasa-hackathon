"""Analysis with a scripted engine: what is sent, what is stored, and every way it can refuse."""

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from monitor.analysis import Analysis
from monitor.db import Store, utc_key
from monitor.engine import EngineError

pytestmark = pytest.mark.anyio

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
NOW = datetime(2026, 11, 13, 10, 30, tzinfo=UTC)


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ScriptedEngine:
    """Answers with the examples unless told otherwise; records every request."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, Exception] = {}
        self.override: dict[str, dict] = {}

    async def _answer(self, name: str, request: dict, default: dict) -> dict:
        self.calls.append((name, request))
        if name in self.fail:
            raise self.fail[name]
        return self.override.get(name, default)

    async def features(self, request: dict) -> dict:
        session = request["session"]
        answer = example("domain/feature-set.preflight")
        answer.update(session_id=session["session_id"], astronaut_id=session["astronaut_id"])
        return await self._answer("features", request, answer)

    async def baseline(self, request: dict) -> dict:
        answer = example("domain/baseline")
        answer["version"] = request.get("previous_baseline_version", 0) + 1
        answer["baseline_id"] = str(uuid.uuid4())
        return await self._answer("baseline", request, answer)

    async def assess(self, request: dict) -> dict:
        return await self._answer("assess", request, example("domain/assessment.amber"))


@pytest.fixture
def store():
    store = Store(":memory:")
    yield store
    store.close()


@pytest.fixture
def engine() -> ScriptedEngine:
    return ScriptedEngine()


@pytest.fixture
def analysis(store, engine) -> Analysis:
    return Analysis(store, engine, window_days=14, clock=lambda: NOW)  # pyright: ignore[reportArgumentType]


def ended_session(store: Store, *, phase: str = "preflight", chunks: bool = True, session_id: str | None = None, state: str = "ended") -> str:
    session = example("domain/session.exercise")
    session["mission_context"]["phase"] = phase
    if session_id is not None:
        session["session_id"] = session_id
    store.insert_session(session, state)
    if chunks:
        # Another seq per session: chunks are keyed by device, stream, boot and seq.
        seq = 1842 if session_id is None else 1842 + int(session_id[-4:], 16)
        store.add_chunk(example("telemetry/stream-chunk.emg") | {"session_id": session["session_id"], "seq": seq})
    return session["session_id"]


def unreachable() -> EngineError:
    """What the client raises when the engine does not answer: no status, caused by the HTTP error."""
    exc = EngineError("/features: engine unreachable: connection refused")
    exc.__cause__ = httpx.ConnectError("connection refused")
    return exc


def ready_for_assessment(store: Store) -> None:
    store.put_profile(example("domain/astronaut-profile"))
    store.put_baseline(example("domain/baseline"))
    feature_set = example("domain/feature-set.preflight")
    store.put_feature_set(feature_set, phase="preflight", anchor_at="2026-11-13T10:02:31Z")


async def test_session_is_processed_once(store, engine, analysis):
    session_id = ended_session(store)

    await analysis.process_session(session_id)
    await analysis.process_session(session_id)

    # No profile yet: the assessment is skipped, so the engine only sees features and baseline.
    assert [name for name, _ in engine.calls] == ["features", "baseline"]
    assert store.session(session_id).state == "processed"


async def test_baseline_receives_reports_within_preflight_windows(store, engine, analysis):
    session_id = ended_session(store)
    report = example("domain/self-report.daily") | {"timestamp": "2026-11-12T08:00:00Z"}
    store.put_self_report(report)
    await analysis.process_session(session_id)
    request = next(body for name, body in engine.calls if name == "baseline")
    assert request["self_reports"] == [report]


async def test_features_request_carries_what_the_session_cites(store, engine, analysis):
    session_id = ended_session(store)
    store.put_calibration(example("domain/calibration.stretch"))
    store.add_clock_sync(example("telemetry/clock-sync.round-trip"))
    store.put_profile(example("domain/astronaut-profile"))

    await analysis.process_session(session_id)

    _, request = engine.calls[0]
    assert [c["calibration_id"] for c in request["calibrations"]] == ["7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f11"]
    assert len(request["clock_syncs"]) == 1
    assert request["profile"]["astronaut_id"] == "AST-01"
    assert len(request["chunks"]) == 1


async def test_profile_is_optional_for_features(store, engine, analysis):
    session_id = ended_session(store)

    await analysis.process_session(session_id)

    assert "profile" not in engine.calls[0][1]


async def test_engine_failure_marks_processing_failed(store, engine, analysis):
    engine.fail["features"] = EngineError("boom")
    session_id = ended_session(store)

    await analysis.process_session(session_id)

    row = store.session(session_id)
    assert (row.state, row.error) == ("processing_failed", "boom")
    assert store.feature_set(session_id) is None


async def test_feature_set_of_another_session_is_refused(store, engine, analysis):
    session_id = ended_session(store)
    other = example("domain/feature-set.preflight")
    other["session_id"] = "00000000-0000-4000-8000-000000000000"
    engine.override["features"] = other

    await analysis.process_session(session_id)

    assert store.session(session_id).state == "processing_failed"
    assert store.feature_set(session_id) is None


async def test_inflight_session_does_not_rebuild_the_baseline(store, engine, analysis):
    session_id = ended_session(store, phase="inflight")

    await analysis.process_session(session_id)

    assert "baseline" not in [name for name, _ in engine.calls]


async def test_empty_features_do_not_rebuild_the_baseline(store, engine, analysis):
    session_id = ended_session(store)
    empty = example("domain/feature-set.preflight")
    empty["features"] = []
    engine.features = _returning(engine, empty, session_id)  # type: ignore[method-assign]

    await analysis.process_session(session_id)

    assert "baseline" not in [name for name, _ in engine.calls]


def _returning(engine: ScriptedEngine, answer: dict, session_id: str):
    async def features(request: dict) -> dict:
        engine.calls.append(("features", request))
        return {**answer, "session_id": session_id}

    return features


async def test_baseline_versions_increase(store, engine, analysis):
    first = ended_session(store)
    await analysis.process_session(first)
    store.insert_session({**example("domain/session.exercise"), "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a09"}, "ended")
    chunk = {**example("telemetry/stream-chunk.emg"), "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a09", "seq": 1}
    store.add_chunk(chunk)

    await analysis.process_session("3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a09")

    baseline_requests = [r for name, r in engine.calls if name == "baseline"]
    assert "previous_baseline_version" not in baseline_requests[0]
    assert baseline_requests[1]["previous_baseline_version"] == 1
    assert len(baseline_requests[1]["feature_sets"]) == 2
    assert store.latest_baseline("AST-01")["version"] == 2


async def test_baseline_refused_by_the_engine_keeps_the_old_one(store, engine, analysis):
    store.put_baseline(example("domain/baseline"))
    engine.fail["baseline"] = EngineError("insufficient")

    await analysis.process_session(ended_session(store))

    assert store.latest_baseline("AST-01")["version"] == 1


async def test_baseline_that_is_not_newer_is_ignored(store, engine, analysis):
    store.put_baseline(example("domain/baseline"))
    engine.override["baseline"] = example("domain/baseline")

    await analysis.process_session(ended_session(store))

    assert store.latest_baseline("AST-01") == example("domain/baseline")


@pytest.mark.parametrize(
    ("prepare", "reason"),
    [
        (lambda s: None, "no_profile"),
        (lambda s: s.put_profile(example("domain/astronaut-profile")), "no_baseline"),
        (lambda s: (s.put_profile(example("domain/astronaut-profile")), s.put_baseline(example("domain/baseline"))), "no_feature_sets"),
    ],
)
async def test_assessment_is_skipped_without_its_inputs(store, engine, analysis, prepare, reason):
    prepare(store)

    outcome = await analysis.assess("AST-01")

    assert (outcome.assessment, outcome.skipped) == (None, reason)
    assert engine.calls == []


async def test_assess_request_has_the_window_and_the_mission_day(store, engine, analysis):
    ready_for_assessment(store)
    store.put_self_report(example("domain/self-report.daily") | {"timestamp": "2026-11-13T08:00:00Z"})
    store.put_self_report(example("domain/self-report.daily") | {"timestamp": "2026-10-01T08:00:00Z", "report_id": "5e1f0e90-0000-4000-8000-0000000000ff"})
    context = example("domain/daily-context") | {"date": "2026-11-13", "mission_day": -30}
    store.put_daily_context(context)

    outcome = await analysis.assess("AST-01")

    _, request = engine.calls[0]
    assert request["as_of"] == NOW.isoformat()
    assert request["mission_day"] == -30
    assert [r["timestamp"] for r in request["self_reports"]] == ["2026-11-13T08:00:00Z"]
    assert request["daily_context"] == [context]
    assert outcome.assessment == store.latest_assessment("AST-01")


async def test_context_without_mission_day_sends_none(store, engine, analysis):
    ready_for_assessment(store)
    context = copy.deepcopy(example("domain/daily-context")) | {"date": "2026-11-13"}
    del context["mission_day"]
    store.put_daily_context(context)

    await analysis.assess("AST-01")

    assert "mission_day" not in engine.calls[0][1]


async def test_engine_error_on_assess_is_reported(store, engine, analysis):
    ready_for_assessment(store)
    engine.fail["assess"] = EngineError("down")

    outcome = await analysis.assess("AST-01")

    assert outcome.skipped == "engine_error: down"
    assert store.latest_assessment("AST-01") is None


async def test_assessment_of_another_astronaut_is_refused(store, engine, analysis):
    ready_for_assessment(store)
    engine.override["assess"] = example("domain/assessment.amber") | {"astronaut_id": "AST-02"}

    outcome = await analysis.assess("AST-01")

    assert outcome.skipped == "engine_error: assessment of another astronaut"
    assert store.latest_assessment("AST-01") is None


# --- retries and restart recovery ----------------------------------------------------------------


@pytest.mark.parametrize("error", [unreachable(), EngineError("/features: HTTP 503: busy", status=503)], ids=["unreachable", "5xx"])
async def test_transient_engine_failure_schedules_a_retry(store, engine, analysis, error):
    engine.fail["features"] = error
    session_id = ended_session(store)

    await analysis.process_session(session_id)

    assert store.session(session_id).state == "processing_failed"
    attempt = store.processing_attempt(session_id)
    assert attempt is not None
    assert (attempt.attempts, attempt.last_error, attempt.next_at) == (1, str(error), utc_key(NOW + timedelta(seconds=30)))


async def test_each_transient_failure_doubles_the_wait(store, engine, analysis):
    engine.fail["features"] = unreachable()
    session_id = ended_session(store)
    await analysis.process_session(session_id)
    store.transition_session(session_id, ("processing_failed",), "ended")

    await analysis.process_session(session_id)

    attempt = store.processing_attempt(session_id)
    assert attempt is not None
    assert (attempt.attempts, attempt.next_at) == (2, utc_key(NOW + timedelta(seconds=60)))


@pytest.mark.parametrize(
    "error",
    [EngineError("/features: HTTP 422: bad", status=422), EngineError("/features: response violates domain/feature-set"), None],
    ids=["4xx", "contract", "no-chunks"],
)
async def test_final_failure_is_not_retried(store, engine, analysis, error):
    if error is not None:
        engine.fail["features"] = error
    session_id = ended_session(store, chunks=error is not None)

    await analysis.process_session(session_id)

    assert store.session(session_id).state == "processing_failed"
    assert store.processing_attempt(session_id) is None


async def test_success_after_a_failure_clears_the_retries(store, engine, analysis):
    engine.fail["features"] = unreachable()
    session_id = ended_session(store)
    await analysis.process_session(session_id)
    del engine.fail["features"]
    store.transition_session(session_id, ("processing_failed",), "ended")

    await analysis.process_session(session_id)

    assert store.session(session_id).state == "processed"
    assert store.processing_attempt(session_id) is None


async def test_recovery_reprocesses_interrupted_and_pending_sessions(store, engine, analysis):
    interrupted = ended_session(store, state="processing")
    pending = ended_session(store, session_id="00000000-0000-4000-8000-0000000000aa")
    failed = ended_session(store, session_id="00000000-0000-4000-8000-0000000000bb", state="processing_failed")

    recovered = await analysis.recover_interrupted()

    assert sorted(recovered) == sorted([interrupted, pending])
    assert [store.session(s).state for s in (interrupted, pending, failed)] == ["processed", "processed", "processing_failed"]
