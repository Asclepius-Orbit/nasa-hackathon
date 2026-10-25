"""Implementaciones nulas: no inventan métricas ni umbrales (dependen del modelo de Aguado)."""

from collections.abc import Sequence

from spine_contracts import Baseline, Level, SensorReading

from spine_engine.interfaces import Deviation, RiskResult, TriageDecision
from spine_engine.pipeline import AssessmentPipeline


class NullBaselineStrategy:
    """Delta del último valor de cada métrica respecto a la media de su baseline."""

    def deviations(self, baseline: Baseline, readings: Sequence[SensorReading]) -> list[Deviation]:
        latest: dict[str, SensorReading] = {}
        for reading in sorted(readings, key=lambda r: r.timestamp):
            latest[reading.metric] = reading
        return [
            Deviation(metric=metric, value=reading.value, score=reading.value - baseline.metrics[metric].mean)
            for metric, reading in latest.items()
            if metric in baseline.metrics
        ]


class NullRiskModel:
    def score(self, deviations: Sequence[Deviation]) -> RiskResult:
        return RiskResult(score=0.0)


class NullTriagePolicy:
    def decide(self, risk: RiskResult, deviations: Sequence[Deviation]) -> TriageDecision:
        return TriageDecision(level=Level.GREEN)


def stub_pipeline() -> AssessmentPipeline:
    return AssessmentPipeline(NullBaselineStrategy(), NullRiskModel(), NullTriagePolicy())
