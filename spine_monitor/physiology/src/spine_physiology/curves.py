"""Curvas piecewise-linear y deriva por dosis de ejercicio.

Dueño: B1. Debe implementar `PiecewiseLinear` (interpolación lineal entre puntos ordenados, constante
fuera de los extremos, escalar y vectorizada) y `dose_drift` (deriva acumulada por la diferencia entre
la dosis real y la planificada, ver la convención en `config`). Funciones puras: sin RNG.
"""

from collections.abc import Sequence

import numpy as np

from spine_physiology.config import LatentCurve


class PiecewiseLinear:
    """y(x) lineal a trozos por `points` (x estrictamente creciente); fuera del rango, el valor del extremo."""

    def __init__(self, points: Sequence[tuple[float, float]]) -> None:
        raise NotImplementedError

    @classmethod
    def from_curve(cls, curve: LatentCurve) -> PiecewiseLinear:
        """Curva de un latente del config (x = mission_day)."""
        raise NotImplementedError

    def __call__(self, x: float) -> float:
        raise NotImplementedError

    def values(self, xs: np.ndarray) -> np.ndarray:
        """Versión vectorizada de `__call__` (float64, misma forma que `xs`)."""
        raise NotImplementedError


def dose_drift(dose_ratios: Sequence[float], drift_per_day: float) -> np.ndarray:
    """Deriva acumulada día a día: out[d] = Σ_{k<=d} drift_per_day × (1 − dose_ratios[k]).

    `dose_ratios[k]` = minutos reales / minutos planificados del día k (1 = cumple el plan).
    """
    raise NotImplementedError
