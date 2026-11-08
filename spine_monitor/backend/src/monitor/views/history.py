"""History view (`app/ui-history`): one row per UTC day of a window.

Pure: no I/O, no clock (`today` and the mission calendar come as arguments). Absence is explicit: a day
without an assessment has level `nodata`, a day without a measure has a null length, and no length is ever
invented as 0. Days are UTC days of the timestamp of each entity (`as_of`, `timestamp`, `started_at` and,
for a feature set, the start of its window or `computed_at`).
"""

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from spine_contracts import SCHEMA_VERSION

from monitor.db import SessionRow
from monitor.views.lumbar import (
    baseline_entry,
    elongation_value,
    first_lumbar_entry,
    instant,
    latest_elongation,
    lumbar_delta_mm,
    measured_at,
    preflight_range_mm,
)

Payload = Mapping[str, Any]


def _by_day(items: Sequence[Any], when: Callable[[Any], datetime], first: date, last: date) -> dict[date, list[Any]]:
    """Items grouped by UTC day inside [first, last], each group oldest first (stable for equal instants)."""
    days: dict[date, list[Any]] = defaultdict(list)
    for item in sorted(items, key=when):
        day = when(item).date()
        if first <= day <= last:
            days[day].append(item)
    return days


def _day_length_delta(feature_sets: Sequence[Payload], baseline: Payload | None) -> float | None:
    latest = latest_elongation(feature_sets)
    if latest is None:
        return None
    value, protocol_id, gravity = latest
    return lumbar_delta_mm(value, baseline_entry(baseline, protocol_id, gravity))


def _preflight_range(feature_sets: Sequence[Payload], baseline: Payload | None) -> dict[str, float] | None:
    """Range of the baseline entry of the protocol and gravity of the most recent measure (any date); the first lumbar entry without measures."""
    latest = latest_elongation(feature_sets)
    if latest is None:
        return preflight_range_mm(first_lumbar_entry(baseline))
    _, protocol_id, gravity = latest
    return preflight_range_mm(baseline_entry(baseline, protocol_id, gravity))


def _report(reports: Sequence[Payload]) -> dict[str, Any] | None:
    if not reports:
        return None
    last = reports[-1]
    return {"kind": last["kind"], "red_flags": list(dict.fromkeys(last.get("red_flags", [])))}


def _pain(reports: Sequence[Payload]) -> int | None:
    dailies = [r for r in reports if r["kind"] == "daily"]
    return dailies[-1].get("pain_nrs") if dailies else None


def _session(sessions: Sequence[SessionRow]) -> dict[str, Any] | None:
    if not sessions:
        return None
    last = sessions[-1]
    return {"session_id": last.payload["session_id"], "state": last.state, "protocol_id": last.payload["protocol_id"]}


def build_history(
    from_date: date,
    to_date: date,
    today: date,
    mission_day_of: Callable[[date], int | None],
    assessments: Sequence[Payload],
    feature_sets: Sequence[Payload],
    self_reports: Sequence[Payload],
    sessions: Sequence[SessionRow],
    baseline: Payload | None,
) -> dict[str, Any]:
    """HistoryView for [from_date, to_date], both inclusive, oldest day first. Raises ValueError if from_date > to_date.

    `preflight_range_mm` is the range of the baseline lumbar entry for the protocol and gravity of the most
    recent elongation among ALL `feature_sets` (also outside the window); with no measure at all it is the first
    lumbar entry of the baseline; null without baseline or entry.
    """
    if from_date > to_date:
        raise ValueError(f"from ({from_date}) is after to ({to_date})")
    assessments_by_day = _by_day(assessments, lambda a: instant(a["as_of"]), from_date, to_date)
    sets_by_day = _by_day([fs for fs in feature_sets if elongation_value(fs) is not None], measured_at, from_date, to_date)
    reports_by_day = _by_day(self_reports, lambda r: instant(r["timestamp"]), from_date, to_date)
    sessions_by_day = _by_day(sessions, lambda s: instant(s.payload["started_at"]), from_date, to_date)

    days: list[dict[str, Any]] = []
    for offset in range((to_date - from_date).days + 1):
        day = from_date + timedelta(days=offset)
        day_assessments = assessments_by_day.get(day, [])
        days.append(
            {
                "date": day.isoformat(),
                "mission_day": mission_day_of(day),
                "level": day_assessments[-1]["level"] if day_assessments else "nodata",
                "length_delta_mm": _day_length_delta(sets_by_day.get(day, []), baseline),
                "pain_nrs": _pain(reports_by_day.get(day, [])),
                "session": _session(sessions_by_day.get(day, [])),
                "report": _report(reports_by_day.get(day, [])),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "today_day": mission_day_of(today),
        "preflight_range_mm": _preflight_range(feature_sets, baseline),
        "days": days,
    }
