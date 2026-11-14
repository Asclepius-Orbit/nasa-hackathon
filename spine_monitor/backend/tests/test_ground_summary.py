"""build_ground_summary: what goes to the ground, conforming to app/ground-summary; codes and numbers only."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from monitor.db import SessionRow
from monitor.views.ground_summary import build_ground_summary
from spine_contracts import validation

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
DAY = date(2027, 2, 9)
NOW = datetime(2027, 2, 9, 9, 0, tzinfo=UTC)
SESSION_ID = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a03"


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


AMBER = example("domain/assessment.amber")
DAILY = example("domain/self-report.daily")
RED_FLAG = example("domain/self-report.red-flag")


def report(base: dict[str, Any], timestamp: str, flags: list[str], **extra: Any) -> dict[str, Any]:
    return {**deepcopy(base), "timestamp": timestamp, "red_flags": flags, **extra}


def session(started_at: str, state: str = "processed", session_id: str = SESSION_ID) -> SessionRow:
    return SessionRow({"session_id": session_id, "protocol_id": "lat_pulldown_standing_v1", "started_at": started_at}, state, None)


def summary(assessment: Any = AMBER, reports: list[dict[str, Any]] | None = None, sessions: list[SessionRow] | None = None, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"mission_day": 87, "revision": 2, "now": NOW} | kw
    result = build_ground_summary("AST-01", DAY, args["mission_day"], args["revision"], args["now"], assessment, reports or [], sessions or [])
    assert validation.errors("app/ground-summary", result) == []
    return result


def test_an_assessment_gives_a_summary_that_conforms_to_the_schema():
    result = summary(sessions=[session("2027-02-09T08:00:00Z")])

    assert result["level"] == AMBER["level"]
    assert result["assessment_id"] == AMBER["assessment_id"]
    assert result["confidence"] == AMBER["confidence"]
    assert (result["astronaut_id"], result["date"], result["mission_day"], result["revision"]) == ("AST-01", "2027-02-09", 87, 2)
    assert result["as_of"] == "2027-02-09T09:00:00Z"
    assert result["data_available"] == sum(1 for d in AMBER["domains"] if d["evaluated"])
    assert result["data_total"] == len(AMBER["domains"])
    assert result["drivers"] == [{"feature_id": d["feature_id"], "deviation_z": d["deviation_z"]} for d in AMBER["drivers"]]
    assert result["recommendations"] == AMBER["recommendations"]
    assert result["sessions"] == [{"session_id": SESSION_ID, "state": "processed", "protocol_id": "lat_pulldown_standing_v1", "pain_stop": None}]
    assert result["contacts"] == []


def test_a_day_without_assessment_is_nodata_with_everything_else_null_or_empty():
    result = summary(None, mission_day=None, revision=1)

    assert result["level"] == "nodata"
    assert result["mission_day"] is None
    for field in ("assessment_id", "confidence", "data_available", "data_total", "escalation"):
        assert result[field] is None
    assert result["drivers"] == result["recommendations"] == []


def test_nodata_still_carries_the_red_flags_and_sessions_of_the_day():
    result = summary(None, [report(DAILY, "2027-02-09T07:00:00Z", ["leg_weakness"])], [session("2027-02-09T08:00:00Z", "ended")])

    assert result["red_flags"] == ["leg_weakness"]
    assert [s["state"] for s in result["sessions"]] == ["ended"]


def test_red_flags_are_the_union_without_duplicates_of_the_reports_of_that_day_only():
    reports = [
        report(DAILY, "2027-02-09T07:00:00Z", ["leg_weakness", "saddle_numbness"]),
        report(RED_FLAG, "2027-02-09T12:00:00Z", ["saddle_numbness", "trauma_event"]),
        report(DAILY, "2027-02-08T23:59:59Z", ["fever_with_back_pain"]),
        report(DAILY, "2027-02-10T00:00:00Z", ["severe_night_pain"]),
    ]

    assert summary(reports=reports)["red_flags"] == ["leg_weakness", "saddle_numbness", "trauma_event"]


def test_only_the_sessions_of_the_day_are_listed_oldest_first():
    sessions = [
        session("2027-02-09T10:00:00Z", "running", "aaaaaaaa-0000-4000-8000-000000000002"),
        session("2027-02-08T23:59:59Z", "processed", "aaaaaaaa-0000-4000-8000-000000000003"),
        session("2027-02-09T08:00:00Z", "rejected", "aaaaaaaa-0000-4000-8000-000000000001"),
    ]

    result = summary(sessions=sessions)

    assert [(s["session_id"][-1], s["state"]) for s in result["sessions"]] == [("1", "rejected"), ("2", "running")]


def test_a_required_escalation_carries_urgency_and_the_rule_ids_without_repeats():
    assessment = deepcopy(AMBER)
    assessment["escalation"] = {"required": True, "urgency": "priority", "reasons": ["free text that must not travel"]}
    assessment["fired_rules"] = [
        {"rule_id": "RULE_B", "level": "red", "overrides_model": True, "description": "prose"},
        {"rule_id": "RULE_A", "level": "amber", "overrides_model": False},
        {"rule_id": "RULE_B", "level": "red", "overrides_model": False},
    ]

    result = summary(assessment)

    assert result["escalation"] == {"required": True, "urgency": "priority", "reasons": ["RULE_B", "RULE_A"]}


def test_an_escalation_not_required_has_no_urgency_and_no_reasons_even_if_rules_fired():
    assessment = deepcopy(AMBER)
    assessment["escalation"] = {"required": False, "urgency": "routine", "reasons": []}

    assert AMBER["fired_rules"]
    assert summary(assessment)["escalation"] == {"required": False, "urgency": None, "reasons": []}


def test_free_text_of_the_reports_never_reaches_the_summary():
    marker = "ZZ-PRIVATE-NOTE-ZZ"
    reports = [
        report(RED_FLAG, "2027-02-09T07:00:00Z", ["leg_weakness"], note=marker),
        report(DAILY, "2027-02-09T08:00:00Z", ["leg_weakness"], note=marker),
    ]
    assessment = deepcopy(AMBER)
    assessment["fired_rules"][0]["description"] = marker

    assert marker not in json.dumps(summary(assessment, reports))


def test_contacts_of_the_day_go_without_their_note():
    contact = example("domain/medical-contact")
    today = {**contact, "at": "2027-02-09T08:40:00Z"}
    result = build_ground_summary("AST-01", DAY, 87, 1, NOW, None, [], [], [contact, today])

    assert result["contacts"] == [{"contact_id": today["contact_id"], "at": today["at"], "contact": "crew_medical_officer"}]
