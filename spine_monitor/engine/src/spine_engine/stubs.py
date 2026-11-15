"""Null implementations: they invent no metrics or thresholds (they depend on the clinical model)."""

from collections.abc import Sequence
from datetime import UTC, datetime

from spine_contracts import SCHEMA_VERSION, Baseline, FeatureSet, FeaturesRequest, FeatureValue, Level

from spine_engine.interfaces import Deviation, RiskResult, TriageContext, TriageDecision
from spine_engine.pipeline import AssessmentPipeline
from spine_engine.reference import index_entries, reference_entry


class NullBaselineStrategy:
    """Delta of the latest value of each feature against the mean of its baseline entry.

    The entry is that of the same protocol and gravity environment; if it does not exist, the Earth one
    (this measures the environment effect). Features without an entry generate no deviation.
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


class NullFeatureExtractor:
    """Empty FeatureSet with the session metadata: it invents no feature."""

    def extract(self, request: FeaturesRequest) -> FeatureSet:
        session = request.session
        return FeatureSet(  # pyright: ignore[reportCallIssue]  # generated model: window/skipped have positional Field defaults
            schema_version=SCHEMA_VERSION,
            astronaut_id=session.astronaut_id,
            session_id=session.session_id,
            protocol_id=session.protocol_id,
            gravity_environment=session.mission_context.gravity_environment,
            source=session.source,
            computed_at=datetime.now(UTC),
            features=[],
        )


class NullRiskModel:
    def score(self, deviations: Sequence[Deviation], completeness: float) -> RiskResult:
        return RiskResult(score=0.0)


class NullTriagePolicy:
    def decide(self, context: TriageContext) -> TriageDecision:
        return TriageDecision(level=Level.GREEN)


def stub_pipeline() -> AssessmentPipeline:
    return AssessmentPipeline(NullBaselineStrategy(), NullRiskModel(), NullTriagePolicy())
