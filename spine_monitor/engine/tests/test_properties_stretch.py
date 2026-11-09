"""Invariantes de la conversión del cordón (#45)."""

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts.models.domain.calibration_schema import Point
from spine_engine.features.stretch_curve import (
    adc_to_resistance,
    elongation_mm,
    elongation_pct,
    resistance_to_length,
)

pytestmark = pytest.mark.property

_step = st.floats(min_value=0.5, max_value=500.0, allow_nan=False, allow_infinity=False)
_positive = st.floats(min_value=1.0, max_value=1e4, allow_nan=False, allow_infinity=False)


@st.composite
def curves(draw: st.DrawFn, *, monotone: bool) -> list[Point]:
    """Curva válida construida directamente: resistencias estrictamente crecientes (sin duplicados)."""
    n = draw(st.integers(min_value=2, max_value=8))
    r = np.cumsum([draw(_positive), *draw(st.lists(_step, min_size=n - 1, max_size=n - 1))])
    if monotone:
        lengths = np.cumsum([draw(_positive), *draw(st.lists(_step, min_size=n - 1, max_size=n - 1))])
    else:
        lengths = np.array(draw(st.lists(_positive, min_size=n, max_size=n)))
    points = [Point(resistance_ohm=float(a), length_mm=float(b)) for a, b in zip(r, lengths, strict=True)]
    return draw(st.permutations(points))


def _divider_counts(r: float, rs: float, full: int) -> int:
    """Cuentas del simulador sin ruido: round(Vout/vref · full) con Vout = vref·R/(R+Rs)."""
    return round(r / (r + rs) * full)


@given(
    bits=st.integers(min_value=10, max_value=16),
    rs=st.floats(min_value=10.0, max_value=1e6),
    ratio=st.floats(min_value=0.01, max_value=100.0),
)
def test_divider_roundtrip_is_within_quantisation(bits: int, rs: float, ratio: float) -> None:
    full = 2**bits - 1
    r = ratio * rs
    x = full * r / (r + rs)  # cuentas sin redondear; |round(x) − x| <= 0.5
    back = float(adc_to_resistance(_divider_counts(r, rs, full), adc_bits=bits, series_resistor_ohm=rs))
    # R(c) = Rs·c/(full − c) es creciente: el error queda acotado por el salto de R en ±0.5 cuentas.
    bound = rs * (x + 0.5) / (full - x - 0.5) - rs * (x - 0.5) / (full - x + 0.5)
    assert abs(back - r) <= bound * (1 + 1e-9)


@given(curve=curves(monotone=False))
def test_curve_passes_through_its_knots(curve: list[Point]) -> None:
    r = np.array([p.resistance_ohm for p in curve])
    np.testing.assert_allclose(resistance_to_length(r, curve), [p.length_mm for p in curve], rtol=1e-12)


@given(curve=curves(monotone=True), rs=st.lists(st.floats(min_value=0.0, max_value=1e5), min_size=2, max_size=30))
def test_monotone_curve_gives_monotone_output(curve: list[Point], rs: list[float]) -> None:
    out = resistance_to_length(np.sort(rs), curve)
    assert np.all(np.diff(out) >= 0.0)


@given(curve=curves(monotone=False), r=st.floats(min_value=0.0, max_value=1e6))
def test_output_stays_within_curve_length_range(curve: list[Point], r: float) -> None:
    lengths = [p.length_mm for p in curve]
    assert min(lengths) <= float(resistance_to_length(r, curve)) <= max(lengths)


@given(rest=_positive, d=_positive)
def test_elongation_is_anchored_at_rest_length(rest: float, d: float) -> None:
    # Ancla escala y signo: reposo -> 0, reposo + d -> +d (estirado), reposo - d -> -d (acortado).
    assert float(elongation_mm(rest, rest)) == 0.0
    assert float(elongation_pct(rest, rest)) == 0.0
    np.testing.assert_allclose(elongation_mm(rest + d, rest), d, rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(elongation_mm(rest - d, rest), -d, rtol=1e-9, atol=1e-9)
    assert float(elongation_pct(rest + d, rest)) > 0.0
    assert float(elongation_pct(rest - d, rest)) < 0.0


@given(rest=_positive, d1=_positive, d2=_positive)
def test_elongation_pct_is_additive_in_elongation(rest: float, d1: float, d2: float) -> None:
    pct = elongation_pct(np.array([rest + d1, rest + d2, rest + d1 + d2]), rest)
    np.testing.assert_allclose(pct[2], pct[0] + pct[1], rtol=1e-9)
