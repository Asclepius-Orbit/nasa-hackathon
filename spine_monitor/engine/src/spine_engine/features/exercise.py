"""``exercise`` family (#41): number of repetitions and regularity of their duration.

Features: ``exercise.rep_count``, ``exercise.rep_duration_s_mean`` and ``exercise.rep_duration_cv_pct``
(coefficient of variation with sample deviation, n-1). At least ``MIN_REPETITIONS`` (2) valid
repetitions are needed; with fewer, all three come out ``skipped`` with ``too_few_repetitions``. Never raises.

``streams`` (common quality of the composite extractor): the IMU-based windows come from the declared IMU
streams, so quality is evaluated over them. Event-based windows are not a stream and
``()`` would give coverage 0 (``no_data``); all the declared streams of the session are used, which is the
data record on which the exercise was done.
"""

import statistics
from collections.abc import Sequence

from spine_contracts import StreamKind
from spine_contracts.models.domain.feature_set_schema import Reason

from spine_engine.features.context import ExtractionContext
from spine_engine.features.registry import ComputedFeature, FamilyResult, SkippedFeature
from spine_engine.features.repetitions import RepetitionService

MIN_REPETITIONS = 2

REP_COUNT = "exercise.rep_count"
REP_DURATION_MEAN = "exercise.rep_duration_s_mean"
REP_DURATION_CV = "exercise.rep_duration_cv_pct"


class ExerciseFamily:
    """Exercise features from the repetition windows of the injected service."""

    family = "exercise"
    feature_ids: Sequence[str] = (REP_COUNT, REP_DURATION_MEAN, REP_DURATION_CV)

    def __init__(self, repetitions: RepetitionService) -> None:
        self._repetitions = repetitions

    def extract(self, context: ExtractionContext) -> FamilyResult:
        found = self._repetitions.windows(context)
        durations = [w.duration_s for w in found.windows]
        if len(durations) < MIN_REPETITIONS:
            return FamilyResult(skipped=tuple(SkippedFeature(feature_id, Reason.TOO_FEW_REPETITIONS) for feature_id in self.feature_ids))
        mean = statistics.fmean(durations)
        # duration > 0 makes a 0 mean impossible; it is guarded anyway
        cv = 100.0 * statistics.stdev(durations) / mean if mean > 0 else 0.0
        streams = self._streams(context, found.source)
        n = len(durations)
        values = {REP_COUNT: float(n), REP_DURATION_MEAN: mean, REP_DURATION_CV: cv}
        return FamilyResult(computed=tuple(ComputedFeature(feature_id, values[feature_id], streams, n=n) for feature_id in self.feature_ids))

    @staticmethod
    def _streams(context: ExtractionContext, source: str) -> tuple[str, ...]:
        declared = context.session.streams
        if source == "imu":
            return tuple(dict.fromkeys(s.stream_id for s in declared if s.stream_kind is StreamKind.IMU))
        return tuple(dict.fromkeys(s.stream_id for s in declared))
