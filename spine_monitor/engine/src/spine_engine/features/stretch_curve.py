"""Pure conversion of the stretch cord (#45): ADC counts -> resistance -> length -> elongation.

No I/O, clock or randomness. All functions accept a scalar or ``ndarray`` and return float64 ``ndarray``
(0-d for a scalar).

- ``adc_to_resistance``: inverse of the simulator's divider (``Vout = vref·R/(R+Rs)``,
  ``counts = round(Vout/vref·full)``, ``full = 2^bits − 1``): ``R = Rs·c/(full − c)``. ``vref`` cancels out.
  Counts outside ``(0, full)`` are not invertible (``c <= 0`` gives R = 0 or negative; ``c >= full`` is the saturated
  ADC, infinite R): they come out ``NaN``, without an exception; the caller discards them.
- ``resistance_to_length``: ``piecewise_linear`` curve of ``StretchCalibration`` (resistance -> length),
  piecewise linear interpolation. Outside the resistance range of the points it SATURATES to the length of the end point;
  ``out_of_curve_range`` says which samples fell outside so the family can flag quality. ``NaN`` goes in and comes out
  ``NaN`` (and does not count as out of range). Points are sorted by resistance; fewer than two points,
  repeated resistances or non-finite values in the points raise ``StretchCurveError`` (a ``ValueError``), which the
  family turns into a skip.
- ``elongation_mm = length − rest_length_mm`` and ``elongation_pct = 100·elongation_mm/rest_length_mm``;
  ``rest_length_mm <= 0`` (or non-finite) raises ``StretchCurveError``.
"""

from collections.abc import Sequence
from typing import Protocol

import numpy as np
from numpy.typing import ArrayLike, NDArray

type FloatArray = NDArray[np.float64]


class StretchCurveError(ValueError):
    """Unusable cord calibration (degenerate curve or non-positive rest length)."""


class CurvePoint(Protocol):
    """Point of the contract's ``curve.points`` (``spine_contracts`` ``Point``)."""

    @property
    def resistance_ohm(self) -> float: ...

    @property
    def length_mm(self) -> float: ...


def adc_to_resistance(counts: ArrayLike, *, adc_bits: int, series_resistor_ohm: float) -> FloatArray:
    """Cord resistance (Ω) per sample; ``NaN`` where the counts fall outside ``(0, 2^bits − 1)``.

    Note: the simulator clips the counts to ``[0.5, full − 0.5]`` and returns a finite R; here counts at 0 or
    ``full`` are not invertible and come out ``NaN`` (they are discarded). Deliberate divergence, pending confirmation.
    """
    c = np.asarray(counts, dtype=np.float64)
    full = float(2**adc_bits - 1)
    valid = (c > 0.0) & (c < full)
    safe = np.where(valid, c, 1.0)
    return np.where(valid, series_resistor_ohm * safe / (full - safe), np.nan)


def _knots(points: Sequence[CurvePoint]) -> tuple[FloatArray, FloatArray]:
    """(resistances, lengths) sorted by resistance; error if there are < 2 points, non-finite or repeated values."""
    if len(points) < 2:
        raise StretchCurveError(f"stretch curve needs at least 2 points, got {len(points)}")
    ordered = sorted(points, key=lambda p: p.resistance_ohm)
    r = np.array([p.resistance_ohm for p in ordered], dtype=np.float64)
    lengths = np.array([p.length_mm for p in ordered], dtype=np.float64)
    if not (np.all(np.isfinite(r)) and np.all(np.isfinite(lengths))):
        raise StretchCurveError("stretch curve points must have finite resistance_ohm and length_mm")
    if np.any(np.diff(r) == 0.0):
        raise StretchCurveError("stretch curve has duplicated resistance_ohm values")
    return r, lengths


def resistance_to_length(resistance_ohm: ArrayLike, points: Sequence[CurvePoint]) -> FloatArray:
    """Cord length (mm) per sample; saturates to the end point outside the curve and preserves ``NaN``."""
    r_knots, l_knots = _knots(points)
    return np.interp(np.asarray(resistance_ohm, dtype=np.float64), r_knots, l_knots)


def out_of_curve_range(resistance_ohm: ArrayLike, points: Sequence[CurvePoint]) -> NDArray[np.bool_]:
    """``True`` where the resistance falls outside the curve's ``[R_min, R_max]`` (``NaN`` gives ``False``)."""
    r_knots, _ = _knots(points)
    r = np.asarray(resistance_ohm, dtype=np.float64)
    return (r < r_knots[0]) | (r > r_knots[-1])


def _check_rest(rest_length_mm: float) -> None:
    if not (np.isfinite(rest_length_mm) and rest_length_mm > 0.0):
        raise StretchCurveError(f"rest_length_mm must be finite and > 0, got {rest_length_mm}")


def elongation_mm(length_mm: ArrayLike, rest_length_mm: float) -> FloatArray:
    """Elongation (mm) = length − rest length."""
    _check_rest(rest_length_mm)
    return np.asarray(length_mm, dtype=np.float64) - rest_length_mm


def elongation_pct(length_mm: ArrayLike, rest_length_mm: float) -> FloatArray:
    """Elongation (%) = 100 · (length − rest) / rest."""
    return 100.0 * elongation_mm(length_mm, rest_length_mm) / rest_length_mm
