"""Familia ``exercise`` (#41): número de repeticiones y regularidad de su duración.

Features: ``exercise.rep_count``, ``exercise.rep_duration_s_mean`` y ``exercise.rep_duration_cv_pct``
(coeficiente de variación con desviación muestral, n-1). Hacen falta al menos ``MIN_REPETITIONS`` (2)
repeticiones válidas; con menos, las tres salen ``skipped`` con ``too_few_repetitions``. Nunca lanza.

``streams`` (calidad común del extractor compuesto): las ventanas por IMU salen de los streams IMU
declarados, así que la calidad se evalúa sobre ellos. Las ventanas por eventos no son un stream y
``()`` daría cobertura 0 (``no_data``); se usan todos los streams declarados de la sesión, que es el
registro de datos sobre el que se hizo el ejercicio.
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
    """Features del ejercicio a partir de las ventanas de repetición del servicio inyectado."""

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
        # duración > 0 hace imposible una media 0; se protege igualmente
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
