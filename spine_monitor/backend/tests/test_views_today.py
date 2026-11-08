"""build_today: the Today screen from the latest assessment; no assessment means 'nodata', never a green."""

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from monitor.views.lumbar import LumbarDelta
from monitor.views.today import build_today
from spine_contracts import validation

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


SESSION = example("domain/session.exercise")  # started 2026-11-13T10:02:31Z
NOW = datetime(2026, 11, 13, 10, 30, tzinfo=UTC)
MEASURED_AT = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
STALE_SESSION_HOURS = 48.0


@dataclass(frozen=True)
class Row:
    payload: dict[str, Any]
    state: str


def delta(value: float | None, measured_at: datetime = MEASURED_AT) -> LumbarDelta | None:
    return None if value is None else LumbarDelta(value, measured_at)


def today(assessment: Any, lumbar: float | None = None, session: Any = None, also: list[str] | None = None, now: datetime = NOW) -> dict[str, Any]:
    view = build_today(assessment, delta(lumbar), session, also or [], now, STALE_SESSION_HOURS)
    assert validation.errors("app/ui-today", view) == []
    return view


def test_no_assessment_is_nodata_with_everything_null_or_empty() -> None:
    assert today(None) == {
        "schema_version": "1.0.0",
        "level": "nodata",
        "assessment_id": None,
        "assessed_at": None,
        "recommendations": [],
        "reasons": [],
        "trust": None,
        "lumbar_delta_mm": None,
        "lumbar_measured_at": None,
        "session_in_progress": None,
        "also_today": [],
    }


def test_no_assessment_still_carries_tasks_lumbar_and_session() -> None:
    view = today(None, 1.5, (Row(SESSION, "running"), 10), ["TASK_ODI"])
    assert view["level"] == "nodata"
    assert view["assessment_id"] is None
    assert view["lumbar_delta_mm"] == 1.5
    assert view["also_today"] == ["TASK_ODI"]
    assert view["session_in_progress"]["total_reps"] == 10


def test_assessment_facts_are_copied_in_order() -> None:
    assessment = example("domain/assessment.amber")
    view = today(assessment)
    assert view["level"] == assessment["level"] != "nodata"
    assert view["assessment_id"] == assessment["assessment_id"]
    assert view["assessed_at"] == assessment["as_of"]
    assert view["recommendations"] == assessment["recommendations"]
    assert view["reasons"] == [
        {"feature_id": d["feature_id"], "deviation_z": d.get("deviation_z"), "contribution": d["contribution"]} for d in assessment["drivers"]
    ]
    assert len(view["reasons"]) >= 2


def test_trust_counts_evaluated_domains_and_copies_missing() -> None:
    assessment = example("domain/assessment.amber")
    assessment["domains"] = [
        {"domain": "spine_length", "level": "amber", "evaluated": True},
        {"domain": "muscle", "level": "green", "evaluated": False},
        {"domain": "kinematics", "level": "green", "evaluated": True},
        {"domain": "symptoms", "level": "green", "evaluated": False},
    ]
    assessment["completeness"]["missing"] = [{"what": "muscle", "reason": "sensor_offline"}]
    assert today(assessment)["trust"] == {
        "confidence": assessment["confidence"],
        "data_available": 2,
        "data_total": 4,
        "missing": [{"what": "muscle", "reason": "sensor_offline"}],
    }


def test_driver_without_deviation_has_null_deviation() -> None:
    assessment = example("domain/assessment.amber")
    assessment["drivers"] = [{"feature_id": "stretch.lumbar.peak_elongation_mm", "value": 3.0, "contribution": 0.4}]
    assert today(assessment)["reasons"] == [{"feature_id": "stretch.lumbar.peak_elongation_mm", "deviation_z": None, "contribution": 0.4}]


def test_assessment_without_domains_has_zero_available_not_full() -> None:
    assessment = example("domain/assessment.amber")
    assessment["domains"] = []
    trust = today(assessment)["trust"]
    assert (trust["data_available"], trust["data_total"]) == (0, 0)


@pytest.mark.parametrize("state", ["starting", "running"])
def test_session_in_progress_for_active_states(state: str) -> None:
    view = today(None, None, (Row(SESSION, state), 10))
    assert view["session_in_progress"] == {
        "session_id": SESSION["session_id"],
        "protocol_id": SESSION["protocol_id"],
        "started_at": SESSION["started_at"],
        "total_reps": 10,
    }


@pytest.mark.parametrize("state", ["rejected", "ended", "processing", "processed", "processing_failed"])
def test_session_not_in_progress_for_other_states(state: str) -> None:
    assert today(None, None, (Row(SESSION, state), 10))["session_in_progress"] is None


def test_session_without_reps_has_null_total_reps() -> None:
    assert today(None, None, (Row(SESSION, "running"), None))["session_in_progress"]["total_reps"] is None


def test_lumbar_delta_is_passed_through_including_zero_and_none() -> None:
    assert today(None, 0.0)["lumbar_delta_mm"] == 0.0
    assert today(None, -2.5)["lumbar_delta_mm"] == -2.5
    assert today(None, None)["lumbar_delta_mm"] is None


def test_lumbar_measured_at_is_the_instant_of_the_measure_in_utc() -> None:
    view = today(None, 1.5)

    assert view["lumbar_measured_at"] == "2026-11-12T10:00:00Z"


def test_lumbar_measured_at_is_null_exactly_when_the_delta_is() -> None:
    assert today(None, None)["lumbar_measured_at"] is None
    assert today(None, 0.0)["lumbar_measured_at"] == "2026-11-12T10:00:00Z"


def test_lumbar_measured_at_is_normalised_to_utc_whatever_the_offset() -> None:
    plus_two = datetime.fromisoformat("2026-11-12T12:00:00+02:00")

    view = build_today(None, LumbarDelta(1.0, plus_two), None, [], NOW, STALE_SESSION_HOURS)

    assert view["lumbar_measured_at"] == "2026-11-12T10:00:00Z"
    assert validation.errors("app/ui-today", view) == []


def test_a_session_older_than_the_staleness_threshold_is_not_in_progress() -> None:
    started = datetime.fromisoformat(SESSION["started_at"])

    assert today(None, None, (Row(SESSION, "running"), 10), now=started + timedelta(hours=48))["session_in_progress"] is not None
    assert today(None, None, (Row(SESSION, "running"), 10), now=started + timedelta(hours=48, seconds=1))["session_in_progress"] is None
    assert today(None, None, (Row(SESSION, "starting"), 10), now=started + timedelta(days=30))["session_in_progress"] is None


def test_the_session_staleness_threshold_is_the_one_given() -> None:
    now = datetime.fromisoformat(SESSION["started_at"]) + timedelta(hours=5)
    row = (Row(SESSION, "running"), 10)

    assert build_today(None, None, row, [], now, 4.0)["session_in_progress"] is None
    assert build_today(None, None, row, [], now, 6.0)["session_in_progress"] is not None


def test_does_not_mutate_the_assessment() -> None:
    assessment = example("domain/assessment.amber")
    before = deepcopy(assessment)
    today(assessment)
    assert assessment == before


@pytest.mark.contract
def test_has_the_keys_of_the_contract_examples() -> None:
    assert set(today(None)) == set(example("app/ui-today.nodata"))
    assert set(today(example("domain/assessment.amber"))["trust"]) == set(example("app/ui-today")["trust"])
