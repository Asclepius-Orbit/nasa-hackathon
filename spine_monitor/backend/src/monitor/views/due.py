"""Which self-reports are due; same semantics as `GET /ui/crew/{id}/self-reports/due`."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any


def reports_due(
    last_daily: Mapping[str, Any] | None,
    last_weekly_odi: Mapping[str, Any] | None,
    now: datetime,
    odi_interval_days: int,
) -> tuple[bool, bool]:
    """`(daily_due, weekly_odi_due)` from the latest daily and weekly-ODI self-reports (or None).

    Daily is due unless the last one is from today (its UTC date equals `now.date()`). The ODI is due if
    there is none or the last one is as old as the interval or older.
    """
    daily_today = last_daily is not None and datetime.fromisoformat(last_daily["timestamp"]).astimezone(UTC).date() == now.date()
    odi_due = last_weekly_odi is None or datetime.fromisoformat(last_weekly_odi["timestamp"]) <= now - timedelta(days=odi_interval_days)
    return not daily_today, odi_due
