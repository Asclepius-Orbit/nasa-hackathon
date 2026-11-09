"""Mission day and phase from the launch and landing dates of the astronaut profile."""

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal

type Phase = Literal["preflight", "inflight", "postflight"]


def mission_day_of(launch_date: date, d: date) -> int:
    """Mission day of `d`: launch day is 1, the day before is -1 (L-30 is -30); there is no day 0."""
    elapsed = (d - launch_date).days
    return elapsed if elapsed < 0 else elapsed + 1


def _date(profile: Mapping[str, Any], key: str) -> date | None:
    value = profile.get(key)
    return None if value is None else date.fromisoformat(value)


def launch_date_of(profile: Mapping[str, Any] | None) -> date | None:
    """None without a profile or without a launch date."""
    return None if profile is None else _date(profile, "launch_date")


def mission_for(profile: Mapping[str, Any] | None, today: date) -> dict[str, Any]:
    """`{day, start, phase}` of `today`; all null without a profile or without a launch date."""
    launch = launch_date_of(profile)
    if profile is None or launch is None:
        return {"day": None, "start": None, "phase": None}
    landing = _date(profile, "landing_date")
    phase: Phase = "preflight" if today < launch else "postflight" if landing is not None and today >= landing else "inflight"
    return {"day": mission_day_of(launch, today), "start": launch.isoformat(), "phase": phase}
