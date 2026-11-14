"""Modelo exploratorio: longitud total equivalente del ensayo, no longitud clínica."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_engine.features.experimental_stretch_curve import (
    estimate_contact_span_mm,
    estimate_equivalent_total_length_mm,
    measured_trials,
)

TRIALS = (
    (1000.0, (630.0, 780.0, 795.0)),
    (1100.0, (1020.0, 1165.0, 1125.0)),
    (1200.0, (1110.0, 1255.0, 1260.0)),
    (1300.0, (1270.0, 1320.0, 1300.0)),
    (1400.0, (1350.0, 1355.0, 1350.0)),
)


def test_experimental_model_uses_mean_and_contact_span_ratio():
    assert estimate_equivalent_total_length_mm(3675.0, TRIALS, 100.0, 500.0) == pytest.approx(1000.0)
    assert estimate_equivalent_total_length_mm(6620.833333333333, TRIALS, 100.0, 500.0) == pytest.approx(1350.0)


def test_experimental_span_uses_personal_straight_reference():
    estimated = estimate_contact_span_mm(5650.0, 4500.0, TRIALS, 100.0, 500.0)
    equivalent_current = estimate_equivalent_total_length_mm(5650.0, TRIALS, 100.0, 500.0)
    equivalent_reference = estimate_equivalent_total_length_mm(4500.0, TRIALS, 100.0, 500.0)
    assert estimated == pytest.approx(538.6, abs=0.2)
    assert estimated == pytest.approx(500.0 * equivalent_current / equivalent_reference)
    assert estimate_contact_span_mm(4500.0, 4500.0, TRIALS, 100.0, 500.0) == 500.0


def test_experimental_span_requires_both_reference_and_current_in_range():
    assert estimate_contact_span_mm(7000.0, 4500.0, TRIALS, 100.0, 500.0) is None
    assert estimate_contact_span_mm(4500.0, 7000.0, TRIALS, 100.0, 500.0) is None


@pytest.mark.parametrize("resistance", [3674.99, 6760.0])
def test_experimental_model_never_extrapolates(resistance: float):
    assert estimate_equivalent_total_length_mm(resistance, TRIALS, 100.0, 500.0) is None


@pytest.mark.parametrize("span", [0.0, -1.0, float('nan')])
def test_invalid_contact_span_is_rejected(span: float):
    with pytest.raises(ValueError):
        estimate_equivalent_total_length_mm(4500.0, TRIALS, 100.0, span)


def test_non_increasing_means_are_rejected():
    with pytest.raises(ValueError):
        estimate_equivalent_total_length_mm(4500.0, ((1000.0, (1000.0,)), (1100.0, (900.0,))), 100.0, 500.0)


@pytest.mark.parametrize(("resistance", "length"), [
    (3675.0, 1000.0),
    (1103.3333333333333 * 5, 1100.0),
    (1208.3333333333333 * 5, 1200.0),
    (1296.6666666666667 * 5, 1300.0),
    (1351.6666666666667 * 5, 1400.0),
])
def test_every_measured_knot_is_inclusive_and_order_independent(resistance, length):
    assert estimate_equivalent_total_length_mm(resistance, tuple(reversed(TRIALS)), 100.0, 500.0) == pytest.approx(length)


@pytest.mark.parametrize("resistance", [0, -1, float("nan"), float("inf")])
def test_invalid_resistance_is_rejected(resistance):
    with pytest.raises(ValueError, match="resistance must"):
        estimate_equivalent_total_length_mm(resistance, TRIALS, 100.0, 500.0)


@pytest.mark.parametrize("bench_span", [0, -1, float("nan"), float("inf")])
def test_invalid_bench_spacing_is_rejected(bench_span):
    with pytest.raises(ValueError, match="contact spans"):
        estimate_equivalent_total_length_mm(4500.0, TRIALS, bench_span, 500.0)


@pytest.mark.parametrize("trials", [
    (),
    ((1000.0, (1000.0,)),),
])
def test_at_least_two_trial_lengths_required(trials):
    with pytest.raises(ValueError, match="at least two"):
        estimate_equivalent_total_length_mm(4500.0, trials, 100.0, 500.0)


@pytest.mark.parametrize("point", [
    (0.0, (1000.0,)), (-1.0, (1000.0,)), (float("nan"), (1000.0,)),
    (1000.0, ()), (1000.0, (0.0,)), (1000.0, (-1.0,)), (1000.0, (float("nan"),)),
])
def test_invalid_trial_data_rejected(point):
    with pytest.raises(ValueError, match="trial lengths and readings"):
        estimate_equivalent_total_length_mm(4500.0, (point, (1100.0, (1100.0,))), 100.0, 500.0)


@pytest.mark.parametrize("points", [
    ((1000.0, (1000.0,)), (1000.0, (1100.0,))),
    ((1000.0, (1000.0,)), (1100.0, (1000.0,))),
])
def test_duplicate_length_or_mean_resistance_rejected(points):
    with pytest.raises(ValueError, match="increase strictly"):
        estimate_equivalent_total_length_mm(4500.0, points, 100.0, 500.0)


@pytest.mark.parametrize(("bench", "span"), [(1e308, 5e-324), (5e-324, 1e308)])
def test_nonrepresentable_contact_span_ratio_rejected(bench, span):
    with pytest.raises(ValueError, match="ratio"):
        estimate_equivalent_total_length_mm(4500.0, TRIALS, bench, span)


def test_measured_trials_reads_all_original_samples_and_spacing():
    spacing, measurements = measured_trials()
    assert spacing == 100
    assert measurements == TRIALS


@pytest.mark.property
@given(st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False))
def test_interpolation_stays_between_adjacent_measured_lengths(fraction):
    r0, r1 = 3675.0, 5516.666666666667
    resistance = r0 + fraction * (r1 - r0)
    result = estimate_equivalent_total_length_mm(resistance, TRIALS, 100.0, 500.0)
    assert result == pytest.approx(1000.0 + fraction * 100.0)
