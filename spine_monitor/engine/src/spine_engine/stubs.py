"""Implementaciones nulas: no inventan métricas ni umbrales (dependen del modelo de Aguado)."""

from collections.abc import Sequence

from spine_contracts import Baseline, FeatureSet, FeatureValue, Level

from spine_engine.interfaces import Deviation, RiskResult, TriageDecision
from spine_engine.pipeline import AssessmentPipeline
from spine_engine.reference import index_entries, reference_entry


class NullBaselineStrategy:
    """Delta del último valor de cada feature respecto a la media de su entrada de baseline.

    La entrada es la del mismo protocolo y entorno de gravedad; si no existe, la de la Tierra
    (así se mide el efecto del entorno). Las features sin entrada no generan desviación.
    """

    def deviations(self, baseline: Baseline, feature_sets: Sequence[FeatureSet]) -> list[Deviation]:
        entries = index_entries(baseline.entries)
        latest: dict[str, tuple[FeatureValue, FeatureSet]] = {}
        for feature_set in sorted(feature_sets, key=lambda s: s.computed_at):
            for feature in feature_set.features:
                latest[feature.feature_id] = (feature, feature_set)

        result = []
        for feature_id, (feature, feature_set) in latest.items():
            entry = reference_entry(entries, feature_id, feature_set.protocol_id, feature_set.gravity_environment)
            if entry is not None:
                result.append(Deviation(feature_id=feature_id, value=feature.value, score=feature.value - entry.mean))
        return result


class NullRiskModel:
    def score(self, deviations: Sequence[Deviation]) -> RiskResult:
        return RiskResult(score=0.0)


class NullTriagePolicy:
    def decide(self, risk: RiskResult, deviations: Sequence[Deviation]) -> TriageDecision:
        return TriageDecision(level=Level.GREEN)


def stub_pipeline() -> AssessmentPipeline:
    return AssessmentPipeline(NullBaselineStrategy(), NullRiskModel(), NullTriagePolicy())
