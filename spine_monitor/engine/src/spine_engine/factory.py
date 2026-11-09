"""Montaje por defecto del motor a partir de la configuración."""

from spine_engine.baseline.builder import StatisticalBaselineBuilder
from spine_engine.deviation.detectors import AllAgreeVote, CompositeDetector, CusumDetector, KalmanDetector
from spine_engine.deviation.scoring import RobustZScorer
from spine_engine.deviation.strategy import StatisticalBaselineStrategy
from spine_engine.interfaces import BaselineBuilder
from spine_engine.pipeline import AssessmentPipeline
from spine_engine.risk import DeviationRiskModel
from spine_engine.settings import EngineSettings
from spine_engine.stubs import NullTriagePolicy


def default_baseline_strategy(settings: EngineSettings) -> StatisticalBaselineStrategy:
    detector = CompositeDetector(
        [
            CusumDetector(settings.cusum_k, settings.cusum_h, settings.cusum_clip, settings.cusum_min_points),
            KalmanDetector(
                settings.kalman_process_var,
                settings.kalman_measurement_var,
                settings.kalman_threshold,
                settings.kalman_persist,
                settings.cusum_clip,
                settings.kalman_min_points,
            ),
        ],
        AllAgreeVote(),
    )
    return StatisticalBaselineStrategy(RobustZScorer(settings.scale_floor), detector)


def default_pipeline(settings: EngineSettings) -> AssessmentPipeline:
    """Baseline estadística y modelo de riesgo genérico reales; el triaje sigue siendo nulo."""
    risk = DeviationRiskModel(settings.risk_domain_weights, settings.risk_persistence_points, settings.risk_saturation)
    return AssessmentPipeline(default_baseline_strategy(settings), risk, NullTriagePolicy())


def default_baseline_builder() -> BaselineBuilder:
    return StatisticalBaselineBuilder()
