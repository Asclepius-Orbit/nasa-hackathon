"""GET /ui/.../views/*: the stateful wiring of the pure view builders, validated against their schemas."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from monitor.views.protocol_catalog import protocol_entry
from spine_contracts import validation

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
AST = "AST-01"
LAUNCH = "2026-12-13"  # L-30 on the harness date (2026-11-13): preflight, mission day -30
LAT = "lat_pulldown_standing_v1"
SPINE_CHECK = "spine_length_rest_v1"
ELONGATION = "stretch.full_spine.peak_elongation_mm"


def url(view: str) -> str:
    return f"/ui/crew/{AST}/views/{view}"


async def get(harness, path: str, schema: str, **params: Any) -> dict[str, Any]:
    response = await harness.http.get(path, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert validation.errors(schema, body) == []
    return body


async def put_profile(harness, **extra: Any) -> None:
    profile = harness.example("domain/astronaut-profile") | extra
    assert (await harness.http.put(f"/ui/crew/{AST}/profile", json=profile)).status_code == 200


def session_id(n: int) -> str:
    return f"3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a{n:02d}"


def add_session(harness, n: int, started_at: str, state: str, *, phase: str = "preflight", protocol: str = LAT) -> dict[str, Any]:
    payload = harness.example("domain/session.exercise")
    payload.update(session_id=session_id(n), started_at=started_at, protocol_id=protocol)
    payload.pop("ended_at", None)
    payload.pop("end_reason", None)
    payload["mission_context"]["phase"] = phase
    harness.store.insert_session(payload, state)
    return payload


def add_baseline(harness) -> dict[str, Any]:
    baseline = harness.example("domain/baseline")
    for entry in baseline["entries"]:
        if entry["feature_id"] == ELONGATION:
            entry.update(min=entry["mean"] - 1.0, max=entry["mean"] + 2.0)
    harness.store.put_baseline(baseline)
    return baseline


def add_feature_set(harness, n: int, start: str, elongation: float) -> None:
    fs = harness.example("domain/feature-set.preflight")
    fs.update(session_id=session_id(n), window={"start": start, "end": start})
    for feature in fs["features"]:
        if feature["feature_id"] == ELONGATION:
            feature["value"] = elongation
    harness.store.put_feature_set(fs, phase="preflight", anchor_at=start)


def add_bare_feature_set(harness, n: int, start: str, elongation: float) -> None:
    """A feature set stored without protocol_id nor gravity_environment."""
    fs = harness.example("domain/feature-set.preflight")
    fs.update(session_id=session_id(n), window={"start": start, "end": start})
    fs.pop("protocol_id", None)
    fs.pop("gravity_environment", None)
    for feature in fs["features"]:
        if feature["feature_id"] == ELONGATION:
            feature["value"] = elongation
    harness.store.put_feature_set(fs, phase="preflight", anchor_at=start)


def add_microgravity_feature_set(harness, n: int, start: str) -> None:
    fs = harness.example("domain/feature-set.preflight")
    fs.update(session_id=session_id(n), gravity_environment="microgravity", window={"start": start, "end": start})
    harness.store.put_feature_set(fs, phase="inflight", anchor_at=start)


def add_assessment(harness, as_of: str, level: str = "amber") -> None:
    assessment = harness.example("domain/assessment.amber")
    assessment.update(as_of=as_of, level=level)
    harness.store.put_assessment(assessment)


def add_daily_report(harness, timestamp: str) -> None:
    harness.store.put_self_report(harness.example("domain/self-report.daily") | {"timestamp": timestamp})


# --- contract surface ------------------------------------------------------------------------------


async def test_view_routes_cover_exactly_the_paths_and_methods_of_ui_openapi(harness):
    contract = yaml.safe_load((CONTRACTS / "ui-openapi.yaml").read_text(encoding="utf-8"))
    served = (await harness.http.get("/openapi.json")).json()["paths"]

    # Served under /ui but outside the UI contract: the crew registry (main process only), the export and the command
    # state (the UI follows the session view instead).
    outside = {
        ("/ui/crew/{astronaut_id}", "put"),
        ("/ui/crew/{astronaut_id}/export", "get"),
        ("/ui/commands/{command_id}", "get"),
    }

    def views(paths: dict[str, Any]) -> set[tuple[str, str]]:
        return {(p, m) for p, item in paths.items() if p.startswith("/ui/") or p == "/ui/crew" for m in item} - outside

    assert views(served) == {(p, m) for p, item in contract["paths"].items() for m in item}


# --- strip -----------------------------------------------------------------------------------------


async def test_strip_without_any_data_has_nulls_and_everything_due(harness):
    strip = await get(harness, url("strip"), "app/ui-strip")

    assert strip["mission"] == {"day": None, "start": None, "phase": None}
    assert strip["harness"] is None
    assert strip["reports_due"] == {"daily": True, "weekly_odi": True}
    assert (strip["ground_link"], strip["last_contact_min_ago"]) == (None, None)


async def test_without_ground_windows_the_ground_routes_have_no_link(harness):
    houston = await get(harness, f"/ui/crew/{AST}/houston", "app/ui-houston")
    outbox = await get(harness, f"/ui/crew/{AST}/outbox", "app/ui-outbox")

    assert (houston["summary"], houston["ground_link"]) == (None, None)
    assert (outbox["items"], outbox["ground_link"]) == ([], None)


async def test_strip_with_profile_device_and_reports(harness):
    await put_profile(harness, launch_date=LAUNCH)
    harness.store.put_device_status(harness.example("telemetry/device-status.degraded"))
    add_daily_report(harness, harness.clock().isoformat())

    strip = await get(harness, url("strip"), "app/ui-strip")

    assert strip["mission"] == {"day": -30, "start": LAUNCH, "phase": "preflight"}
    assert strip["harness"]["device_id"] == "wb-01"
    assert strip["harness"]["degraded"] is True
    assert strip["reports_due"] == {"daily": False, "weekly_odi": True}


async def test_strip_a_fresh_device_is_not_stale(harness):
    harness.store.put_device_status(harness.example("telemetry/device-status.degraded") | {"timestamp": "2026-11-13T09:50:00Z"})

    harness_view = (await get(harness, url("strip"), "app/ui-strip"))["harness"]

    assert (harness_view["stale"], harness_view["last_seen_at"]) == (False, "2026-11-13T09:50:00Z")


async def test_strip_an_old_device_status_is_listed_as_stale_not_null(harness):
    harness.store.put_device_status(harness.example("telemetry/device-status.degraded") | {"timestamp": "2026-11-13T09:00:00Z"})

    harness_view = (await get(harness, url("strip"), "app/ui-strip"))["harness"]

    assert harness_view is not None
    assert (harness_view["stale"], harness_view["last_seen_at"], harness_view["device_id"]) == (True, "2026-11-13T09:00:00Z", "wb-01")


async def test_strip_with_device_id_shows_the_paired_device_not_the_latest(harness):
    paired = harness.example("telemetry/device-status.degraded") | {"device_id": "wb-01", "timestamp": "2026-11-13T09:30:00Z"}
    latest = harness.example("telemetry/device-status.degraded") | {"device_id": "sim-01", "timestamp": "2026-11-13T09:55:00Z"}
    harness.store.put_device_status(paired)
    harness.store.put_device_status(latest)

    unpaired = (await get(harness, url("strip"), "app/ui-strip"))["harness"]
    pinned = (await get(harness, url("strip"), "app/ui-strip", device_id="wb-01"))["harness"]

    assert unpaired["device_id"] == "sim-01"
    assert (pinned["device_id"], pinned["last_seen_at"]) == ("wb-01", "2026-11-13T09:30:00Z")


async def test_strip_with_a_device_id_that_never_reported_has_no_harness(harness):
    harness.store.put_device_status(harness.example("telemetry/device-status.degraded"))

    assert (await get(harness, url("strip"), "app/ui-strip", device_id="ghost-01"))["harness"] is None


@pytest.mark.parametrize("bad", ["", "X", "UPPER", "-bad", "a" * 40])
async def test_strip_rejects_an_invalid_device_id(harness, bad):
    response = await harness.http.get(url("strip"), params={"device_id": bad})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_strip_rejects_a_bad_astronaut_id(harness):
    response = await harness.http.get("/ui/crew/nobody/views/strip")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


# --- today -----------------------------------------------------------------------------------------


async def test_today_without_data_is_nodata_with_the_open_tasks(harness):
    today = await get(harness, url("today"), "app/ui-today")

    assert today["level"] == "nodata"
    assert today["lumbar_delta_mm"] is None
    assert today["session_in_progress"] is None
    # Without a profile the phase is unknown, so the baseline task is not asserted.
    assert today["also_today"] == ["TASK_DAILY_REPORT", "TASK_ODI", "TASK_SESSION"]


async def test_today_with_assessment_lumbar_delta_session_and_baseline_pending(harness):
    await put_profile(harness, launch_date=LAUNCH)
    baseline = add_baseline(harness)
    add_assessment(harness, "2026-11-13T09:00:00Z", "amber")
    add_feature_set(harness, 1, "2026-11-12T10:00:00Z", 8.0)
    add_session(harness, 2, "2026-11-13T09:30:00Z", "running")

    today = await get(harness, url("today"), "app/ui-today")

    entry = next(e for e in baseline["entries"] if e["feature_id"] == ELONGATION)
    assert today["level"] == "amber"
    assert today["lumbar_delta_mm"] == pytest.approx(8.0 - entry["mean"])
    assert today["session_in_progress"]["session_id"] == session_id(2)
    assert today["session_in_progress"]["total_reps"] == 60
    # The running session is not complete, so 0 of 3 reference sessions are recorded; one started today, so no TASK_SESSION.
    assert today["also_today"] == ["TASK_DAILY_REPORT", "TASK_ODI", "TASK_BASELINE_SESSION"]


async def test_today_a_rejected_session_today_does_not_count_as_done(harness):
    add_session(harness, 1, "2026-11-13T09:30:00Z", "rejected")

    today = await get(harness, url("today"), "app/ui-today")

    assert "TASK_SESSION" in today["also_today"]
    assert today["session_in_progress"] is None


async def test_today_only_a_session_started_today_utc_removes_the_session_task(harness):
    add_session(harness, 1, "2026-11-12T23:59:59Z", "ended")
    assert "TASK_SESSION" in (await get(harness, url("today"), "app/ui-today"))["also_today"]

    add_session(harness, 2, "2026-11-13T00:00:01Z", "ended")
    assert "TASK_SESSION" not in (await get(harness, url("today"), "app/ui-today"))["also_today"]


async def test_today_baseline_task_needs_preflight_and_fewer_than_required(harness):
    await put_profile(harness, launch_date="2026-11-01", landing_date="2027-05-01")  # inflight today
    inflight = await get(harness, url("today"), "app/ui-today")
    await put_profile(harness, launch_date=LAUNCH)
    for n, day in enumerate(("01", "02", "03"), start=1):
        add_session(harness, n, f"2026-11-{day}T10:00:00Z", "processed")
    complete = await get(harness, url("today"), "app/ui-today")

    assert "TASK_BASELINE_SESSION" not in inflight["also_today"]
    assert "TASK_BASELINE_SESSION" not in complete["also_today"]


async def test_today_lumbar_measured_at_is_the_instant_of_the_measure(harness):
    add_baseline(harness)
    add_feature_set(harness, 1, "2026-11-12T10:00:00Z", 8.0)

    today = await get(harness, url("today"), "app/ui-today")

    assert today["lumbar_delta_mm"] is not None
    assert today["lumbar_measured_at"] == "2026-11-12T10:00:00Z"


async def test_today_without_a_lumbar_delta_has_no_measure_instant(harness):
    add_feature_set(harness, 1, "2026-11-12T10:00:00Z", 8.0)  # a measure, but no baseline

    today = await get(harness, url("today"), "app/ui-today")

    assert (today["lumbar_delta_mm"], today["lumbar_measured_at"]) == (None, None)


async def test_today_a_microgravity_measure_is_compared_with_the_earth_baseline(harness):
    baseline = add_baseline(harness)
    assert {e["gravity_environment"] for e in baseline["entries"] if e["feature_id"] == ELONGATION} == {"earth_1g"}
    add_microgravity_feature_set(harness, 1, "2026-11-12T10:00:00Z")

    today = await get(harness, url("today"), "app/ui-today")

    assert today["lumbar_delta_mm"] is not None


async def test_today_a_feature_set_without_protocol_is_a_null_delta_not_a_500(harness):
    add_baseline(harness)
    add_bare_feature_set(harness, 1, "2026-11-12T10:00:00Z", 8.0)

    today = await get(harness, url("today"), "app/ui-today")

    assert (today["lumbar_delta_mm"], today["lumbar_measured_at"]) == (None, None)


async def test_today_a_session_that_never_ended_is_not_shown_as_in_progress(harness):
    add_session(harness, 1, "2026-11-10T09:30:00Z", "running")  # 3 days old, past stale_session_hours (48)

    today = await get(harness, url("today"), "app/ui-today")

    assert today["session_in_progress"] is None


async def test_today_a_recent_running_session_is_in_progress(harness):
    add_session(harness, 1, "2026-11-12T09:30:00Z", "running")  # 24 hours old

    today = await get(harness, url("today"), "app/ui-today")

    assert today["session_in_progress"]["session_id"] == session_id(1)


async def test_today_the_lumbar_delta_ignores_measures_older_than_the_history_window(harness):
    add_baseline(harness)
    add_feature_set(harness, 1, "2026-09-01T10:00:00Z", 8.0)

    today = await get(harness, url("today"), "app/ui-today")

    assert today["lumbar_delta_mm"] is None


# --- history ---------------------------------------------------------------------------------------


async def test_history_default_window_is_the_last_30_days_ending_today(harness):
    history = await get(harness, url("history"), "app/ui-history")

    assert (history["from"], history["to"]) == ("2026-10-15", "2026-11-13")
    assert len(history["days"]) == 30
    assert {d["level"] for d in history["days"]} == {"nodata"}
    assert history["today_day"] is None
    assert history["preflight_range_mm"] is None


async def test_history_with_data_rows_the_requested_window(harness):
    await put_profile(harness, launch_date=LAUNCH)
    add_baseline(harness)
    add_assessment(harness, "2026-11-12T09:00:00Z", "red")
    add_feature_set(harness, 1, "2026-11-12T10:00:00Z", 9.0)
    add_session(harness, 1, "2026-11-12T10:00:00Z", "processed")
    add_daily_report(harness, "2026-11-12T08:00:00Z")

    history = await get(harness, url("history"), "app/ui-history", **{"from": "2026-11-11", "to": "2026-11-13"})

    assert [d["date"] for d in history["days"]] == ["2026-11-11", "2026-11-12", "2026-11-13"]
    day = history["days"][1]
    assert (day["level"], day["mission_day"], day["pain_nrs"]) == ("red", -31, 5)
    assert day["length_delta_mm"] is not None
    assert day["session"] == {"session_id": session_id(1), "state": "processed", "protocol_id": LAT}
    assert day["report"] == {"kind": "daily", "red_flags": []}
    assert history["days"][0]["level"] == "nodata"
    assert history["today_day"] == -30
    assert history["preflight_range_mm"] == {"lo": -1.0, "hi": 2.0}


async def test_history_preflight_range_uses_a_measure_outside_the_window(harness):
    add_baseline(harness)
    add_feature_set(harness, 1, "2026-09-01T10:00:00Z", 9.0)

    history = await get(harness, url("history"), "app/ui-history", **{"from": "2026-11-12", "to": "2026-11-13"})

    assert history["preflight_range_mm"] == {"lo": -1.0, "hi": 2.0}
    assert all(d["length_delta_mm"] is None for d in history["days"])


async def test_history_preflight_range_follows_the_latest_measure_not_an_older_one(harness):
    baseline = add_baseline(harness)
    other = next(e for e in baseline["entries"] if e["feature_id"] == ELONGATION) | {"protocol_id": "other_v1", "min": 0.0, "max": 100.0}
    harness.store.put_baseline(baseline | {"baseline_id": "b0b0b0b0-0000-4000-8000-000000000002", "version": 2, "entries": [*baseline["entries"], other]})
    add_feature_set(harness, 1, "2026-01-01T10:00:00Z", 7.0)
    add_feature_set(harness, 2, "2026-03-01T10:00:00Z", 9.0)

    history = await get(harness, url("history"), "app/ui-history", **{"from": "2026-11-12", "to": "2026-11-13"})

    assert history["preflight_range_mm"] == {"lo": -1.0, "hi": 2.0}


async def test_history_a_microgravity_measure_gets_a_delta_from_the_earth_baseline(harness):
    add_baseline(harness)
    add_microgravity_feature_set(harness, 1, "2026-11-12T10:00:00Z")

    history = await get(harness, url("history"), "app/ui-history", **{"from": "2026-11-12", "to": "2026-11-12"})

    assert history["days"][0]["length_delta_mm"] is not None
    assert history["preflight_range_mm"] is not None


async def test_history_a_feature_set_without_protocol_is_a_null_delta_not_a_500(harness):
    add_baseline(harness)
    add_bare_feature_set(harness, 1, "2026-11-12T10:00:00Z", 8.0)

    history = await get(harness, url("history"), "app/ui-history", **{"from": "2026-11-12", "to": "2026-11-12"})

    assert history["days"][0]["length_delta_mm"] is None
    assert history["preflight_range_mm"] is None


async def test_history_a_window_of_366_days_is_served(harness):
    history = await get(harness, url("history"), "app/ui-history", **{"from": "2025-11-13", "to": "2026-11-13"})

    assert len(history["days"]) == 366


async def test_history_a_window_of_367_days_is_an_invalid_range(harness):
    response = await harness.http.get(url("history"), params={"from": "2025-11-12", "to": "2026-11-13"})

    assert response.status_code == 422
    body = response.json()
    assert (body["type"], body["status"]) == ("invalid_range", 422)
    assert "366" in body["detail"]
    assert validation.errors("app/error", body) == []


async def test_history_an_open_ended_window_over_the_cap_is_an_invalid_range(harness):
    response = await harness.http.get(url("history"), params={"from": "2020-01-01"})

    assert response.status_code == 422
    assert response.json()["type"] == "invalid_range"


async def test_history_from_after_to_is_an_invalid_range(harness):
    response = await harness.http.get(url("history"), params={"from": "2026-11-13", "to": "2026-11-12"})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert (body["type"], body["status"]) == ("invalid_range", 422)
    assert validation.errors("app/error", body) == []


async def test_history_a_malformed_date_is_a_422_problem(harness):
    response = await harness.http.get(url("history"), params={"from": "yesterday"})

    assert response.status_code == 422
    assert validation.errors("app/error", response.json()) == []


async def test_history_a_missing_end_of_the_window_is_filled_in(harness):
    only_from = await get(harness, url("history"), "app/ui-history", **{"from": "2026-11-10"})
    only_to = await get(harness, url("history"), "app/ui-history", to="2026-11-30")

    assert (only_from["from"], only_from["to"]) == ("2026-11-10", "2026-11-13")
    assert (only_to["from"], only_to["to"]) == ("2026-11-01", "2026-11-30")


# --- baseline and first-day -------------------------------------------------------------------------


async def test_baseline_without_data_lists_the_plan_with_the_default_protocol(harness):
    baseline = await get(harness, url("baseline"), "app/ui-baseline")

    assert (baseline["recorded"], baseline["required"], baseline["reference_version"]) == (0, 3, None)
    assert [(s["status"], s["offset_days"], s["date"], s["protocol_id"]) for s in baseline["sessions"]] == [
        ("planned", -30, None, LAT),
        ("planned", -23, None, LAT),
        ("planned", -16, None, LAT),
    ]


async def test_baseline_with_launch_date_sessions_and_reference(harness):
    await put_profile(harness, launch_date=LAUNCH)
    add_baseline(harness)
    add_session(harness, 1, "2026-11-01T10:00:00Z", "processed")
    add_session(harness, 2, "2026-11-08T10:00:00Z", "running")  # not finished: does not count
    add_session(harness, 3, "2026-11-09T10:00:00Z", "processed", phase="inflight")  # not preflight

    baseline = await get(harness, url("baseline"), "app/ui-baseline")

    assert (baseline["recorded"], baseline["reference_version"], baseline["built_from_sessions"]) == (1, 1, 2)
    assert baseline["sessions"][0] == {"session_id": session_id(1), "protocol_id": LAT, "status": "complete", "offset_days": -30, "date": "2026-11-01"}
    assert [(s["status"], s["date"]) for s in baseline["sessions"][1:]] == [("planned", "2026-11-20"), ("planned", "2026-11-27")]


async def test_first_day_without_profile_has_the_open_tasks_and_the_baseline_view(harness):
    first_day = await get(harness, url("first-day"), "app/ui-first-day")
    baseline = await get(harness, url("baseline"), "app/ui-baseline")

    assert first_day["baseline"] == baseline
    assert first_day["todo"] == ["TASK_DAILY_REPORT", "TASK_ODI", "TASK_SESSION"]


async def test_first_day_in_preflight_lists_the_baseline_task(harness):
    await put_profile(harness, launch_date=LAUNCH)

    first_day = await get(harness, url("first-day"), "app/ui-first-day")

    assert first_day["todo"] == ["TASK_DAILY_REPORT", "TASK_ODI", "TASK_BASELINE_SESSION", "TASK_SESSION"]


# --- session view ----------------------------------------------------------------------------------


async def test_session_view_of_an_unknown_session_is_a_404_problem(harness):
    response = await harness.http.get(f"/ui/sessions/{session_id(99)}/view")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert validation.errors("app/error", response.json()) == []


async def test_session_view_with_a_malformed_id_is_a_422_problem(harness):
    response = await harness.http.get("/ui/sessions/not-a-uuid/view")

    assert response.status_code == 422
    assert validation.errors("app/error", response.json()) == []


async def test_session_view_without_device_status_has_no_sensor_checks(harness):
    payload = add_session(harness, 1, "2026-11-13T09:30:00Z", "running")

    view = await get(harness, f"/ui/sessions/{payload['session_id']}/view", "app/ui-session")

    entry = protocol_entry(LAT)
    assert entry is not None
    assert view["session_id"] == payload["session_id"]
    assert (view["state"], view["protocol_id"], view["total_reps"], view["rest_seconds"]) == ("running", LAT, 60, 120)
    assert view["fit_items"] == [item["id"] for item in entry["fit_items"]]
    assert view["sensor_checks"] == []
    assert view["calibration"] is None


async def test_session_view_carries_the_astronaut_of_the_session(harness):
    payload = add_session(harness, 1, "2026-11-13T09:30:00Z", "running")

    view = await get(harness, f"/ui/sessions/{payload['session_id']}/view", "app/ui-session")

    assert view["astronaut_id"] == payload["astronaut_id"] == AST


async def test_session_view_with_a_stale_device_status_does_not_pass_the_check(harness):
    payload = add_session(harness, 1, "2026-11-13T09:30:00Z", "running")
    harness.store.put_device_status(harness.example("telemetry/device-status.degraded") | {"timestamp": "2026-11-13T09:00:00Z", "status": "ok"})

    view = await get(harness, f"/ui/sessions/{payload['session_id']}/view", "app/ui-session")

    assert [s["status"] for s in view["steps"]][:2] == ["done", "now"]


async def test_session_view_follows_the_flow_through_the_real_session_endpoints(harness):
    await harness.setup_astronaut()
    harness.store.put_device_status(harness.example("telemetry/device-status.degraded") | {"timestamp": harness.clock().isoformat()})
    started, _ = await harness.start_session()
    calibrate = await harness.http.post(f"/ui/sessions/{started}/calibrations", json={"calibration_type": "emg_mvc", "stream_ids": ["emg_lat_left"]})
    assert calibrate.status_code == 202, calibrate.text
    await harness.ack(calibrate.json()["command"], "completed", result={"calibration_id": "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f12"})

    view = await get(harness, f"/ui/sessions/{started}/view", "app/ui-session")

    assert view["calibration"]["acked"] is True
    assert {c["stream"]: c["state"] for c in view["sensor_checks"]}["stretch_spine"] == "stalled"


async def test_session_view_of_a_spine_check_has_no_reps(harness):
    payload = add_session(harness, 1, "2026-11-13T09:30:00Z", "running", protocol=SPINE_CHECK)

    view = await get(harness, f"/ui/sessions/{payload['session_id']}/view", "app/ui-session")

    assert (view["band"], view["total_reps"], view["rest_seconds"]) == (None, None, None)


# --- a view that breaks its schema is never served ---------------------------------------------------


async def test_a_view_that_does_not_conform_is_a_500_problem(harness, monkeypatch):
    import monitor.api as api

    monkeypatch.setattr(api, "build_strip", lambda *args, **kwargs: {"nonsense": True})

    response = await harness.http.get(url("strip"))

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "internal"
    assert validation.errors("app/error", body) == []


async def test_the_500_detail_is_generic_and_the_validation_error_only_goes_to_the_log(harness, monkeypatch, caplog):
    import monitor.api as api

    monkeypatch.setattr(api, "build_strip", lambda *args, **kwargs: {"leaked_field_name": True})

    with caplog.at_level("ERROR", logger="monitor.api"):
        response = await harness.http.get(url("strip"))

    assert response.status_code == 500
    assert "leaked_field_name" not in response.text
    assert "app/ui-strip" not in response.json().get("detail", "")
    assert any("leaked_field_name" in record.getMessage() or "app/ui-strip" in record.getMessage() for record in caplog.records)


async def test_a_non_conforming_view_logs_only_the_pointer_never_the_failing_value(caplog):
    from datetime import date

    from monitor.api import Problem, _conforming
    from monitor.views.history import build_history

    view = build_history(date(2030, 1, 8), date(2030, 1, 8), date(2030, 1, 8), lambda _: None, [], [], [], [], None)
    view["days"][0]["level"] = "SENTINEL_HEALTH_VALUE"

    with caplog.at_level("ERROR", logger="monitor.api"), pytest.raises(Problem) as raised:
        _conforming("app/ui-history", view)

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert raised.value.status == 500
    assert "SENTINEL_HEALTH_VALUE" not in logged
    assert "/days/0/level" in logged
    assert "app/ui-history" in logged
