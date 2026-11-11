"""The /assess request the app builds from its own data.

One builder for the real assessment (`Analysis`) and for the read-only API that shows the request without sending it
(`/crew/{astronaut_id}/assess-base`), so both always read the same data the same way. A request that cannot be built is
never made up: the reason comes back instead (`no_profile`, `no_baseline`, `no_feature_sets`).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from spine_contracts import SCHEMA_VERSION

from monitor.db import Store


@dataclass(frozen=True)
class BuiltRequest:
    request: dict[str, Any] | None = None
    skipped: str | None = None


def build_assess_request(store: Store, astronaut_id: str, as_of: datetime, window: timedelta) -> BuiltRequest:
    """Profile, latest baseline, and feature sets, self-reports and daily context of `[as_of - window, as_of]`."""
    profile = store.profile(astronaut_id)
    if profile is None:
        return BuiltRequest(skipped="no_profile")
    baseline = store.latest_baseline(astronaut_id)
    if baseline is None:
        return BuiltRequest(skipped="no_baseline")
    start = as_of - window
    feature_sets = store.feature_sets_between(astronaut_id, start, as_of)
    if not feature_sets:
        return BuiltRequest(skipped="no_feature_sets")
    contexts = store.daily_contexts_between(astronaut_id, start.date().isoformat(), as_of.date().isoformat())
    request: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "astronaut_id": astronaut_id,
        "as_of": as_of.isoformat(),
        "profile": profile,
        "baseline": baseline,
        "feature_sets": feature_sets,
        "self_reports": store.self_reports_between(astronaut_id, start, as_of),
        "daily_context": contexts,
    }
    today = next((c for c in contexts if c["date"] == as_of.date().isoformat()), None)
    if today is not None and "mission_day" in today:
        request["mission_day"] = today["mission_day"]
    return BuiltRequest(request=request)
