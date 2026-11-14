"""Conversión pura del cordón de estiramiento (#45): cuentas ADC -> resistencia -> longitud -> elongación.

Sin I/O, reloj ni azar. Todas las funciones aceptan escalar o ``ndarray`` y devuelven ``ndarray`` float64
(0-d para escalar).

- ``adc_to_resistance``: inversa del divisor del simulador (``Vout = vref·R/(R+Rs)``,
  ``counts = round(Vout/vref·full)``, ``full = 2^bits − 1``): ``R = Rs·c/(full − c)``. ``vref`` se cancela.
  Las cuentas fuera de ``(0, full)`` no son invertibles (``c <= 0`` da R = 0 o negativa; ``c >= full`` es el ADC
  saturado, R infinita): salen ``NaN``, sin excepción; el llamador las descarta.
- ``resistance_to_length``: curva ``piecewise_linear`` de ``StretchCalibration`` (resistencia -> longitud),
  interpolación lineal por tramos. Fuera del rango de resistencias de los puntos SATURA a la longitud del extremo;
  ``out_of_curve_range`` dice qué muestras quedaron fuera para que la familia marque calidad. ``NaN`` entra y sale
  ``NaN`` (y no cuenta como fuera de rango). Los puntos se ordenan por resistencia; menos de dos puntos,
  resistencias repetidas o valores no finitos en los puntos lanzan ``StretchCurveError`` (un ``ValueError``), que la
  familia convierte en skip.
- ``elongation_mm = length − rest_length_mm`` y ``elongation_pct = 100·elongation_mm/rest_length_mm``;
  ``rest_length_mm <= 0`` (o no finito) lanza ``StretchCurveError``.
"""

from collections.abc import Sequence
from typing import Protocol

import numpy as np
from numpy.typing import ArrayLike, NDArray

type FloatArray = NDArray[np.float64]


class StretchCurveError(ValueError):
    """Calibración del cordón inutilizable (curva degenerada o longitud de reposo no positiva)."""


class CurvePoint(Protocol):
    """Punto de ``curve.points`` del contrato (``spine_contracts`` ``Point``)."""

    @property
    def resistance_ohm(self) -> float: ...

    @property
    def length_mm(self) -> float: ...


def adc_to_resistance(counts: ArrayLike, *, adc_bits: int, series_resistor_ohm: float) -> FloatArray:
    """Resistencia del cordón (Ω) por muestra; ``NaN`` donde las cuentas caen fuera de ``(0, 2^bits − 1)``.

    Nota: el simulador recorta las cuentas a ``[0.5, full − 0.5]`` y devuelve R finita; aquí las cuentas en 0 o
    ``full`` son no invertibles y salen ``NaN`` (se descartan). Divergencia deliberada, pendiente de confirmar con Uri.
    """
    c = np.asarray(counts, dtype=np.float64)
    full = float(2**adc_bits - 1)
    valid = (c > 0.0) & (c < full)
    safe = np.where(valid, c, 1.0)
    return np.where(valid, series_resistor_ohm * safe / (full - safe), np.nan)


def _knots(points: Sequence[CurvePoint]) -> tuple[FloatArray, FloatArray]:
    """(resistencias, longitudes) ordenadas por resistencia; error si hay < 2 puntos, valores no finitos o repetidos."""
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
    """Longitud del cordón (mm) por muestra; satura al extremo fuera de la curva y conserva ``NaN``."""
    r_knots, l_knots = _knots(points)
    return np.interp(np.asarray(resistance_ohm, dtype=np.float64), r_knots, l_knots)


def out_of_curve_range(resistance_ohm: ArrayLike, points: Sequence[CurvePoint]) -> NDArray[np.bool_]:
    """``True`` donde la resistencia queda fuera de ``[R_min, R_max]`` de la curva (``NaN`` da ``False``)."""
    r_knots, _ = _knots(points)
    r = np.asarray(resistance_ohm, dtype=np.float64)
    return (r < r_knots[0]) | (r > r_knots[-1])


def _check_rest(rest_length_mm: float) -> None:
    if not (np.isfinite(rest_length_mm) and rest_length_mm > 0.0):
        raise StretchCurveError(f"rest_length_mm must be finite and > 0, got {rest_length_mm}")


def elongation_mm(length_mm: ArrayLike, rest_length_mm: float) -> FloatArray:
    """Elongación (mm) = longitud − longitud de reposo."""
    _check_rest(rest_length_mm)
    return np.asarray(length_mm, dtype=np.float64) - rest_length_mm


def elongation_pct(length_mm: ArrayLike, rest_length_mm: float) -> FloatArray:
    """Elongación (%) = 100 · (longitud − reposo) / reposo."""
    return 100.0 * elongation_mm(length_mm, rest_length_mm) / rest_length_mm
