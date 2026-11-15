"""Symptom features (#47): 7-day aggregates of the self-reports; pure function, no wall clock.

- Window ``[as_of - 7 days, as_of]`` with both ends included (the ``timestamp`` instant is compared).
- ``pain_nrs_7d_mean``/``stiffness_nrs_7d_mean`` come from the ``daily`` reports;
    ``sleep_hours_7d_mean`` only from the ``daily`` ones that carry ``sleep.hours``. ``weekly_odi`` contribute nothing.
- ``red_flag_count_7d`` adds the flags of the ``daily`` and ``red_flag`` reports; an empty list counts 0,
    whereas an absent or null field contributes no observation.
- A feature without applicable data goes to ``skipped`` with ``no_data``: it is never filled with zeros.
- Only the reports and contexts of the requested astronaut count.
- ``gravity_environment`` is that of the most recent daily context (by ``date``) within the window, comparing
    UTC dates (``start.date() <= date <= end.date()``); without a context, ``None``.
- ``computed_at`` is ``as_of``: there is no clock in the logic.
"""

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from spine_contracts import (
    SCHEMA_VERSION,
    DailyContext,
    DailySelfReport,
    FeatureSet,
    FeatureValue,
    Quality,
    RedFlagReport,
    SelfReport,
)
from spine_contracts.catalog import unit_for
from spine_contracts.models.common_schema import AssessedBy
from spine_contracts.models.domain.feature_set_schema import Reason, SkippedItem, Window

# Rolling window of the symptom aggregates: ``[as_of - 7 days, as_of]``, both ends included.
WINDOW = timedelta(days=7)

PAIN_MEAN = "symptoms.pain_nrs_7d_mean"
STIFFNESS_MEAN = "symptoms.stiffness_nrs_7d_mean"
SLEEP_MEAN = "symptoms.sleep_hours_7d_mean"
RED_FLAG_COUNT = "symptoms.red_flag_count_7d"

# Self-report: there is no sampling to degrade, so quality is maximal and without flags (the engine sets it).
_QUALITY_SCORE = 1.0


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _feature(feature_id: str, value: float, n: int) -> FeatureValue:
    quality = Quality(score=_QUALITY_SCORE, flags=[], assessed_by=AssessedBy.ENGINE)
    return FeatureValue.model_validate({"feature_id": feature_id, "value": value, "unit": unit_for(feature_id), "n": n, "quality": quality})


def _latest_gravity(daily_context: Sequence[DailyContext], astronaut_id: str, first: datetime, last: datetime) -> DailyContext | None:
    first_day, last_day = first.astimezone(UTC).date(), last.astimezone(UTC).date()
    candidates = [c for c in daily_context if c.astronaut_id == astronaut_id and first_day <= c.date <= last_day]
    # tie-break by value so the result does not depend on the input order
    return max(candidates, key=lambda c: (c.date, c.gravity_environment.value), default=None)


def symptom_feature_set(self_reports: Sequence[SelfReport], daily_context: Sequence[DailyContext], as_of: datetime, astronaut_id: str) -> FeatureSet:
    """Rolling-window FeatureSet (``session_id=None``) with the symptom aggregates of ``astronaut_id``."""
    start = as_of - WINDOW
    reports = [r for r in self_reports if r.astronaut_id == astronaut_id and start <= r.timestamp <= as_of]
    dailies = [r for r in reports if isinstance(r, DailySelfReport)]
    flag_reports = [r for r in reports if isinstance(r, RedFlagReport)]
    sleeping = [r.sleep.hours for r in dailies if r.sleep is not None]
    flag_counts = [len(r.red_flags) for r in dailies if r.red_flags is not None] + [len(r.red_flags) for r in flag_reports]

    computed: list[FeatureValue] = []
    if dailies:
        pain = [r.pain_nrs for r in dailies]
        computed.append(_feature(PAIN_MEAN, _mean(pain), len(pain)))
        computed.append(_feature(STIFFNESS_MEAN, _mean([r.stiffness_nrs for r in dailies]), len(dailies)))
    if sleeping:
        computed.append(_feature(SLEEP_MEAN, _mean(sleeping), len(sleeping)))
    if flag_counts:
        computed.append(_feature(RED_FLAG_COUNT, float(sum(flag_counts)), len(flag_counts)))

    done = {f.feature_id for f in computed}
    skipped = [
        SkippedItem(feature_id=feature_id, reason=Reason.NO_DATA)
        for feature_id in sorted({PAIN_MEAN, STIFFNESS_MEAN, SLEEP_MEAN, RED_FLAG_COUNT} - done)
    ]
    latest = _latest_gravity(daily_context, astronaut_id, start, as_of)
    return FeatureSet(
        schema_version=SCHEMA_VERSION,
        astronaut_id=astronaut_id,
        session_id=None,
        protocol_id=None,
        gravity_environment=None if latest is None else latest.gravity_environment,
        computed_at=as_of,
        window=Window(start=start, end=as_of),
        features=sorted(computed, key=lambda f: f.feature_id),
        skipped=skipped,
    )
