"""Curvas piecewise-linear y deriva por dosis de ejercicio.

Dueño: B1. Implementa `PiecewiseLinear` (interpolación lineal entre puntos ordenados, constante
fuera de los extremos, escalar y vectorizada) y `dose_drift` (deriva acumulada por la diferencia entre
la dosis real y la planificada, ver la convención en `config`). Funciones puras: sin RNG.
"""

from collections.abc import Sequence

import numpy as np

from spine_physiology.config import LatentCurve


class PiecewiseLinear:
    """y(x) lineal a trozos por `points` (x estrictamente creciente); fuera del rango, el valor del extremo."""

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
        """Curva de un latente del config (x = mission_day)."""
        return cls(curve.pairs())

    def __call__(self, x: float) -> float:
        # np.interp mantiene el valor del extremo fuera del rango.
        return float(np.interp(x, self._xs, self._ys))

    def values(self, xs: np.ndarray) -> np.ndarray:
        """Versión vectorizada de `__call__` (float64, misma forma que `xs`)."""
        return np.interp(np.asarray(xs, dtype=np.float64), self._xs, self._ys)


def dose_drift(dose_ratios: Sequence[float], drift_per_day: float) -> np.ndarray:
    """Deriva acumulada día a día: out[d] = Σ_{k<=d} drift_per_day × (1 − dose_ratios[k]).

    `dose_ratios[k]` = minutos reales / minutos planificados del día k (1 = cumple el plan).
    """
    ratios = np.asarray(dose_ratios, dtype=np.float64)
    return np.cumsum(drift_per_day * (1.0 - ratios))
