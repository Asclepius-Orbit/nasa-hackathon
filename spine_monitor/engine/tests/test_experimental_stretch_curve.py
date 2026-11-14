"""Modelo exploratorio: longitud total equivalente del ensayo, no longitud clínica."""

import pytest
from spine_engine.features.experimental_stretch_curve import estimate_contact_span_mm, estimate_equivalent_total_length_mm

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
