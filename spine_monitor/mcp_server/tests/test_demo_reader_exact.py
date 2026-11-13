"""Valores exactos del lector de demo para un reloj fijo (2026-11-13T12:00:00Z): cada marca de tiempo, nivel, riesgo y límite."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from spine_contracts import dump
from spine_mcp_server.demo_data import build_demo_app_reader, build_demo_assess_base
from spine_mcp_server.errors import UpstreamError

AST = "AST-01"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
OTHER_NOW = datetime(2026, 11, 6, 9, 30, tzinfo=UTC)  # redondea a 09:00
NAMESPACE = uuid.UUID("a5c1e910-0000-4000-8000-0d3e0da7a000")
SECOND = timedelta(seconds=1)
WIDE = timedelta(days=30)
BASELINE_ID = "b45e11e0-0000-4000-8000-0000000000b1"

# oldest -> newest (k = 13 .. 0, t = 0 .. 13)
RISKS = [0.18, 0.2, 0.22, 0.24, 0.26, 0.28, 0.3, 0.32, 0.4, 0.44, 0.49, 0.53, 0.58, 0.62]
DRIVER_STRETCH = [6.6, 6.6, 6.72, 6.72, 6.84, 6.84, 6.96, 6.96, 7.7, 7.7, 8.4, 8.4, 9.1, 9.1]
DRIVER_STRETCH_Z = [0.1, 0.1, 0.2, 0.2, 0.3, 0.3, 0.4, 0.4, 1.1, 1.1, 1.8, 1.8, 2.4, 2.4]
DRIVER_PAIN = [2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.1, 2.3, 2.4, 2.7, 3.0, 3.4, 3.9, 4.1]
DAILY_PAIN = [2, 2, 2, 2, 2, 2, 3, 3, 3, 4, 4, 5, 5, 5]
SLEEP_HOURS = [7.2, 7.1, 7.0, 6.8, 6.7, 6.6, 6.5, 6.4, 6.2, 6.1, 6.0, 5.9, 5.8, 5.6]
SLEEP_QUALITY = [8, 8, 7, 7, 7, 6, 6, 6, 6, 5, 5, 5, 4, 4]


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(kind: str, moment: datetime) -> str:
    return str(uuid.uuid5(NAMESPACE, f"AST-01/{kind}/{_iso(moment)}"))


def _day(k: int, hour: int = 12, minute: int = 0) -> datetime:
    """Instante de hace `k` días respecto a NOW, a la hora indicada."""
    return (NOW - timedelta(days=k)).replace(hour=hour, minute=minute)


def _history(now: datetime = NOW, limit: int = 100) -> dict[str, Any]:
    return dump(build_demo_app_reader(now).get_history(AST, now - WIDE, now, limit))


def _sessions(start: datetime = NOW - WIDE, end: datetime = NOW, limit: int = 100) -> dict[str, Any]:
    return dump(build_demo_app_reader(NOW).get_sessions(AST, start, end, limit))


def _reports(start: datetime = NOW - WIDE, end: datetime = NOW, limit: int = 100) -> dict[str, Any]:
    return dump(build_demo_app_reader(NOW).get_self_reports(AST, start, end, limit))


def test_assessments_are_one_per_day_oldest_first_with_the_exact_risk_ladder():
    assessments = _history()["assessments"]
    assert [a["as_of"] for a in assessments] == [_iso(_day(k)) for k in range(13, -1, -1)]
    assert [a["risk_score"] for a in assessments] == RISKS
    assert [a["level"] for a in assessments] == ["green"] * 8 + ["amber"] * 6
    assert [a["mission_day"] for a in assessments] == list(range(74, 88))
    assert [a["assessment_id"] for a in assessments] == [_id("assessment", _day(k)) for k in range(13, -1, -1)]
    assert {a["astronaut_id"] for a in assessments} == {AST}


def test_assessment_drivers_and_domains_follow_the_stretch_and_pain_tables():
    assessments = _history()["assessments"]
    for t, a in enumerate(assessments):
        stretch, pain = a["drivers"]
        assert (stretch["feature_id"], stretch["unit"], stretch["value"], stretch["deviation_z"]) == (
            "stretch.full_spine.peak_elongation_mm",
            "mm",
            DRIVER_STRETCH[t],
            DRIVER_STRETCH_Z[t],
        )
        assert (pain["feature_id"], pain["unit"], pain["value"]) == ("symptoms.pain_nrs_7d_mean", "nrs", DRIVER_PAIN[t])
        assert pain["deviation_z"] == pytest.approx((DRIVER_PAIN[t] - 2.0) / 1.2, abs=0.051)
        assert stretch["contribution"] == pytest.approx(min(0.35, max(0.02, 0.13 * DRIVER_STRETCH_Z[t])), abs=0.0051)
        assert pain["contribution"] == pytest.approx(min(0.25, max(0.02, 0.1 * pain["deviation_z"])), abs=0.0051)
        domains = {d["domain"]: d for d in a["domains"]}
        assert [d["domain"] for d in a["domains"]] == ["spine_length", "muscle", "kinematics", "symptoms"]
        assert all(d["evaluated"] is True for d in a["domains"])
        assert domains["muscle"]["score"] == pytest.approx(0.2 + 0.027 * t, abs=0.0051)
        assert domains["kinematics"]["score"] == 0.2
        assert domains["spine_length"]["score"] == pytest.approx(min(1.0, max(0.0, 0.1 + 0.25 * DRIVER_STRETCH_Z[t])), abs=0.0051)
        assert domains["symptoms"]["score"] == pytest.approx(min(1.0, max(0.0, 0.1 + 0.22 * pain["deviation_z"])), abs=0.0051)
        for d in a["domains"]:
            assert d["level"] == ("green" if d["score"] < 0.4 else "amber" if d["score"] < 0.8 else "red")
        assert a["confidence"] == pytest.approx(0.80 - 0.005 * t, abs=0.0051)


def test_assessment_trend_rules_recommendations_and_provenance_per_day():
    assessments = _history()["assessments"]
    for t, a in enumerate(assessments):
        k = 13 - t
        if t < 8:
            assert a["trend"] == {"projected_level": "green", "days_to_threshold": None}
            assert a["recommendations"] == []
        else:
            assert a["trend"] == {"projected_level": "red", "days_to_threshold": 12.0 + 2 * k}
            assert a["recommendations"] == (["REC_LUMBAR_STAB_01", "REC_INCREASE_ARED"] if t >= 10 else ["REC_LUMBAR_STAB_01"])
        assert [r["rule_id"] for r in a["fired_rules"]] == (["RULE_SYMPTOM_PAIN_TREND"] if t >= 10 else [])
        assert a["escalation"] == {"required": False, "reasons": []}
        session_k = k if k % 2 == 1 else k + 1
        assert a["based_on"]["session_ids"] == [_id("session", _day(session_k, 10, 30))]
        assert a["based_on"]["self_report_ids"] == [_id("self_report", _day(k, 11, 30))]
        assert a["based_on"]["baseline_id"] == BASELINE_ID
        assert a["based_on"]["baseline_version"] == 1


def test_exercise_features_are_computed_every_other_day_three_minutes_after_the_session():
    feature_sets = _history()["feature_sets"]
    ks = [13, 11, 9, 7, 5, 3, 1]
    assert [f["computed_at"] for f in feature_sets] == [_iso(_day(k, 10, 39)) for k in ks]
    assert [f["session_id"] for f in feature_sets] == [_id("session", _day(k, 10, 30)) for k in ks]
    for k, f in zip(ks, feature_sets, strict=True):
        t = 13 - k
        assert f["window"] == {"start": _iso(_day(k, 10, 31)), "end": _iso(_day(k, 10, 36))}
        assert f["skipped"] == [{"feature_id": "emg.latissimus_dorsi.right.median_freq_hz", "reason": "stream_missing"}]
        values = {x["feature_id"]: x for x in f["features"]}
        assert values["emg.latissimus_dorsi.left.rms_pct_mvc"]["value"] == pytest.approx(41.0 + 0.5 * t, abs=0.051)
        assert values["emg.latissimus_dorsi.left.median_freq_hz"]["value"] == pytest.approx(87.3 - 0.65 * t, abs=0.051)
        assert values["imu.t9.sagittal.rom_deg"]["value"] == pytest.approx(28.4 + 0.2167 * t, abs=0.051)
        assert values["stretch.full_spine.peak_elongation_mm"]["value"] == (round(6.6 + 0.06 * t, 2) if t < 8 else round(7.0 + 0.35 * (t - 6), 2))
        flags = values["emg.latissimus_dorsi.left.median_freq_hz"]["quality"]["flags"]
        assert (flags == []) if t < 8 else (flags != [])


def test_history_window_block():
    history = _history()
    assert (history["from"], history["to"], history["limit"], history["truncated"]) == (_iso(NOW - WIDE), _iso(NOW), 100, False)
    assert (history["astronaut_id"], history["schema_version"]) == (AST, "1.0.0")


def test_sessions_are_the_exact_exercise_and_spine_check_timestamps_in_order():
    items = _sessions()["items"]
    exercise = [(_day(k, 10, 30), _day(k, 10, 36), "exercise") for k in (13, 11, 9, 7, 5, 3, 1)]
    checks = [(_day(k, 10, 0), _day(k, 10, 5), "spine_check") for k in (8, 4, 0)]
    expected = sorted(exercise + checks)
    assert [(i["started_at"], i["ended_at"], i["session_type"]) for i in items] == [(_iso(s), _iso(e), kind) for s, e, kind in expected]
    assert [i["session_id"] for i in items] == [_id("session", s) for s, _, _ in expected]
    assert {i["source"] for i in items} == {"simulator"}
    assert {i["end_reason"] for i in items} == {"completed"}
    assert {i["mission_context"]["phase"] for i in items} == {"inflight"}
    assert {i["mission_context"]["gravity_environment"] for i in items} == {"microgravity"}
    assert [i["protocol_id"] for i in items if i["session_type"] == "exercise"] == ["lat_pulldown_standing_v1"] * 7
    assert [i["protocol_id"] for i in items if i["session_type"] == "spine_check"] == ["spine_length_rest_v1"] * 3


def test_exercise_events_keep_the_offsets_of_the_contract_example_from_the_start():
    first = _sessions()["items"][0]
    assert first["started_at"] == "2026-10-31T10:30:00Z"
    assert [(e["event_type"], e["timestamp"]) for e in first["events"]] == [
        ("set_start", "2026-10-31T10:30:37Z"),
        ("rep_start", "2026-10-31T10:30:39.500000Z"),
        ("rep_end", "2026-10-31T10:30:43.100000Z"),
        ("rep_start", "2026-10-31T10:31:29.900000Z"),
        ("rep_end", "2026-10-31T10:31:33.500000Z"),
        ("set_end", "2026-10-31T10:31:34Z"),
        ("rest_start", "2026-10-31T10:31:34.200000Z"),
        ("rest_end", "2026-10-31T10:32:04Z"),
    ]


def test_self_reports_are_daily_at_half_past_eleven_plus_one_weekly_odi():
    items = _reports()["items"]
    daily = [i for i in items if i["kind"] == "daily"]
    assert [i["timestamp"] for i in daily] == [_iso(_day(k, 11, 30)) for k in range(13, -1, -1)]
    assert [i["mission_day"] for i in daily] == list(range(74, 88))
    assert [i["report_id"] for i in daily] == [_id("self_report", _day(k, 11, 30)) for k in range(13, -1, -1)]
    assert [i["pain_nrs"] for i in daily] == DAILY_PAIN
    assert [i["pain_nrs_worst_24h"] for i in daily] == [p + 1 for p in DAILY_PAIN]
    assert [i["stiffness_nrs"] for i in daily] == [p + 1 for p in DAILY_PAIN]
    assert [i["sleep"]["hours"] for i in daily] == SLEEP_HOURS
    assert [i["sleep"]["quality_nrs"] for i in daily] == SLEEP_QUALITY
    odi = [i for i in items if i["kind"] == "weekly_odi"]
    assert len(odi) == 1
    assert (odi[0]["timestamp"], odi[0]["mission_day"], odi[0]["report_id"]) == ("2026-11-11T11:15:00Z", 85, _id("self_report", _day(2, 11, 15)))
    assert len(items) == 15
    assert [i["timestamp"] for i in items] == sorted(i["timestamp"] for i in items)
    assert items[11] == odi[0]  # el ODI va justo antes del autoinforme diario de ese día (11:15 < 11:30)


def test_status_block():
    status = dump(build_demo_app_reader(NOW).get_status(AST))
    assert (status["generated_at"], status["astronaut_id"]) == ("2026-11-13T12:00:00Z", AST)
    assert status["today"] == {"date": "2026-11-13", "mission_day": 87, "phase": "inflight", "gravity_environment": "microgravity"}
    assert status["latest_session"]["started_at"] == "2026-11-13T10:00:00Z"
    assert status["latest_session"]["session_type"] == "spine_check"
    assert status["latest_session"]["session_id"] == _id("session", _day(0, 10, 0))
    assert status["latest_self_report"]["timestamp"] == "2026-11-13T11:30:00Z"
    assert status["latest_assessment"]["as_of"] == "2026-11-13T12:00:00Z"
    assert status["latest_assessment"]["risk_score"] == 0.62
    assert status["active_baseline"] == {"baseline_id": BASELINE_ID, "version": 1, "created_at": "2026-10-29T12:00:00Z"}
    (device,) = status["devices"]
    assert (device["timestamp"], device["status"], device["battery_pct"]) == ("2026-11-13T10:30:00Z", "ok", 64.0)
    assert {s["state"] for s in device["streams"]} == {"active"}
    assert status["freshness"] == [
        {"what": "assessment", "last_at": "2026-11-13T12:00:00Z", "age_hours": 0.0, "stale": False},
        {"what": "session", "last_at": "2026-11-13T10:00:00Z", "age_hours": 2.0, "stale": False},
        {"what": "self_report", "last_at": "2026-11-13T11:30:00Z", "age_hours": 0.5, "stale": False},
        {"what": "device", "device_id": "wb-01", "last_at": "2026-11-13T10:30:00Z", "age_hours": 1.5, "stale": False},
    ]


def test_second_clock_shifts_every_instant_and_rounds_to_the_hour():
    reader = build_demo_app_reader(OTHER_NOW)
    status = dump(reader.get_status(AST))
    assert status["generated_at"] == "2026-11-06T09:00:00Z"
    assert status["today"]["date"] == "2026-11-06"
    assert status["latest_assessment"]["as_of"] == "2026-11-06T09:00:00Z"
    assert status["latest_session"]["started_at"] == "2026-11-06T07:00:00Z"
    assert status["latest_self_report"]["timestamp"] == "2026-11-06T08:30:00Z"
    assert status["active_baseline"]["created_at"] == "2026-10-22T09:00:00Z"
    assert _history(OTHER_NOW)["assessments"][0]["as_of"] == "2026-10-24T09:00:00Z"


def test_ids_are_deterministic_and_change_with_the_clock():
    a = _history()["assessments"][-1]["assessment_id"]
    assert a == _history()["assessments"][-1]["assessment_id"]
    assert a != _history(NOW + timedelta(hours=1))["assessments"][-1]["assessment_id"]
    assert a == _history(NOW + timedelta(minutes=59))["assessments"][-1]["assessment_id"]


# --- filtros y límites en los bordes exactos -------------------------------------------------

FIRST_SESSION = _day(13, 10, 30)
LAST_SESSION = _day(0, 10, 0)


@pytest.mark.parametrize(
    ("start", "end", "count"),
    [
        (FIRST_SESSION, LAST_SESSION, 10),
        (FIRST_SESSION - SECOND, LAST_SESSION + SECOND, 10),
        (FIRST_SESSION + SECOND, LAST_SESSION, 9),
        (FIRST_SESSION, LAST_SESSION - SECOND, 9),
        (FIRST_SESSION, FIRST_SESSION, 1),
        (LAST_SESSION, LAST_SESSION, 1),
        (FIRST_SESSION - SECOND, FIRST_SESSION - SECOND, 0),
    ],
)
def test_session_window_is_inclusive_on_both_ends_to_the_second(start: datetime, end: datetime, count: int):
    assert len(_sessions(start, end)["items"]) == count


def test_session_limit_keeps_the_most_recent_and_flags_truncation():
    full = _sessions()["items"]
    cut = _sessions(limit=3)
    assert cut["items"] == full[-3:]
    assert (cut["truncated"], cut["limit"]) == (True, 3)
    assert _sessions(limit=10)["truncated"] is False
    assert _sessions(limit=9)["truncated"] is True
    assert len(_sessions(limit=10)["items"]) == 10


@pytest.mark.parametrize(
    ("start", "end", "count"),
    [
        (_day(13, 11, 30), _day(0, 11, 30), 15),
        (_day(13, 11, 30) + SECOND, _day(0, 11, 30), 14),
        (_day(13, 11, 30), _day(0, 11, 30) - SECOND, 14),
        (_day(2, 11, 15), _day(2, 11, 15), 1),
        (_day(2, 11, 15), _day(2, 11, 30), 2),
        (_day(2, 11, 15) + SECOND, _day(2, 11, 30) - SECOND, 0),
    ],
)
def test_self_report_window_is_inclusive_on_both_ends_to_the_second(start: datetime, end: datetime, count: int):
    assert len(_reports(start, end)["items"]) == count


def test_self_report_limit_keeps_the_most_recent_and_flags_truncation():
    full = _reports()["items"]
    cut = _reports(limit=4)
    assert cut["items"] == full[-4:]
    assert cut["truncated"] is True
    assert _reports(limit=15)["truncated"] is False
    assert _reports(limit=14)["truncated"] is True


@pytest.mark.parametrize(
    ("start", "end", "assessments", "feature_sets"),
    [
        (_day(13, 10, 39), _day(0), 14, 7),
        (_day(13, 10, 39) + SECOND, _day(0), 14, 6),
        (_day(13), _day(0), 14, 6),
        (_day(13) + SECOND, _day(0), 13, 6),
        (_day(13, 10, 39), _day(0) - SECOND, 13, 7),
        (_day(1, 10, 39), _day(1, 10, 39), 0, 1),
        (_day(1, 10, 39) + SECOND, _day(0), 2, 0),
        (_day(5), _day(5), 1, 0),
    ],
)
def test_history_window_is_inclusive_on_both_ends_to_the_second(start: datetime, end: datetime, assessments: int, feature_sets: int):
    history = dump(build_demo_app_reader(NOW).get_history(AST, start, end))
    assert (len(history["assessments"]), len(history["feature_sets"])) == (assessments, feature_sets)
    assert (history["from"], history["to"]) == (_iso(start), _iso(end))


def test_history_limit_keeps_the_most_recent_and_truncates_when_either_list_exceeds_it():
    full = _history()
    cut = _history(limit=5)
    assert cut["assessments"] == full["assessments"][-5:]
    assert cut["feature_sets"] == full["feature_sets"][-5:]
    assert (cut["truncated"], cut["limit"]) == (True, 5)
    assert _history(limit=14)["truncated"] is False
    assert _history(limit=13)["truncated"] is True
    assert _history(limit=7)["truncated"] is True  # 7 feature sets caben, 14 valoraciones no


# --- errores y explicaciones -----------------------------------------------------------------


def test_every_reader_method_rejects_an_unknown_astronaut_with_a_404():
    reader = build_demo_app_reader(NOW)
    calls = [
        lambda: reader.get_status("AST-99"),
        lambda: reader.get_history("AST-99", NOW - WIDE, NOW),
        lambda: reader.get_sessions("AST-99", NOW - WIDE, NOW),
        lambda: reader.get_self_reports("AST-99", NOW - WIDE, NOW),
        lambda: build_demo_assess_base(NOW).get_base("AST-99"),
    ]
    for call in calls:
        with pytest.raises(UpstreamError) as caught:
            call()
        assert (caught.value.status, caught.value.title, caught.value.detail) == (404, "Astronaut not found", "No astronaut with id AST-99.")


def test_unknown_explanation_is_a_404_for_assessment():
    with pytest.raises(UpstreamError) as caught:
        build_demo_app_reader(NOW).get_explanation("00000000-0000-4000-8000-000000000000")
    assert (caught.value.status, caught.value.title) == (404, "Assessment not found")
    assert caught.value.detail == "No assessment with id 00000000-0000-4000-8000-000000000000."


@pytest.mark.parametrize("k", [13, 7, 6, 1, 0])
def test_explanations_are_exact_for_first_middle_and_last_assessments(k: int):
    explanation = dump(build_demo_app_reader(NOW).get_explanation(_id("assessment", _day(k))))
    t = 13 - k
    assert explanation["assessment"]["as_of"] == _iso(_day(k))
    assert explanation["assessment"]["risk_score"] == RISKS[t]
    assert explanation["baseline"] == {"baseline_id": BASELINE_ID, "version": 1}
    assert explanation["unresolved"] == []
    assert [e["feature_id"] for e in explanation["baseline_entries"]] == ["stretch.full_spine.peak_elongation_mm"]
    session_k = k if k % 2 == 1 else k + 1
    assert [f["session_id"] for f in explanation["feature_sets"]] == [_id("session", _day(session_k, 10, 30))]
    assert [r["report_id"] for r in explanation["self_reports"]] == [_id("self_report", _day(k, 11, 30))]
    assert explanation["self_reports"][0]["pain_nrs"] == DAILY_PAIN[t]
    assert explanation["schema_version"] == "1.0.0"


def test_demo_reader_is_a_pure_function_of_the_hour():
    assert dump(build_demo_app_reader(NOW).get_status(AST)) == dump(build_demo_app_reader(NOW.replace(minute=59, second=59)).get_status(AST))


def test_assess_base_exact_timestamps():
    base = dump(build_demo_assess_base(NOW).get_base(AST))
    assert (base["as_of"], base["mission_day"]) == ("2026-11-13T12:00:00Z", 87)
    assert base["profile"]["updated_at"] == "2026-10-24T12:00:00Z"
    assert base["profile"]["anthropometry"]["body_mass_measured_at"] == "2026-10-24T12:00:00Z"
    assert base["baseline"]["created_at"] == "2026-10-29T12:00:00Z"
    assert [f["computed_at"] for f in base["feature_sets"]] == ["2026-11-08T10:39:00Z", "2026-11-10T10:39:00Z", "2026-11-12T10:39:00Z"]
    assert [r["timestamp"] for r in base["self_reports"]] == [_iso(_day(k, 11, 30)) for k in range(6, -1, -1)]
    assert [r["pain_nrs"] for r in base["self_reports"]] == DAILY_PAIN[-7:]
    context = base["daily_context"][0]
    assert (context["date"], context["mission_day"], context["medication"][0]["taken_at"]) == ("2026-11-13", 87, "2026-11-13T00:00:00Z")
    shifted = dump(build_demo_assess_base(OTHER_NOW).get_base(AST))
    assert shifted["as_of"] == "2026-11-06T09:00:00Z"
    assert shifted["daily_context"][0]["date"] == "2026-11-06"
    assert shifted["daily_context"][0]["medication"][0]["taken_at"] == "2026-11-06T00:00:00Z"
