"""Cord protocols view: latest valid measurement per posture, cadence and the calculated estimate."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from monitor.views.cord import (
    FLEXION,
    STRAIGHT,
    EstimateRequest,
    Measurements,
    build_cord_history,
    build_cord_protocols,
    collect_measurements,
    estimate_request,
    estimate_view,
)
from monitor.views.tasks import tasks_for
from spine_contracts import validation

NOW = datetime(2027, 2, 10, 9, 0, tzinfo=UTC)
FEATURE = {STRAIGHT: "stretch.full_spine.straight_resistance_ohm", FLEXION: "stretch.full_spine.max_flexion_resistance_ohm"}


def row(protocol: str, *, days_ago: float, ohm: float = 4500, gravity: str = "earth_1g", phase: str = "preflight", score: float = 0.9,
        end_reason: str = "completed", session_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """A (session, feature set) pair as the store keeps them."""
    sid = session_id or f"00000000-0000-4000-8000-{int(days_ago * 100):012d}"
    start = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    session = {"session_id": sid, "protocol_id": protocol, "end_reason": end_reason, "mission_context": {"phase": phase, "gravity_environment": gravity}}
    feature_set = {
        "session_id": sid,
        "protocol_id": protocol,
        "gravity_environment": gravity,
        "computed_at": start,
        "features": [{"feature_id": FEATURE[protocol], "value": ohm, "unit": "ohm", "quality": {"score": score, "assessed_by": "engine"}}],
    }
    return session, feature_set


def measures(*rows: tuple[dict[str, Any], dict[str, Any]]) -> Measurements:
    return collect_measurements(list(rows), min_score=0.5)


def test_last_valid_earth_reference_and_follow_up_are_told_apart_by_gravity() -> None:
    m = measures(
        row(STRAIGHT, days_ago=40, ohm=4400),
        row(STRAIGHT, days_ago=30, ohm=4500),
        row(STRAIGHT, days_ago=9, ohm=5500, gravity="microgravity", phase="inflight"),
        row(STRAIGHT, days_ago=2, ohm=5650, gravity="microgravity", phase="inflight"),
    )
    earth, follow_up = m[STRAIGHT]
    assert earth is not None and follow_up is not None
    assert (earth["resistance_ohm"], follow_up["resistance_ohm"], follow_up["gravity_environment"]) == (4500, 5650, "microgravity")
    assert m[FLEXION].earth is None and m[FLEXION].follow_up is None


@pytest.mark.parametrize(
    "bad",
    [
        {"score": 0.4},  # below the minimum quality
        {"end_reason": "aborted_pain"},  # not completed
        {"ohm": 0.0},  # not a resistance
    ],
)
def test_invalid_measurements_are_never_the_last_valid_one(bad: dict[str, Any]) -> None:
    m = measures(row(STRAIGHT, days_ago=30, ohm=4500), row(STRAIGHT, days_ago=1, **{"ohm": 4000, **bad}))
    earth = m[STRAIGHT].earth
    assert earth is not None and earth["resistance_ohm"] == 4500


def test_a_postures_measurements_do_not_leak_into_the_other() -> None:
    m = measures(row(FLEXION, days_ago=3, ohm=5650))
    assert m[STRAIGHT].earth is None and m[FLEXION].earth is not None


def test_a_session_without_the_feature_is_ignored() -> None:
    session, fs = row(STRAIGHT, days_ago=3)
    fs["features"] = []
    assert measures((session, fs))[STRAIGHT].earth is None


def test_earth_measurement_after_launch_is_not_a_reference() -> None:
    m = measures(row(STRAIGHT, days_ago=1, phase="postflight"))
    assert m[STRAIGHT].earth is None and m[STRAIGHT].follow_up is None


def due(m: Measurements, phase: str | None, protocol: str = STRAIGHT, cadence: int = 7) -> tuple[bool, str]:
    result = next(p for p in build_cord_protocols(m, phase, NOW, cadence, {})["postures"] if p["protocol_id"] == protocol)
    return result["due"], result["due_reason"]


def test_preflight_asks_for_the_earth_reference_until_there_is_one() -> None:
    assert due(measures(), "preflight") == (True, "CORD_NO_EARTH_REFERENCE")
    assert due(measures(row(STRAIGHT, days_ago=3)), "preflight") == (False, "CORD_UP_TO_DATE")


def test_inflight_follow_up_follows_the_cadence() -> None:
    def follow(days: float) -> Measurements:
        return measures(row(STRAIGHT, days_ago=days, gravity="microgravity", phase="inflight"))

    assert due(measures(), "inflight") == (True, "CORD_NO_FOLLOW_UP")
    assert due(follow(6.9), "inflight") == (False, "CORD_UP_TO_DATE")
    assert due(follow(7), "inflight") == (True, "CORD_FOLLOW_UP_OVERDUE")
    assert due(follow(7), "inflight", cadence=14) == (False, "CORD_UP_TO_DATE")


def test_each_posture_has_its_own_clock() -> None:
    m = measures(row(STRAIGHT, days_ago=1, gravity="microgravity", phase="inflight"), row(FLEXION, days_ago=20, gravity="microgravity", phase="inflight"))
    assert due(m, "inflight", STRAIGHT) == (False, "CORD_UP_TO_DATE")
    assert due(m, "inflight", FLEXION) == (True, "CORD_FOLLOW_UP_OVERDUE")


@pytest.mark.parametrize("phase", ["postflight", None])
def test_nothing_is_due_outside_preflight_and_flight(phase: str | None) -> None:
    assert due(measures(), phase) == (False, "CORD_UP_TO_DATE")


def test_estimate_request_needs_both_measurements_and_the_straight_back_length() -> None:
    m = measures(row(STRAIGHT, days_ago=30, ohm=4500), row(STRAIGHT, days_ago=1, ohm=5650, gravity="microgravity", phase="inflight"))
    request = estimate_request(STRAIGHT, m[STRAIGHT])
    assert isinstance(request, EstimateRequest)
    assert request.body == {"schema_version": "1.0.0", "baseline_resistance_ohm": 4500, "current_resistance_ohm": 5650, "reference_length_mm": 500}
    assert estimate_request(STRAIGHT, measures(row(STRAIGHT, days_ago=30))[STRAIGHT]) == "ESTIMATE_NO_FOLLOW_UP"
    only_follow = measures(row(STRAIGHT, days_ago=1, gravity="microgravity", phase="inflight"))
    assert estimate_request(STRAIGHT, only_follow[STRAIGHT]) == "ESTIMATE_NO_EARTH_REFERENCE"
    flexed = measures(row(FLEXION, days_ago=30), row(FLEXION, days_ago=1, gravity="microgravity", phase="inflight"))
    assert estimate_request(FLEXION, flexed[FLEXION]) == "ESTIMATE_REFERENCE_LENGTH_UNKNOWN"


def test_the_view_labels_the_estimate_as_calculated_and_conforms_to_the_contract() -> None:
    m = measures(row(STRAIGHT, days_ago=30, ohm=4500), row(STRAIGHT, days_ago=1, ohm=5650, gravity="microgravity", phase="inflight"))
    request = estimate_request(STRAIGHT, m[STRAIGHT])
    assert isinstance(request, EstimateRequest)
    answer = {"schema_version": "1.0.0", "estimated_length_mm": 538.5, "elongation_mm": 38.5, "model": "experimental_uniform_resistance_v1"}
    estimate = estimate_view(request, answer)
    view = build_cord_protocols(m, "inflight", NOW, 7, {STRAIGHT: estimate, FLEXION: "ESTIMATE_NO_EARTH_REFERENCE"})
    assert validation.errors("app/ui-cord-protocols", view) == []
    straight, flexion = view["postures"]
    flags = ("calculated", "measured_length", "clinically_validated", "affects_triage")
    assert [straight["estimate"][f] for f in flags] == [True, False, False, False]
    assert straight["estimate"]["reference_length_mm"] == 500 and straight["estimate_unavailable"] is None
    assert flexion["estimate"] is None and flexion["estimate_unavailable"] == "ESTIMATE_NO_EARTH_REFERENCE"


def test_cord_tasks_come_after_the_existing_ones_in_a_fixed_order() -> None:
    assert tasks_for(True, True, True, False, cord_due=(STRAIGHT, FLEXION)) == [
        "TASK_DAILY_REPORT", "TASK_ODI", "TASK_BASELINE_SESSION", "TASK_SESSION", "TASK_CORD_STRAIGHT", "TASK_CORD_MAX_FLEXION",
    ]  # fmt: skip
    assert tasks_for(False, False, False, True, cord_due=(FLEXION,)) == ["TASK_CORD_MAX_FLEXION"]
    assert tasks_for(False, False, False, True) == []

def test_history_lists_valid_and_invalid_measurements_with_the_reason_newest_first() -> None:
    rows = [
        row(STRAIGHT, days_ago=30, ohm=4500),
        row(STRAIGHT, days_ago=9, ohm=5100, gravity="microgravity", phase="inflight", score=0.3),
        row(STRAIGHT, days_ago=5, ohm=0, gravity="microgravity", phase="inflight"),
        row(STRAIGHT, days_ago=2, ohm=5650, gravity="microgravity", phase="inflight", end_reason="aborted_pain"),
        row(FLEXION, days_ago=1, ohm=5700, gravity="microgravity", phase="inflight"),
    ]
    view = build_cord_history(rows, min_score=0.5, limit=20)
    assert validation.errors("app/ui-cord-history", view) == []
    straight, flexion = view["postures"]
    assert [(m["resistance_ohm"], m["valid"], m["invalid_reason"]) for m in straight["measurements"]] == [
        (5650, False, "CORD_INVALID_NOT_COMPLETED"),
        (0, False, "CORD_INVALID_NO_VALUE"),
        (5100, False, "CORD_INVALID_LOW_QUALITY"),
        (4500, True, None),
    ]
    assert straight["measurements"][0]["gravity_environment"] == "microgravity" and straight["measurements"][3]["quality_score"] == 0.9
    assert [m["valid"] for m in flexion["measurements"]] == [True]
    assert "estimate" not in str(view)


def test_history_is_bounded_per_posture() -> None:
    rows = [row(STRAIGHT, days_ago=d) for d in range(1, 8)]
    kept = build_cord_history(rows, min_score=0.5, limit=3)["postures"][0]["measurements"]
    assert len(kept) == 3
