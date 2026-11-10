"""Strip view (`app/ui-strip`): mission day, harness, ground link and reports due."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from spine_contracts import SCHEMA_VERSION

from monitor.views.due import reports_due
from monitor.views.lumbar import instant
from monitor.views.mission import mission_for

WEAK_STATES = ("stalled", "disabled")


def _latest_device(astronaut_id: str, statuses: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Status of the astronaut's most recently reported device (ties: the greater device_id); None without any."""
    own = [s for s in statuses if s["astronaut_id"] == astronaut_id]
    return max(own, key=lambda s: (datetime.fromisoformat(s["timestamp"]), s["device_id"]), default=None)


def _harness(status: Mapping[str, Any] | None, now: datetime, stale_device_hours: float) -> dict[str, Any] | None:
    if status is None or status["status"] == "offline":
        return None
    weak = [{"stream": s["stream_id"], "state": s["state"]} for s in status.get("streams", []) if s["state"] in WEAK_STATES]
    return {
        "device_id": status["device_id"],
        "battery_pct": status.get("battery_pct"),
        "weak_streams": weak,
        "degraded": status["status"] == "degraded",
        "stale": now - datetime.fromisoformat(status["timestamp"]) > timedelta(hours=stale_device_hours),
        "last_seen_at": status["timestamp"],
    }


def minutes_since(moment: str | None, now: datetime) -> int | None:
    """Whole minutes from `moment` to `now`, rounded down; None without a moment. A receipt ahead of our clock counts as 0, not as negative."""
    if moment is None:
        return None
    return max(0, (now - instant(moment)) // timedelta(minutes=1))


def build_strip(
    astronaut_id: str,
    profile: Mapping[str, Any] | None,
    device_statuses: Sequence[Mapping[str, Any]],
    last_daily: Mapping[str, Any] | None,
    last_odi: Mapping[str, Any] | None,
    now: datetime,
    odi_interval_days: int,
    stale_device_hours: float,
    ground_link: Mapping[str, Any] | None,
    last_contact_min_ago: int | None,
) -> dict[str, Any]:
    """The strip of `astronaut_id` at `now`.

    The harness is the device of the astronaut with the most recent status timestamp (several devices:
    the latest wins, ties go to the greater device_id); null when there is none or it is offline. A device whose
    last status is older than `stale_device_hours` is still listed, with `stale` true and `last_seen_at` the
    timestamp of that status. The ground link (null when not configured) and the minutes since the last contact (null
    when there was none yet) come already computed, as they depend on the settings and on the outbox.
    """
    daily_due, odi_due = reports_due(last_daily, last_odi, now, odi_interval_days)
    return {
        "schema_version": SCHEMA_VERSION,
        "astronaut_id": astronaut_id,
        "mission": mission_for(profile, now.date()),
        "harness": _harness(_latest_device(astronaut_id, device_statuses), now, stale_device_hours),
        "ground_link": ground_link,
        "last_contact_min_ago": last_contact_min_ago,
        "reports_due": {"daily": daily_due, "weekly_odi": odi_due},
    }
