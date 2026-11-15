"""Personal baseline strategy: z of the latest session + sustained change over the history."""

from collections.abc import Sequence

from spine_contracts import Baseline, FeatureSet

from spine_engine.interfaces import ChangeDetector, Deviation, DeviationScorer
from spine_engine.reference import index_entries, reference_entry


class StatisticalBaselineStrategy:
    """Implements `BaselineStrategy`. Scores and detects with the strategies injected into it.

    Scoring uses the latest value of each feature. The history of feature sets (same
    protocol and gravity environment) feeds the detector, which says whether the change is sustained or noise.
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
            observations = {}
            for index, other in enumerate(ordered):
                if other.protocol_id != feature_set.protocol_id or other.gravity_environment != feature_set.gravity_environment:
                    continue
                for f in other.features:
                    if f.feature_id != feature_id:
                        continue
                    window = f.window or other.window
                    # Windowless observations have no identity by which to deduplicate.
                    key = (window.start, window.end) if window is not None else (index, None)
                    observations[key] = self._scorer.score(f.value, entry)
            series = list(observations.values())
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
