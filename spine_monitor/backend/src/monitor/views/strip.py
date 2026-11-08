"""Strip view (`app/ui-strip`): mission day, harness, ground link and reports due."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from spine_contracts import SCHEMA_VERSION

from monitor.views.due import reports_due
from monitor.views.mission import mission_for

WEAK_STATES = ("stalled", "disabled")


def _latest_device(astronaut_id: str, statuses: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Status of the astronaut's most recently reported device (ties: the greater device_id); None without any."""
    own = [s for s in statuses if s["astronaut_id"] == astronaut_id]
    return max(own, key=lambda s: (datetime.fromisoformat(s["timestamp"]), s["device_id"]), default=None)


def _harness(status: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if status is None or status["status"] == "offline":
        return None
    weak = [{"stream": s["stream_id"], "state": s["state"]} for s in status.get("streams", []) if s["state"] in WEAK_STATES]
    return {
        "device_id": status["device_id"],
        "battery_pct": status.get("battery_pct"),
        "weak_streams": weak,
        "degraded": status["status"] == "degraded",
    }


def build_strip(
    astronaut_id: str,
    profile: Mapping[str, Any] | None,
    device_statuses: Sequence[Mapping[str, Any]],
    last_daily: Mapping[str, Any] | None,
    last_odi: Mapping[str, Any] | None,
    now: datetime,
    odi_interval_days: int,
) -> dict[str, Any]:
    """The strip of `astronaut_id` at `now`.

    The harness is the device of the astronaut with the most recent status timestamp (several devices:
    the latest wins, ties go to the greater device_id); null when there is none or it is offline. The
    ground link and the last contact are not modelled yet (issue #62) and are always null.
    """
    daily_due, odi_due = reports_due(last_daily, last_odi, now, odi_interval_days)
    return {
        "schema_version": SCHEMA_VERSION,
        "astronaut_id": astronaut_id,
        "mission": mission_for(profile, now.date()),
        "harness": _harness(_latest_device(astronaut_id, device_statuses)),
        "ground_link": None,
        "last_contact_min_ago": None,
        "reports_due": {"daily": daily_due, "weekly_odi": odi_due},
    }
