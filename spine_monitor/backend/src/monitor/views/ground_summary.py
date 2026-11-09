"""Ground summary (`app/ground-summary`): one revision of the summary of one day, as it travels to the ground station.

Pure: no I/O, no clock, and it never receives the astronaut profile (the mission day arrives already computed). Codes and
numbers only: the free text of the reports (notes) and of the rules (descriptions) is never copied. PROVISIONAL content,
pending validation by Aguado. Without an assessment the level is 'nodata' and the assessment fields are null or empty.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from spine_contracts import SCHEMA_VERSION

from monitor.db import SessionRow
from monitor.views.history import day_red_flags
from monitor.views.lumbar import instant
from monitor.views.today import _trust


def _escalation(assessment: Mapping[str, Any]) -> dict[str, Any]:
    escalation = assessment["escalation"]
    if not escalation["required"]:
        return {"required": False, "urgency": None, "reasons": []}
    reasons = list(dict.fromkeys(rule["rule_id"] for rule in assessment["fired_rules"]))
    return {"required": True, "urgency": escalation["urgency"], "reasons": reasons}


def build_ground_summary(
    astronaut_id: str,
    day: date,
    mission_day: int | None,
    revision: int,
    now: datetime,
    assessment: Mapping[str, Any] | None,
    self_reports: Sequence[Mapping[str, Any]],
    sessions: Sequence[SessionRow],
) -> dict[str, Any]:
    """GroundSummary of `day` (UTC) for `revision`, made at `now`.

    `assessment` is the latest of the day (the caller picks it). Reports and sessions of other days are ignored, so
    the caller may pass a wider window. The escalation reasons are the `rule_id` of the fired rules, only when required.
    """
    todays_reports = [r for r in self_reports if instant(r["timestamp"]).date() == day]
    todays_sessions = sorted((s for s in sessions if instant(s.payload["started_at"]).date() == day), key=lambda s: instant(s.payload["started_at"]))
    view: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "astronaut_id": astronaut_id,
        "date": day.isoformat(),
        "mission_day": mission_day,
        "revision": revision,
        "as_of": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "level": "nodata",
        "assessment_id": None,
        "confidence": None,
        "data_available": None,
        "data_total": None,
        "drivers": [],
        "red_flags": day_red_flags(todays_reports),
        "sessions": [{"session_id": s.payload["session_id"], "state": s.state, "protocol_id": s.payload["protocol_id"]} for s in todays_sessions],
        "recommendations": [],
        "escalation": None,
        "contacts": None,
    }
    if assessment is not None:
        trust = _trust(assessment)
        view |= {
            "level": assessment["level"],
            "assessment_id": assessment["assessment_id"],
            "confidence": trust["confidence"],
            "data_available": trust["data_available"],
            "data_total": trust["data_total"],
            "drivers": [{"feature_id": d["feature_id"], "deviation_z": d.get("deviation_z")} for d in assessment["drivers"]],
            "recommendations": list(assessment["recommendations"]),
            "escalation": _escalation(assessment),
        }
    return view
