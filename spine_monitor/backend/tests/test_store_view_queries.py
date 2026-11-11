"""Store queries that back the UI views: session rows of any state and unbounded assessment ranges."""

import copy
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from monitor.db import ProcessingAttempt, Store

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def session(n: int, started_at: str, *, phase: str = "preflight", astronaut: str = "AST-01") -> dict:
    payload = copy.deepcopy(example("domain/session.exercise"))
    payload.update(session_id=f"3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a{n:02d}", started_at=started_at, astronaut_id=astronaut)
    payload["mission_context"]["phase"] = phase
    return payload


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "app.db")
    yield store
    store.close()


def at(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


def ids(rows) -> list[str]:
    return [r.payload["session_id"][-2:] for r in rows]


def test_session_rows_come_oldest_first_in_any_state(store):
    store.insert_session(session(2, "2026-11-12T10:00:00Z"), "rejected")
    store.insert_session(session(1, "2026-11-10T10:00:00Z"), "starting")
    store.insert_session(session(3, "2026-11-13T10:00:00Z", astronaut="AST-02"), "running")

    rows = store.session_rows("AST-01")

    assert [(r.payload["session_id"][-2:], r.state) for r in rows] == [("01", "starting"), ("02", "rejected")]


def test_session_rows_filter_by_phase_and_by_started_at_range(store):
    store.insert_session(session(1, "2026-11-10T10:00:00Z", phase="preflight"), "ended")
    store.insert_session(session(2, "2026-11-12T10:00:00Z", phase="inflight"), "ended")
    store.insert_session(session(3, "2026-11-13T10:00:00Z", phase="preflight"), "ended")

    assert ids(store.session_rows("AST-01", phase="preflight")) == ["01", "03"]
    assert ids(store.session_rows("AST-01", start=at(12), end=at(13))) == ["02"]
    assert ids(store.session_rows("AST-01", start=at(12), end=at(14))) == ["02", "03"]
    assert ids(store.session_rows("AST-01", start=at(12))) == ["02", "03"]
    assert ids(store.session_rows("AST-01", end=at(12))) == ["01"]


def test_latest_session_is_the_most_recent_of_any_state(store):
    assert store.latest_session("AST-01") is None
    store.insert_session(session(1, "2026-11-10T10:00:00Z"), "ended")
    store.insert_session(session(2, "2026-11-12T10:00:00Z"), "rejected")

    latest = store.latest_session("AST-01")

    assert latest is not None
    assert (latest.payload["session_id"][-2:], latest.state) == ("02", "rejected")


def test_assessments_between_is_inclusive_oldest_first_and_unbounded(store):
    for n, day in enumerate((9, 10, 11, 12)):
        assessment = example("domain/assessment.amber")
        assessment.update(assessment_id=f"a55e55e0-0000-4000-8000-0000000000{n:02d}", as_of=f"2026-11-{day:02d}T09:00:00Z")
        store.put_assessment(assessment)

    found = store.assessments_between("AST-01", at(10), at(11, 9))

    assert [a["as_of"] for a in found] == ["2026-11-10T09:00:00Z", "2026-11-11T09:00:00Z"]


ELONGATION = "stretch.lumbar.peak_elongation_mm"


def feature_set(n: int, *, astronaut: str = "AST-01", feature_id: str = ELONGATION, value: float | None = 5.0) -> dict:
    payload = copy.deepcopy(example("domain/feature-set.preflight"))
    payload.update(session_id=f"3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a{n:02d}", astronaut_id=astronaut)
    payload["features"] = [{"feature_id": feature_id, "value": value, "unit": "mm", "quality": {"score": 1.0, "flags": []}}]
    return payload


def test_latest_feature_set_with_is_the_most_recent_that_has_a_value_for_the_feature(store):
    store.put_feature_set(feature_set(1), phase="preflight", anchor_at="2026-11-10T10:00:00Z")
    store.put_feature_set(feature_set(2), phase="preflight", anchor_at="2026-11-12T10:00:00Z")
    store.put_feature_set(feature_set(3, feature_id="emg.x"), phase="preflight", anchor_at="2026-11-13T10:00:00Z")
    store.put_feature_set(feature_set(4, value=None), phase="preflight", anchor_at="2026-11-13T11:00:00Z")
    store.put_feature_set(feature_set(5, astronaut="AST-02"), phase="preflight", anchor_at="2026-11-13T12:00:00Z")

    latest = store.latest_feature_set_with("AST-01", ELONGATION)

    assert latest is not None
    assert latest["session_id"].endswith("02")


def test_latest_feature_set_with_is_none_without_a_set_for_the_feature(store):
    store.put_feature_set(feature_set(1, feature_id="emg.x"), phase="preflight", anchor_at="2026-11-10T10:00:00Z")

    assert store.latest_feature_set_with("AST-01", ELONGATION) is None
    assert store.latest_feature_set_with("AST-09", "emg.x") is None


# --- restart recovery queries (#59) ---------------------------------------------------------------


def command(n: int, issued_at: str) -> dict:
    payload = copy.deepcopy(example("telemetry/command.stop-session"))
    payload.update(command_id=f"7a1c0e52-5b3d-4c8f-8e21-9d4b6a3c2f{n:02d}", issued_at=issued_at)
    return payload


def test_sessions_in_states_spans_astronauts_ordered_by_start(store):
    store.insert_session(session(3, "2026-11-13T10:00:00Z", astronaut="AST-02"), "running")
    store.insert_session(session(1, "2026-11-10T10:00:00Z"), "starting")
    store.insert_session(session(2, "2026-11-12T10:00:00Z"), "ended")
    store.insert_session(session(4, "2026-11-11T10:00:00Z"), "running")

    rows = store.sessions_in_states(["running", "starting"])

    assert [row.payload["session_id"][-2:] for row in rows] == ["01", "04", "03"]
    assert store.sessions_in_states([]) == []


def test_sessions_in_states_breaks_ties_by_session_id(store):
    store.insert_session(session(2, "2026-11-10T10:00:00Z"), "running")
    store.insert_session(session(1, "2026-11-10T10:00:00Z"), "running")

    assert [row.payload["session_id"][-2:] for row in store.sessions_in_states(["running"])] == ["01", "02"]


def test_commands_in_state_returns_acks_ordered_by_issue_time(store):
    store.insert_command(command(2, "2026-11-10T10:05:00Z"), "sent")
    store.insert_command(command(1, "2026-11-10T10:00:00Z"), "sent")
    store.insert_command(command(3, "2026-11-10T10:10:00Z"), "completed")
    ack = copy.deepcopy(example("telemetry/command-ack.accepted")) | {"command_id": command(1, "2026-11-10T10:00:00Z")["command_id"]}
    store.add_ack(ack)

    rows = store.commands_in_state("sent")

    assert [row.payload["command_id"][-2:] for row in rows] == ["01", "02"]
    assert rows[0].state == "sent"
    assert rows[0].acks == [ack]
    assert rows[1].acks == []
    assert rows[0] == store.command(rows[0].payload["command_id"])
    assert store.commands_in_state("timeout") == []


def test_commands_in_state_breaks_ties_by_command_id(store):
    store.insert_command(command(2, "2026-11-10T10:00:00Z"), "sent")
    store.insert_command(command(1, "2026-11-10T10:00:00Z"), "sent")

    assert [row.payload["command_id"][-2:] for row in store.commands_in_state("sent")] == ["01", "02"]


NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def test_processing_attempt_is_absent_until_a_failure_is_recorded(store):
    assert store.processing_attempt("s1") is None


def test_recording_failures_counts_and_keeps_the_last_error(store):
    assert store.record_processing_failure("s1", "boom", NOW) == 1
    assert store.record_processing_failure("s1", "bang", datetime(2026, 11, 10, 14, 0, tzinfo=timezone(timedelta(hours=2)))) == 2

    attempt = store.processing_attempt("s1")

    assert attempt == ProcessingAttempt("s1", 2, "bang", "2026-11-10T12:00:00.000000Z")


def test_reset_processing_attempts_forgets_the_session_and_is_idempotent(store):
    store.record_processing_failure("s1", "boom", NOW)
    store.record_processing_failure("s2", "boom", NOW)

    store.reset_processing_attempts("s1")
    store.reset_processing_attempts("unknown")

    assert store.processing_attempt("s1") is None
    assert store.processing_attempt("s2") is not None


def test_due_processing_retries_filters_state_attempts_and_time_ordered_by_next_at(store):
    states = ("processing_failed", "processing_failed", "processing_failed", "processing_failed", "processed", "processing_failed")
    for n, state in enumerate(states, start=1):
        store.insert_session(session(n, f"2026-11-10T0{n}:00:00Z"), state)
    ids = {n: session(n, "2026-11-10T00:00:00Z")["session_id"] for n in range(1, 7)}
    store.record_processing_failure(ids[1], "e", NOW + timedelta(minutes=10))  # due, later
    store.record_processing_failure(ids[2], "e", NOW)  # due exactly now, earliest
    store.record_processing_failure(ids[3], "e", NOW + timedelta(hours=1))  # not due yet
    for _ in range(3):
        store.record_processing_failure(ids[4], "e", NOW)  # attempts exhausted
    store.record_processing_failure(ids[5], "e", NOW)  # session no longer failed
    # session 6 is failed but has no attempts row

    assert store.due_processing_retries(NOW + timedelta(minutes=10), max_attempts=3) == [ids[2], ids[1]]
    assert store.due_processing_retries(NOW - timedelta(seconds=1), max_attempts=3) == []


def test_profile_astronaut_ids_lists_every_profile_in_order_and_is_empty_without_any(store):
    assert store.profile_astronaut_ids() == []

    for astronaut in ("AST-02", "AST-01", "AST-03"):
        store.put_profile(example("domain/astronaut-profile") | {"astronaut_id": astronaut})
    store.put_profile(example("domain/astronaut-profile") | {"astronaut_id": "AST-01", "updated_at": "2026-11-11T09:00:00Z"})  # replaces

    assert store.profile_astronaut_ids() == ["AST-01", "AST-02", "AST-03"]


def test_create_daily_context_stores_only_when_the_day_is_empty(store):
    first = example("domain/daily-context")
    other = {**first, "phase": "inflight", "gravity_environment": "microgravity"}

    assert store.create_daily_context(first) is True
    assert store.create_daily_context(other) is False
    assert store.daily_context(first["astronaut_id"], first["date"]) == first
