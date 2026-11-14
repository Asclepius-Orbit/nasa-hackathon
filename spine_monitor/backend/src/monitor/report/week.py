"""Week data of the Houston report: the last seven UTC days of one astronaut, as plain data.

Pure: no I/O, no clock (`now` and the mission calendar come as arguments). Only codes, numbers and instants: the free
text of the reports (notes), the descriptions of the rules and anything the LLM agent wrote are never copied, and no
profile data (name, age...) comes in, only the pseudonymous `astronaut_id`. Absence is explicit: a day without an
assessment has level `nodata`, a missing measure is `None`, and nothing is ever defaulted to green or to 0.
"""

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from statistics import fmean
from typing import Any

from spine_contracts import SCHEMA_VERSION

from monitor.db import SessionRow
from monitor.views.lumbar import instant

Payload = Mapping[str, Any]
WEEK_DAYS = 7
ODI_MAX_ITEM_SCORE = 5


def week_start(end: date) -> date:
    """First day of the week that ends on `end`: both inclusive, seven UTC days."""
    return end - timedelta(days=WEEK_DAYS - 1)


def _in_week[T](items: Sequence[T], when: Callable[[T], datetime], start: date, end: date) -> dict[date, list[T]]:
    """Items grouped by UTC day inside [start, end], each group oldest first (stable for equal instants)."""
    days: dict[date, list[T]] = defaultdict(list)
    for item in sorted(items, key=when):
        day = when(item).date()
        if start <= day <= end:
            days[day].append(item)
    return days


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _feature_values(feature_set: Payload, suffix: str) -> list[float]:
    return [f["value"] for f in feature_set["features"] if f["feature_id"].endswith(suffix) and f.get("value") is not None]


def _metrics(feature_set: Payload | None) -> dict[str, float | None]:
    """The key measures of one session feature set; each None when the set lacks it (never 0)."""
    empty: dict[str, float | None] = dict.fromkeys(("reps", "rep_duration_s", "elongation_mm", "rom_deg", "emg_pct_mvc"), None)
    if feature_set is None:
        return empty

    def first(suffix: str) -> float | None:
        found = _feature_values(feature_set, suffix)
        return found[0] if found else None

    rom, emg = _feature_values(feature_set, ".rom_deg"), _feature_values(feature_set, ".rms_pct_mvc")
    return {
        "reps": first("exercise.rep_count"),
        "rep_duration_s": first("exercise.rep_duration_s_mean"),
        "elongation_mm": first(".peak_elongation_mm"),
        "rom_deg": max(rom) if rom else None,
        "emg_pct_mvc": fmean(emg) if emg else None,
    }


def _session(row: SessionRow, feature_set: Payload | None) -> dict[str, Any]:
    payload = row.payload
    pains = [e for e in payload.get("events", []) if e["event_type"] == "pain_reported"]
    stopped = payload.get("end_reason") == "aborted_pain"
    return {
        "session_id": payload["session_id"],
        "date": instant(payload["started_at"]).date().isoformat(),
        "started_at": payload["started_at"],
        "session_type": payload.get("session_type"),
        "protocol_id": payload["protocol_id"],
        "state": row.state,
        "end_reason": payload.get("end_reason"),
        "pain_stop": stopped,
        "pain_stop_rep": pains[-1].get("rep_index") if stopped and pains else None,
        "planned_reps": payload.get("planned_reps"),
        **_metrics(feature_set),
    }


def _escalation(assessment: Payload) -> dict[str, Any]:
    escalation = assessment["escalation"]
    if not escalation["required"]:
        return {"required": False, "urgency": None, "reasons": []}
    return {"required": True, "urgency": escalation.get("urgency"), "reasons": _unique([r["rule_id"] for r in assessment["fired_rules"]])}


def _assessment(assessment: Payload) -> dict[str, Any]:
    completeness = assessment["completeness"]
    return {
        "as_of": assessment["as_of"],
        "level": assessment["level"],
        "risk_score": assessment.get("risk_score"),
        "confidence": assessment.get("confidence"),
        "completeness": {"score": completeness["score"], "missing": [{"what": m["what"], "reason": m["reason"]} for m in completeness["missing"]]},
        "domains": [{"domain": d["domain"], "level": d["level"], "score": d.get("score"), "evaluated": d["evaluated"]} for d in assessment["domains"]],
        "drivers": [
            {
                "feature_id": d["feature_id"],
                "value": d.get("value"),
                "unit": d.get("unit"),
                "deviation_z": d.get("deviation_z"),
                "contribution": d.get("contribution"),
            }
            for d in assessment["drivers"]
        ],
        "trend": assessment.get("trend"),
        "fired_rules": [
            {"rule_id": r["rule_id"], "domain": r["domain"], "level": r["level"], "overrides_model": r["overrides_model"]} for r in assessment["fired_rules"]
        ],
        "recommendations": list(assessment["recommendations"]),
        "escalation": _escalation(assessment),
    }


def _odi(reports: Sequence[Payload], mission_day_of: Callable[[date], int | None]) -> dict[str, Any] | None:
    odis = [r for r in reports if r["kind"] == "weekly_odi"]
    if not odis:
        return None
    latest = odis[-1]
    day = instant(latest["timestamp"]).date()
    items = [{"item_id": i["item_id"], "score": i["score"]} for i in latest["items"]]
    return {
        "date": day.isoformat(),
        "mission_day": mission_day_of(day),
        "items": items,
        "percent": sum(i["score"] for i in items) / (ODI_MAX_ITEM_SCORE * len(items)) * 100,
    }


def _day(day: date, mission_day: int | None, assessments: Sequence[Payload], reports: Sequence[Payload], sessions: Sequence[SessionRow]) -> dict[str, Any]:
    dailies = [r for r in reports if r["kind"] == "daily"]
    last = dailies[-1] if dailies else {}
    return {
        "date": day.isoformat(),
        "mission_day": mission_day,
        "level": assessments[-1]["level"] if assessments else "nodata",
        "pain_nrs": last.get("pain_nrs"),
        "pain_nrs_worst_24h": last.get("pain_nrs_worst_24h"),
        "stiffness_nrs": last.get("stiffness_nrs"),
        "sleep_hours": last.get("sleep", {}).get("hours"),
        "red_flags": _unique([f for r in reports for f in r.get("red_flags", [])]),
        "other_symptoms": _unique([s for r in reports for s in r.get("other_symptoms", [])]),
        "reports": len(reports),
        "sessions": sum(1 for s in sessions if s.state != "rejected"),
    }


def build_week_report(
    astronaut_id: str,
    start: date,
    end: date,
    now: datetime,
    mission_day_of: Callable[[date], int | None],
    assessments: Sequence[Payload],
    self_reports: Sequence[Payload],
    sessions: Sequence[SessionRow],
    feature_sets: Sequence[Payload],
) -> dict[str, Any]:
    """Data of the week [start, end] (UTC days, inclusive), made at `now`.

    Everything outside the week is ignored, so the caller may pass a wider window. A `rejected` session never ran and
    is left out. `latest_assessment` is the last one of the week; the ODI is the last weekly one of the week.
    """
    by_day_assessments = _in_week(assessments, lambda a: instant(a["as_of"]), start, end)
    by_day_reports = _in_week(self_reports, lambda r: instant(r["timestamp"]), start, end)
    by_day_sessions = _in_week(sessions, lambda s: instant(s.payload["started_at"]), start, end)
    sets = {fs["session_id"]: fs for fs in feature_sets if fs.get("session_id")}

    days = [
        _day(day, mission_day_of(day), by_day_assessments.get(day, []), by_day_reports.get(day, []), by_day_sessions.get(day, []))
        for day in (start + timedelta(days=n) for n in range((end - start).days + 1))
    ]
    reports = [r for day in sorted(by_day_reports) for r in by_day_reports[day]]
    week_assessments = [a for day in sorted(by_day_assessments) for a in by_day_assessments[day]]
    week_sessions = [s for day in sorted(by_day_sessions) for s in by_day_sessions[day] if s.state != "rejected"]
    pains = [d["pain_nrs"] for d in days if d["pain_nrs"] is not None]
    worsts = [d["pain_nrs_worst_24h"] for d in days if d["pain_nrs_worst_24h"] is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "astronaut_id": astronaut_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "generated_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "mission_day": {"start": mission_day_of(start), "end": mission_day_of(end)},
        "has_data": bool(week_assessments or reports or week_sessions),
        "days": days,
        "pain": {
            "daily_reports": len(pains),
            "mean": fmean(pains) if pains else None,
            "max": max(pains) if pains else None,
            "max_worst_24h": max(worsts) if worsts else None,
        },
        "symptoms": {
            "red_flags": _unique([f for r in reports for f in r.get("red_flags", [])]),
            "other_symptoms": _unique([s for r in reports for s in r.get("other_symptoms", [])]),
        },
        "odi": _odi(reports, mission_day_of),
        "sessions": [_session(s, sets.get(s.payload["session_id"])) for s in week_sessions],
        "assessments": [
            {"as_of": a["as_of"], "level": a["level"], "risk_score": a.get("risk_score"), "confidence": a.get("confidence")} for a in week_assessments
        ],
        "latest_assessment": _assessment(week_assessments[-1]) if week_assessments else None,
    }
