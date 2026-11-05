from pathlib import Path

import numpy as np
import pytest
from spine_physiology.config import load_config
from spine_physiology.curves import PiecewiseLinear, dose_drift


def test_interpolates_and_holds_ends():
    c = PiecewiseLinear([(0.0, 0.0), (10.0, 5.0), (20.0, 5.0), (30.0, 0.0)])
    assert c(5.0) == pytest.approx(2.5)
    assert c(10.0) == 5.0
    assert c(25.0) == pytest.approx(2.5)
    assert c(-100.0) == 0.0
    assert c(100.0) == 0.0


def test_values_matches_scalar_and_shape():
    c = PiecewiseLinear([(0.0, 1.0), (4.0, 3.0)])
    xs = np.array([[-1.0, 0.0, 2.0], [4.0, 5.0, 1.0]])
    out = c.values(xs)
    assert out.shape == xs.shape and out.dtype == np.float64
    assert np.allclose(out, [[c(x) for x in row] for row in xs])


def test_single_point_is_constant():
    c = PiecewiseLinear([(3.0, 7.0)])
    assert c(-5.0) == c(3.0) == c(9.0) == 7.0


def test_rejects_unsorted_or_empty():
    with pytest.raises(ValueError):
        PiecewiseLinear([(1.0, 0.0), (1.0, 1.0)])
    with pytest.raises(ValueError):
        PiecewiseLinear([])


def test_from_curve():
    cfg = load_config(Path(__file__).parents[1] / "configs" / "example-mission.yaml")
    c = PiecewiseLinear.from_curve(cfg.curves.spine_elongation)
    assert c(0) == 0.0 and c(3) == 8.0 and c(1.5) == pytest.approx(4.0)


def test_dose_drift_cumulative():
    out = dose_drift([1.0, 0.5, 0.0, 1.5], 2.0)
    assert np.allclose(out, [0.0, 1.0, 3.0, 2.0])
    assert dose_drift([], 1.0).shape == (0,)


def test_empty_and_non_increasing_points_are_rejected_with_a_clear_message():
    with pytest.raises(ValueError, match=r"^PiecewiseLinear needs at least one point$"):
        PiecewiseLinear([])
    with pytest.raises(ValueError, match=r"^points must be strictly increasing in x: \[0\.0, 5\.0, 5\.0\]$"):
        PiecewiseLinear([(0.0, 1.0), (5.0, 2.0), (5.0, 3.0)])


def test_dose_drift_is_float64_whatever_the_input_precision():
    out = dose_drift(np.array([0.5, 0.25], dtype=np.float32), 0.1)
    assert out.dtype == np.float64
    assert out == pytest.approx([0.05, 0.05 + 0.075])
