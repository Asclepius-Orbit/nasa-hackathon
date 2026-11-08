"""Baseline and first-day views: progress of the pre-flight reference sessions against the plan."""

from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from monitor.db import SessionRow
from monitor.views.baseline import COMPLETE_STATES, build_baseline, build_first_day
from spine_contracts import SCHEMA_VERSION, validation

DEFAULT = "lat_pulldown_standing_v1"
LAUNCH = date(2026, 11, 15)
OFFSETS = [-30, -23, -16]


def session(started_at: str, *, state: str = "processed", phase: str = "preflight", protocol: str = DEFAULT, session_id: str | None = None) -> SessionRow:
    payload = {
        "session_id": session_id or str(uuid4()),
        "started_at": started_at,
        "protocol_id": protocol,
        "mission_context": {"phase": phase, "gravity_environment": "earth_1g"},
    }
    return SessionRow(payload, state, None)


def view(
    sessions: list[SessionRow] | None = None,
    baseline: dict[str, Any] | None = None,
    launch: date | None = LAUNCH,
    required: int = 3,
    offsets: list[int] | None = None,
):
    return build_baseline(baseline, sessions or [], launch, required, OFFSETS if offsets is None else offsets, DEFAULT)


def test_complete_states_are_the_finished_ones_without_failures():
    assert set(COMPLETE_STATES) == {"ended", "processing", "processed"}


# --- build_baseline ------------------------------------------------------------------------------


def test_nothing_recorded_everything_planned_from_the_launch_date():
    result = view()

    assert validation.errors("app/ui-baseline", result) == []
    assert result == {
        "schema_version": SCHEMA_VERSION,
        "recorded": 0,
        "required": 3,
        "reference_version": None,
        "built_from_sessions": 0,
        "sessions": [
            {"session_id": None, "protocol_id": DEFAULT, "status": "planned", "offset_days": -30, "date": "2026-10-16"},
            {"session_id": None, "protocol_id": DEFAULT, "status": "planned", "offset_days": -23, "date": "2026-10-23"},
            {"session_id": None, "protocol_id": DEFAULT, "status": "planned", "offset_days": -16, "date": "2026-10-30"},
        ],
    }


def test_recorded_sessions_take_the_first_plan_positions_in_started_at_order():
    later = session("2026-10-23T09:00:00Z", session_id="3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1b02", protocol="spine_length_rest_v1")
    earlier = session("2026-10-16T09:00:00Z", session_id="3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1b01")
    baseline = {"version": 1, "built_from_session_ids": ["a", "b"]}

    result = view([later, earlier], baseline)

    assert validation.errors("app/ui-baseline", result) == []
    assert (result["recorded"], result["required"], result["reference_version"], result["built_from_sessions"]) == (2, 3, 1, 2)
    assert result["sessions"] == [
        {"session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1b01", "protocol_id": DEFAULT, "status": "complete", "offset_days": -30, "date": "2026-10-16"},
        {
            "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1b02",
            "protocol_id": "spine_length_rest_v1",
            "status": "complete",
            "offset_days": -23,
            "date": "2026-10-23",
        },
        {"session_id": None, "protocol_id": DEFAULT, "status": "planned", "offset_days": -16, "date": "2026-10-30"},
    ]


def test_the_date_of_a_recorded_session_is_its_utc_day():
    result = view([session("2026-10-16T23:30:00-05:00")])

    assert result["sessions"][0]["date"] == "2026-10-17"


def test_recorded_sessions_keep_their_date_without_launch_date_but_planned_ones_are_null():
    result = view([session("2026-10-16T09:00:00Z")], launch=None)

    assert validation.errors("app/ui-baseline", result) == []
    assert [s["date"] for s in result["sessions"]] == ["2026-10-16", None, None]
    assert [s["offset_days"] for s in result["sessions"]] == OFFSETS


@pytest.mark.parametrize("state", ["starting", "running", "rejected", "processing_failed"])
def test_sessions_in_other_states_do_not_count(state):
    result = view([session("2026-10-16T09:00:00Z", state=state)])

    assert result["recorded"] == 0
    assert all(s["status"] == "planned" for s in result["sessions"])


@pytest.mark.parametrize("state", ["ended", "processing", "processed"])
def test_ended_processing_and_processed_sessions_count(state):
    assert view([session("2026-10-16T09:00:00Z", state=state)])["recorded"] == 1


@pytest.mark.parametrize("phase", ["inflight", "postflight"])
def test_only_preflight_sessions_count(phase):
    assert view([session("2026-10-16T09:00:00Z", phase=phase)])["recorded"] == 0


def test_more_sessions_than_the_plan_count_as_recorded_but_only_plan_positions_are_listed():
    sessions = [session(f"2026-10-{day}T09:00:00Z") for day in (10, 11, 12, 13, 14)]

    result = view(sessions)

    assert validation.errors("app/ui-baseline", result) == []
    assert result["recorded"] == 5
    assert [(s["status"], s["date"]) for s in result["sessions"]] == [("complete", "2026-10-10"), ("complete", "2026-10-11"), ("complete", "2026-10-12")]


def test_every_plan_position_is_complete_when_exactly_the_required_were_recorded():
    result = view([session(f"2026-10-{day}T09:00:00Z") for day in (16, 23, 30)])

    assert [s["status"] for s in result["sessions"]] == ["complete"] * 3
    assert result["recorded"] == 3


def test_required_and_plan_are_independent():
    result = view(required=5, offsets=[-30, -20])

    assert (result["required"], [s["offset_days"] for s in result["sessions"]]) == (5, [-30, -20])


def test_no_baseline_means_null_version_and_zero_sessions_built():
    result = view(baseline=None)

    assert (result["reference_version"], result["built_from_sessions"]) == (None, 0)


def test_baseline_version_and_session_count():
    result = view(baseline={"version": 4, "built_from_session_ids": ["a", "b", "c"]})

    assert (result["reference_version"], result["built_from_sessions"]) == (4, 3)


def test_required_below_one_is_refused():
    with pytest.raises(ValueError, match="required"):
        view(required=0)


# --- build_first_day -----------------------------------------------------------------------------


def test_first_day_wraps_todo_and_the_baseline_view():
    baseline_view = view([session("2026-10-16T09:00:00Z")])

    result = build_first_day(["TASK_BASELINE_SESSION", "TASK_DAILY_REPORT"], baseline_view)

    assert validation.errors("app/ui-first-day", result) == []
    assert result == {"schema_version": SCHEMA_VERSION, "todo": ["TASK_BASELINE_SESSION", "TASK_DAILY_REPORT"], "baseline": baseline_view}


def test_first_day_keeps_the_order_and_drops_repeated_tasks():
    result = build_first_day(["TASK_ODI", "TASK_DAILY_REPORT", "TASK_ODI"], view())

    assert result["todo"] == ["TASK_ODI", "TASK_DAILY_REPORT"]


def test_first_day_with_nothing_to_do():
    result = build_first_day([], view())

    assert result["todo"] == []
    assert validation.errors("app/ui-first-day", result) == []


# --- properties ----------------------------------------------------------------------------------

SESSION_SPEC = st.tuples(
    st.integers(1, 28),
    st.sampled_from(["ended", "processing", "processed", "running", "rejected", "processing_failed"]),
    st.sampled_from(["preflight", "inflight"]),
)


@pytest.mark.property
@given(
    specs=st.lists(SESSION_SPEC, max_size=8),
    offsets=st.lists(st.integers(-60, -1), max_size=5, unique=True),
    required=st.integers(1, 6),
    launch=st.none() | st.dates(min_value=date(2026, 1, 1), max_value=date(2028, 1, 1)),
)
def test_property_plan_positions_are_complete_then_planned(specs, offsets, required, launch):
    sessions = [session(f"2026-10-{day:02d}T09:00:00Z", state=state, phase=phase) for day, state, phase in specs]
    counted = sum(1 for _, state, phase in specs if state in COMPLETE_STATES and phase == "preflight")

    result = view(sessions, launch=launch, required=required, offsets=sorted(offsets))
    statuses = [s["status"] for s in result["sessions"]]

    assert validation.errors("app/ui-baseline", result) == []
    assert result["recorded"] == counted
    assert len(statuses) == len(offsets)
    assert statuses == ["complete"] * min(counted, len(offsets)) + ["planned"] * (len(offsets) - min(counted, len(offsets)))
    assert [s["offset_days"] for s in result["sessions"]] == sorted(offsets)
    assert all((s["session_id"] is None) == (s["status"] == "planned") for s in result["sessions"])
    assert all(s["date"] is None for s in result["sessions"] if s["status"] == "planned" and launch is None)
