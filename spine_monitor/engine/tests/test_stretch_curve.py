"""Conversión del cordón (#45): ADC -> resistencia -> longitud -> elongación."""

import math
from typing import NamedTuple

import numpy as np
import pytest
from spine_contracts.models.domain.calibration_schema import Point
from spine_engine.features.stretch_curve import (
    StretchCurveError,
    adc_to_resistance,
    elongation_mm,
    elongation_pct,
    out_of_curve_range,
    resistance_to_length,
)

# 10 bits -> full = 1023; Rs = 1000 Ω.
_BITS = 10
_FULL = 1023
_RS = 1000.0


def test_adc_to_resistance_inverts_divider_exactly() -> None:
    # c = 341 -> R = 1000 * 341 / 682 = 500 Ω.
    assert adc_to_resistance(341, adc_bits=_BITS, series_resistor_ohm=_RS) == 500.0


def test_adc_to_resistance_is_vectorised() -> None:
    out = adc_to_resistance(np.array([341, 682]), adc_bits=_BITS, series_resistor_ohm=_RS)
    np.testing.assert_array_equal(out, [500.0, 2000.0])


@pytest.mark.parametrize("counts", [0, -3, _FULL, _FULL + 5])
def test_adc_to_resistance_non_invertible_counts_give_nan(counts: int) -> None:
    assert math.isnan(float(adc_to_resistance(counts, adc_bits=_BITS, series_resistor_ohm=_RS)))


def test_adc_to_resistance_keeps_valid_samples_next_to_saturated_ones() -> None:
    out = adc_to_resistance(np.array([0, 1, _FULL - 1, _FULL]), adc_bits=_BITS, series_resistor_ohm=_RS)
    assert math.isnan(out[0])
    assert out[1] == pytest.approx(_RS / 1022)
    assert out[2] == pytest.approx(_RS * 1022)
    assert math.isnan(out[3])


def _pts(*pairs: tuple[float, float]) -> list[Point]:
    return [Point(resistance_ohm=r, length_mm=length) for r, length in pairs]


_CURVE = _pts((100.0, 200.0), (200.0, 220.0), (400.0, 230.0))


@pytest.mark.parametrize(
    ("r", "expected"),
    [(100.0, 200.0), (150.0, 210.0), (200.0, 220.0), (300.0, 225.0), (400.0, 230.0)],
)
def test_resistance_to_length_interpolates_between_knots(r: float, expected: float) -> None:
    assert float(resistance_to_length(r, _CURVE)) == pytest.approx(expected, abs=1e-12)


def test_resistance_to_length_two_point_curve() -> None:
    curve = _pts((10.0, 50.0), (20.0, 70.0))
    np.testing.assert_allclose(resistance_to_length(np.array([10.0, 12.5, 20.0]), curve), [50.0, 55.0, 70.0])


def test_resistance_to_length_saturates_outside_curve() -> None:
    out = resistance_to_length(np.array([1.0, 99.9, 400.1, 1e9]), _CURVE)
    np.testing.assert_array_equal(out, [200.0, 200.0, 230.0, 230.0])


def test_resistance_to_length_sorts_unordered_points() -> None:
    curve = _pts((400.0, 230.0), (100.0, 200.0), (200.0, 220.0))
    assert float(resistance_to_length(150.0, curve)) == pytest.approx(210.0)


def test_resistance_to_length_propagates_nan() -> None:
    out = resistance_to_length(np.array([np.nan, 150.0]), _CURVE)
    assert math.isnan(out[0])
    assert out[1] == pytest.approx(210.0)


def test_out_of_curve_range_flags_only_outside_samples() -> None:
    r = np.array([99.9, 100.0, 250.0, 400.0, 400.1, np.nan])
    np.testing.assert_array_equal(out_of_curve_range(r, _CURVE), [True, False, False, False, True, False])


@pytest.mark.parametrize(
    "curve",
    [
        _pts((100.0, 200.0)),
        [],
        _pts((100.0, 200.0), (100.0, 210.0)),
        _pts((100.0, 200.0), (300.0, 220.0), (100.0, 230.0)),
    ],
    ids=["one_point", "empty", "duplicate", "duplicate_unordered"],
)
@pytest.mark.parametrize("fn", [resistance_to_length, out_of_curve_range])
def test_degenerate_curve_raises_domain_error(curve: list[Point], fn: object) -> None:
    with pytest.raises(StretchCurveError):
        fn(150.0, curve)  # pyright: ignore[reportCallIssue, reportOperatorIssue]  # fn es una de las dos funciones


class _RawPoint(NamedTuple):
    """Punto sin validar (el ``Point`` del contrato ya rechaza NaN/inf): el motor no debe fiarse de ello."""

    resistance_ohm: float
    length_mm: float


@pytest.mark.parametrize(
    "bad_point",
    [(math.nan, 210.0), (math.inf, 210.0), (-math.inf, 210.0), (150.0, math.nan), (150.0, math.inf)],
    ids=["r_nan", "r_inf", "r_neg_inf", "length_nan", "length_inf"],
)
@pytest.mark.parametrize("fn", [resistance_to_length, out_of_curve_range])
def test_non_finite_curve_point_raises_domain_error(bad_point: tuple[float, float], fn: object) -> None:
    curve = [_RawPoint(100.0, 200.0), _RawPoint(*bad_point), _RawPoint(400.0, 230.0)]
    with pytest.raises(StretchCurveError, match="finite"):
        fn(150.0, curve)  # pyright: ignore[reportCallIssue, reportOperatorIssue]  # fn es una de las dos funciones


def test_stretch_curve_error_is_value_error() -> None:
    assert issubclass(StretchCurveError, ValueError)


def test_elongation_mm_subtracts_rest_length() -> None:
    np.testing.assert_array_equal(elongation_mm(np.array([200.0, 203.5, 198.0]), 200.0), [0.0, 3.5, -2.0])


def test_elongation_pct_relative_to_rest_length() -> None:
    np.testing.assert_allclose(elongation_pct(np.array([200.0, 210.0, 190.0]), 200.0), [0.0, 5.0, -5.0])


@pytest.mark.parametrize("rest", [0.0, -1.0, math.nan, math.inf])
@pytest.mark.parametrize("fn", [elongation_mm, elongation_pct])
def test_elongation_rejects_non_positive_rest_length(rest: float, fn: object) -> None:
    with pytest.raises(StretchCurveError):
        fn(np.array([200.0]), rest)  # pyright: ignore[reportCallIssue, reportOperatorIssue]  # fn es una de las dos funciones
