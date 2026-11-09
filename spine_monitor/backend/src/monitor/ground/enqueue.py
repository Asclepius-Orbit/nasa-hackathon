"""Queues the ground summary of the day of an assessment. A day with no assessment queues nothing: its summary is the view's job.

Only the mission day leaves the profile, already computed: the profile itself never reaches the summary builder.

A summary that does not conform, or that carries a NaN or an infinity (JSON Schema lets them through), is never written: as the
oldest entry of its astronaut it would hold back everything after it, retried forever, and it would break the Houston view.
"""

import logging
import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from spine_contracts import validation

from monitor.db import Store, day_span
from monitor.views.ground_summary import build_ground_summary
from monitor.views.lumbar import instant
from monitor.views.mission import launch_date_of, mission_day_of

_log = logging.getLogger(__name__)


class _RejectedSummary(Exception):
    """Carries the JSON pointer of the problem, never the value: it may be health data. Raised inside the write transaction, it undoes it."""

    def __init__(self, pointer: str) -> None:
        super().__init__(pointer)
        self.pointer = pointer


def _non_finite_pointer(value: Any, pointer: str = "") -> str | None:
    """JSON pointer of the first NaN or infinity inside `value`; None when there is none."""
    if isinstance(value, float):
        return None if math.isfinite(value) else pointer
    children = value.items() if isinstance(value, Mapping) else enumerate(value) if isinstance(value, list) else ()
    for key, child in children:
        if (found := _non_finite_pointer(child, f"{pointer}/{key}")) is not None:
            return found
    return None


def _checked(summary: dict[str, Any]) -> dict[str, Any]:
    found = validation.errors("app/ground-summary", summary)
    if found:
        # Only the pointer: the message of a violation quotes the offending value.
        raise _RejectedSummary(found[0].split(": ", 1)[0])
    non_finite = _non_finite_pointer(summary)
    if non_finite is not None:
        raise _RejectedSummary(non_finite)
    return summary


def enqueue_ground_summary(store: Store, assessment: Mapping[str, Any], now: datetime) -> int | None:
    """Queues the summary of the UTC day of `assessment["as_of"]`, already stored, and returns its revision.

    The summary carries the latest assessment of that day, which is not `assessment` if this one is about an earlier moment.
    A rejected summary writes nothing, whatever was queued stays as it was, and the result is None.
    """
    astronaut_id = assessment["astronaut_id"]
    day = instant(assessment["as_of"]).date()
    start, end = day_span(day, day)
    launch = launch_date_of(store.profile(astronaut_id))
    mission_day = None if launch is None else mission_day_of(launch, day)
    latest = store.assessments_between(astronaut_id, start, end)[-1]
    reports = store.self_reports_between(astronaut_id, start, end)
    sessions = store.session_rows(astronaut_id, start=start, end=end)
    try:
        return store.upsert_ground_summary(
            astronaut_id,
            day,
            mission_day,
            lambda revision: _checked(build_ground_summary(astronaut_id, day, mission_day, revision, now, latest, reports, sessions)),
            now,
        )
    except _RejectedSummary as rejected:
        _log.error("%s: ground summary of %s not queued: rejected at %s", astronaut_id, day, rejected.pointer)
        return None
