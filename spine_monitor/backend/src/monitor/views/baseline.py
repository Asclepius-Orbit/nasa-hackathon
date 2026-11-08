"""Baseline and first-day views (`app/ui-baseline`, `app/ui-first-day`).

Pure: progress of the pre-flight reference sessions against the plan (offsets in days relative to launch,
e.g. -30, -23, -16). The plan and the number of sessions required come from settings; the launch date and
the default protocol from the caller.
"""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from spine_contracts import SCHEMA_VERSION

from monitor.db import SessionRow
from monitor.views.lumbar import instant

# A reference session is complete once it ended: ended, processing and processed. `starting`/`running` are
# not finished and `rejected`/`processing_failed` produced no usable features.
COMPLETE_STATES = ("ended", "processing", "processed")


def _recorded(sessions: Sequence[SessionRow]) -> list[SessionRow]:
    """Complete pre-flight sessions, oldest first (callers may pass more than that: it is filtered here too)."""
    done = [s for s in sessions if s.state in COMPLETE_STATES and s.payload["mission_context"]["phase"] == "preflight"]
    return sorted(done, key=lambda s: instant(s.payload["started_at"]))


def build_baseline(
    baseline: Mapping[str, Any] | None,
    preflight_sessions: Sequence[SessionRow],
    launch_date: date | None,
    required: int,
    plan_offsets: Sequence[int],
    default_protocol_id: str,
) -> dict[str, Any]:
    """BaselineView. `recorded` counts every complete pre-flight session, even beyond `required`.

    `sessions` has one row per plan offset: the first `recorded` positions are `complete` (session id, protocol
    and UTC date of the session, paired by started_at order) and the rest `planned` (no session id, protocol
    `default_protocol_id`, date = launch_date + offset, null without a launch date). Sessions beyond the
    plan have no offset and are not listed. Raises ValueError if `required` < 1.
    """
    if required < 1:
        raise ValueError(f"required must be at least 1, got {required}")
    recorded = _recorded(preflight_sessions)
    rows: list[dict[str, Any]] = []
    for position, offset in enumerate(plan_offsets):
        if position < len(recorded):
            payload = recorded[position].payload
            rows.append(
                {
                    "session_id": payload["session_id"],
                    "protocol_id": payload["protocol_id"],
                    "status": "complete",
                    "offset_days": offset,
                    "date": instant(payload["started_at"]).date().isoformat(),
                }
            )
        else:
            planned = None if launch_date is None else (launch_date + timedelta(days=offset)).isoformat()
            rows.append({"session_id": None, "protocol_id": default_protocol_id, "status": "planned", "offset_days": offset, "date": planned})
    return {
        "schema_version": SCHEMA_VERSION,
        "recorded": len(recorded),
        "required": required,
        "reference_version": None if baseline is None else baseline["version"],
        "built_from_sessions": 0 if baseline is None else len(baseline["built_from_session_ids"]),
        "sessions": rows,
    }


def build_first_day(todo: Sequence[str], baseline_view: Mapping[str, Any]) -> dict[str, Any]:
    """FirstDayView: the tasks left (order kept, repeats dropped) and the baseline view as it is."""
    return {"schema_version": SCHEMA_VERSION, "todo": list(dict.fromkeys(todo)), "baseline": dict(baseline_view)}
