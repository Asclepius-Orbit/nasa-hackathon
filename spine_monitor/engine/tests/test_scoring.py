import pytest
from spine_contracts import BaselineEntry, GravityEnvironment
from spine_engine.deviation.scoring import MAD_TO_SIGMA, RobustZScorer


def entry(median=40.0, mad=2.0, std=3.0) -> BaselineEntry:
    return BaselineEntry(
        feature_id="emg.latissimus_dorsi.left.rms_pct_mvc",
        gravity_environment=GravityEnvironment.EARTH_1G,
        n=5,
        mean=median,
        std=std,
        median=median,
        mad=mad,
    )


def test_z_is_robust_distance_to_the_median_in_mad_units():
    z = RobustZScorer().score(46.0, entry(median=40.0, mad=2.0))

    assert z == pytest.approx(6.0 / (MAD_TO_SIGMA * 2.0))


def test_z_is_negative_below_the_median():
    assert RobustZScorer().score(34.0, entry()) < 0


def test_falls_back_to_std_when_mad_is_zero():
    z = RobustZScorer().score(46.0, entry(median=40.0, mad=0.0, std=3.0))

    assert z == pytest.approx(2.0)


def test_falls_back_to_a_floor_proportional_to_the_median_when_there_is_no_spread():
    z = RobustZScorer(relative_floor=0.05).score(44.0, entry(median=40.0, mad=0.0, std=0.0))

    assert z == pytest.approx(4.0 / (0.05 * 40.0))


def test_zero_median_without_spread_still_divides_by_something():
    z = RobustZScorer().score(0.0, entry(median=0.0, mad=0.0, std=0.0))

    assert z == 0.0
