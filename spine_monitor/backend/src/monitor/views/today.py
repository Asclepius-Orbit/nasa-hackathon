"""Today view (`app/ui-today`): the Today screen built from the latest assessment."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from spine_contracts import SCHEMA_VERSION

from monitor.views.lumbar import LumbarDelta, instant

ACTIVE_SESSION_STATES = ("starting", "running")


class SessionLike(Protocol):
    """A stored session: its payload (domain/session) and its app state (e.g. `db.SessionRow`)."""

    @property
    def payload(self) -> Mapping[str, Any]: ...

    @property
    def state(self) -> str: ...


def _in_progress(session_and_reps: tuple[SessionLike, int | None] | None, now: datetime, stale_session_hours: float) -> dict[str, Any] | None:
    if session_and_reps is None:
        return None
    session, total_reps = session_and_reps
    payload = session.payload
    if session.state not in ACTIVE_SESSION_STATES or now - instant(payload["started_at"]) > timedelta(hours=stale_session_hours):
        return None
    return {
        "session_id": payload["session_id"],
        "protocol_id": payload["protocol_id"],
        "started_at": payload["started_at"],
        "total_reps": total_reps,
    }


def _trust(assessment: Mapping[str, Any]) -> dict[str, Any]:
    domains = assessment["domains"]
    return {
        "confidence": assessment["confidence"],
        "data_available": sum(1 for d in domains if d["evaluated"]),
        "data_total": len(domains),
        "missing": [{"what": m["what"], "reason": m["reason"]} for m in assessment["completeness"]["missing"]],
    }


def build_today(
    assessment: Mapping[str, Any] | None,
    lumbar: LumbarDelta | None,
    session_in_progress: tuple[SessionLike, int | None] | None,
    also_today: Sequence[str],
    now: datetime,
    stale_session_hours: float,
) -> dict[str, Any]:
    """The Today screen. Without an assessment the level is 'nodata' and every assessment field is null or empty.

    `lumbar` arrives already computed (None when unknown): the delta and the instant of its measure are both
    present or both null. `session_in_progress` is the latest session with the total_reps of its protocol; it is
    shown only while its state is starting or running and it started at most `stale_session_hours` before `now`
    (an older one never ended and is not live).
    """
    view: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "level": "nodata",
        "assessment_id": None,
        "assessed_at": None,
        "recommendations": [],
        "reasons": [],
        "trust": None,
        "lumbar_delta_mm": None if lumbar is None else lumbar.delta_mm,
        "lumbar_measured_at": None if lumbar is None else lumbar.measured_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "session_in_progress": _in_progress(session_in_progress, now, stale_session_hours),
        "also_today": list(also_today),
    }
    if assessment is not None:
        view |= {
            "level": assessment["level"],
            "assessment_id": assessment["assessment_id"],
            "assessed_at": assessment["as_of"],
            "recommendations": list(assessment["recommendations"]),
            "reasons": [{"feature_id": d["feature_id"], "deviation_z": d.get("deviation_z"), "contribution": d["contribution"]} for d in assessment["drivers"]],
            "trust": _trust(assessment),
        }
    return view
