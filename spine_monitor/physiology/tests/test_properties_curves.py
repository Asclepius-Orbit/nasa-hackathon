from itertools import accumulate

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_physiology.curves import PiecewiseLinear, dose_drift

pytestmark = pytest.mark.property

coord = st.floats(min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False)


@st.composite
def curves(draw):
    # x estrictamente creciente por construcción (huecos positivos acumulados), sin assume/filter.
    start = draw(coord)
    gaps = draw(st.lists(st.floats(min_value=1e-3, max_value=10), max_size=7))
    xs = list(accumulate(gaps, initial=start))
    ys = [draw(coord) for _ in xs]
    return list(zip(xs, ys, strict=True))


@given(points=curves())
def test_curve_passes_through_its_knots(points):
    curve = PiecewiseLinear(points)
    for x, y in points:
        assert curve(x) == pytest.approx(y, abs=1e-9)


@given(points=curves(), x=coord)
def test_curve_stays_within_the_knot_range(points, x):
    ys = [y for _, y in points]
    assert min(ys) - 1e-9 <= PiecewiseLinear(points)(x) <= max(ys) + 1e-9


@given(points=curves(), xs=st.lists(coord, min_size=1, max_size=20))
def test_vectorised_values_match_the_scalar_call(points, xs):
    curve = PiecewiseLinear(points)
    assert np.allclose(curve.values(np.array(xs)), [curve(x) for x in xs])


@given(drift=coord, n=st.integers(min_value=1, max_value=30))
def test_dose_drift_is_zero_when_the_plan_is_met(drift, n):
    assert np.allclose(dose_drift([1.0] * n, drift), 0.0)


ratio_lists = st.lists(st.floats(min_value=0.0, max_value=2.0), min_size=1, max_size=30)
shortfall_ratios = st.lists(st.floats(min_value=0.0, max_value=0.9), min_size=1, max_size=30)
surplus_ratios = st.lists(st.floats(min_value=1.1, max_value=2.0), min_size=1, max_size=30)
positive_drift = st.floats(min_value=1e-3, max_value=1e3)


@given(ratios=ratio_lists, drift=coord)
def test_dose_drift_has_one_value_per_day(ratios, drift):
    assert len(dose_drift(ratios, drift)) == len(ratios)


@given(ratios=ratio_lists, drift=coord)
def test_dose_drift_starts_with_the_first_day_shortfall(ratios, drift):
    assert dose_drift(ratios, drift)[0] == pytest.approx(drift * (1.0 - ratios[0]))


@given(ratios=shortfall_ratios, drift=positive_drift)
def test_a_shortfall_makes_the_drift_grow_monotonically(ratios, drift):
    out = dose_drift(ratios, drift)
    assert np.all(np.diff(out) >= 0.0)
    assert out[-1] > 0.0


@given(ratios=surplus_ratios, drift=positive_drift)
def test_a_surplus_makes_the_drift_negative(ratios, drift):
    out = dose_drift(ratios, drift)
    assert np.all(np.diff(out) <= 0.0)
    assert out[-1] < 0.0


@given(ratios=ratio_lists, drift=coord)
def test_dose_drift_is_linear_in_the_drift_rate(ratios, drift):
    assert np.allclose(dose_drift(ratios, 2.0 * drift), 2.0 * dose_drift(ratios, drift))
