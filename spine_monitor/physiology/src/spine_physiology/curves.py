"""Piecewise-linear curves and exercise-dose drift.

Owner: B1. Implements `PiecewiseLinear` (linear interpolation between ordered points, constant
outside the ends, scalar and vectorized) and `dose_drift` (cumulative drift from the difference between
the actual and the planned dose, see the convention in `config`). Pure functions: no RNG.
"""

from collections.abc import Sequence

import numpy as np

from spine_physiology.config import LatentCurve


class PiecewiseLinear:
    """y(x) piecewise linear over `points` (x strictly increasing); outside the range, the end value."""

    def __init__(self, points: Sequence[tuple[float, float]]) -> None:
        if len(points) < 1:
            raise ValueError("PiecewiseLinear needs at least one point")
        xs = np.array([p[0] for p in points], dtype=np.float64)
        ys = np.array([p[1] for p in points], dtype=np.float64)
        if np.any(np.diff(xs) <= 0):
            raise ValueError(f"points must be strictly increasing in x: {xs.tolist()}")
        self._xs = xs
        self._ys = ys

    @classmethod
    def from_curve(cls, curve: LatentCurve) -> PiecewiseLinear:
        """Curve of a config latent (x = mission_day)."""
        return cls(curve.pairs())

    def __call__(self, x: float) -> float:
        # np.interp keeps the end value outside the range.
        return float(np.interp(x, self._xs, self._ys))

    def values(self, xs: np.ndarray) -> np.ndarray:
        """Vectorized version of `__call__` (float64, same shape as `xs`)."""
        return np.interp(np.asarray(xs, dtype=np.float64), self._xs, self._ys)


def dose_drift(dose_ratios: Sequence[float], drift_per_day: float) -> np.ndarray:
    """Cumulative day-by-day drift: out[d] = Σ_{k<=d} drift_per_day × (1 − dose_ratios[k]).

    `dose_ratios[k]` = actual minutes / planned minutes of day k (1 = meets the plan).
    """
    ratios = np.asarray(dose_ratios, dtype=np.float64)
    return np.cumsum(drift_per_day * (1.0 - ratios))
