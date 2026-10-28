"""Estrategia de baseline personal: z de la última sesión + cambio sostenido sobre el histórico."""

from collections.abc import Sequence

from spine_contracts import Baseline, FeatureSet

from spine_engine.interfaces import ChangeDetector, Deviation, DeviationScorer
from spine_engine.reference import index_entries, reference_entry


class StatisticalBaselineStrategy:
    """Implementa `BaselineStrategy`. Puntúa y detecta con las estrategias que se le inyectan.

    La puntuación es la del último valor de cada feature. El histórico de feature sets (mismo
    protocolo y entorno de gravedad) alimenta el detector, que dice si el cambio es sostenido o ruido.
    """

    def __init__(self, scorer: DeviationScorer, detector: ChangeDetector) -> None:
        self._scorer = scorer
        self._detector = detector

    def deviations(self, baseline: Baseline, feature_sets: Sequence[FeatureSet]) -> list[Deviation]:
        entries = index_entries(baseline.entries)
        ordered = sorted(feature_sets, key=lambda s: s.computed_at)

        latest = {}
        for feature_set in ordered:
            for feature in feature_set.features:
                latest[feature.feature_id] = (feature, feature_set)

        result = []
        for feature_id, (feature, feature_set) in latest.items():
            entry = reference_entry(entries, feature_id, feature_set.protocol_id, feature_set.gravity_environment)
            if entry is None:
                continue
            series = [
                self._scorer.score(f.value, entry)
                for other in ordered
                if other.protocol_id == feature_set.protocol_id and other.gravity_environment == feature_set.gravity_environment
                for f in other.features
                if f.feature_id == feature_id
            ]
            z = self._scorer.score(feature.value, entry)
            signal = self._detector.detect(series)
            result.append(
                Deviation(
                    feature_id=feature_id,
                    value=feature.value,
                    score=z,
                    z=z,
                    protocol_id=feature_set.protocol_id,
                    reference_gravity=entry.gravity_environment,
                    sustained=signal.sustained,
                    direction=signal.direction,
                    n_points=len(series),
                )
            )
        return result
