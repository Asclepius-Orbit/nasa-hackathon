"""Generic risk model over the deviations: no clinical ranges or per-feature risk direction."""

import math
from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol

from spine_contracts import AssessmentDomain, Driver

from spine_engine.interfaces import Deviation, RiskResult
from spine_engine.pipeline import domain_of

# ASSUMPTION: provisional weights, pending clinical validation. Multiplier of a driver's contribution by the level of the
# catalog range its value falls in. Alert keeps the generic weight; a value that is clinically normal is
# damped, a critical one is amplified. A value in no range, or a feature without ranges, uses 1.0.
RANGE_LEVEL_WEIGHTS: Final[Mapping[str, float]] = {"normal": 0.5, "alert": 1.0, "critical": 2.0}
_WORST_FIRST: Final = ("critical", "alert", "normal")


class CatalogView(Protocol):
    """What the model reads from the feature catalog. Raises `KeyError` with an id it does not know."""

    def risk_direction(self, feature_id: str) -> str: ...

    def ranges(self, feature_id: str) -> dict[str, Any] | None: ...


def _within(bounds: Mapping[str, float | None], value: float) -> bool:
    lo, hi = bounds.get("min"), bounds.get("max")
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def range_level(ranges: Mapping[str, Any], value: float) -> str | None:
    """Level (critical, alert or normal) of the range containing `value`; ends inclusive, the worst wins."""
    for level in _WORST_FIRST:
        bounds = ranges.get(level)
        if bounds is not None and _within(bounds, value):
            return level
    return None


class DeviationRiskModel:
    """Implements `RiskModel`. Combines the |z| of the sustained deviations.

    Each sustained deviation contributes `|z| · weight of its domain · persistence`, with persistence
    `min(n_points, persistence_points) / persistence_points`. The sum is saturated to [0, 1] with
    `1 - exp(-sum / saturation)`, computed with `expm1` so as not to lose tiny sums (with very
    large sums rounding reaches 1.0). Without a known `risk_direction`, any direction is worse: |z|
    is used, and the sign only travels in the driver's `deviation_z`. An isolated deviation, without z or of a
    feature without a domain contributes no risk. Confidence is completeness: what is missing does not raise it.

    With a catalog (`CatalogView`), the feature's risk direction filters the deviation: `lower_is_worse`
    only counts if it goes down, `higher_is_worse` only if it goes up, `none` never counts and `either_is_worse` or
    `unspecified` use |z|. The direction is `Deviation.direction` and, if missing, the sign of z. If the feature
    has ranges, the level where its value falls (ends inclusive, the worst wins at a shared end)
    multiplies the contribution with `RANGE_LEVEL_WEIGHTS`; a value outside every range or a feature without
    ranges stays in the generic z model. So does an id the catalog does not know. Without a catalog
    (the default) none of this applies.

    Limitation: `n_points` is the length of the feature's series, not the final deviated stretch, and the
    detectors require a minimum number of points to mark a deviation as sustained. With
    `persistence_points` less than or equal to that minimum the persistence factor is always 1.
    A domain absent from `domain_weights` contributes no risk (`EngineSettings` fills them in).
    """

    def __init__(
        self,
        domain_weights: Mapping[AssessmentDomain, float],
        persistence_points: int,
        saturation: float,
        catalog: CatalogView | None = None,
    ) -> None:
        if persistence_points < 1:
            raise ValueError("persistence_points must be at least 1")
        if not math.isfinite(saturation):
            raise ValueError("saturation must be finite")
        if saturation <= 0:
            raise ValueError("saturation must be positive")
        if not all(math.isfinite(weight) for weight in domain_weights.values()):
            raise ValueError("domain weights must be finite")
        if any(weight < 0 for weight in domain_weights.values()):
            raise ValueError("domain weights must not be negative")
        self._weights = dict(domain_weights)
        self._persistence_points = persistence_points
        self._saturation = saturation
        self._catalog = catalog

    def _catalog_weight(self, deviation: Deviation, z: float) -> float:
        """Catalog factor on the contribution: 0 if the deviation goes in the good direction."""
        if self._catalog is None:
            return 1.0
        try:
            direction = self._catalog.risk_direction(deviation.feature_id)
            ranges = self._catalog.ranges(deviation.feature_id)
        except KeyError:
            return 1.0
        moved = deviation.direction or ("down" if z < 0 else "up")
        if direction == "none" or (direction == "lower_is_worse" and moved != "down") or (direction == "higher_is_worse" and moved != "up"):
            return 0.0
        level = None if ranges is None else range_level(ranges, deviation.value)
        return 1.0 if level is None else RANGE_LEVEL_WEIGHTS[level]

    def _raw(self, deviation: Deviation) -> float:
        domain = domain_of(deviation.feature_id)
        if not deviation.sustained or deviation.z is None or domain is None:
            return 0.0
        persistence = min(deviation.n_points, self._persistence_points) / self._persistence_points
        return abs(deviation.z) * self._weights.get(domain, 0.0) * persistence * self._catalog_weight(deviation, deviation.z)

    def score(self, deviations: Sequence[Deviation], completeness: float) -> RiskResult:
        raw = [(d, c) for d in deviations if (c := self._raw(d)) > 0]
        total = sum(c for _, c in raw)
        score = -math.expm1(-total / self._saturation)
        drivers = [
            Driver(feature_id=d.feature_id, value=d.value, deviation_z=d.z, contribution=score * c / total)
            for d, c in sorted(raw, key=lambda item: (-item[1], item[0].feature_id))
        ]
        return RiskResult(score=score, drivers=drivers, confidence=completeness)
