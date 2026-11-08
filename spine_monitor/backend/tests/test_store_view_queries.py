"""Store queries that back the UI views: session rows of any state and unbounded assessment ranges."""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from monitor.db import Store

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
