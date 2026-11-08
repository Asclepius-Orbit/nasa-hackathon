import math

import pytest
from spine_contracts import AssessmentDomain
from spine_engine.interfaces import Deviation
from spine_engine.risk import DeviationRiskModel

STRETCH = "stretch.lumbar.peak_elongation_mm"
EMG = "emg.latissimus_dorsi.left.rms_pct_mvc"
IMU = "imu.l3.sagittal.rom_deg"
WEIGHTS = {
    AssessmentDomain.SPINE_LENGTH: 1.0,
    AssessmentDomain.MUSCLE: 0.5,
    AssessmentDomain.KINEMATICS: 1.0,
    AssessmentDomain.SYMPTOMS: 1.0,
}


def model(persistence_points: int = 4, saturation: float = 2.0) -> DeviationRiskModel:
    return DeviationRiskModel(WEIGHTS, persistence_points=persistence_points, saturation=saturation)


def dev(feature_id: str = STRETCH, z: float | None = 2.0, sustained: bool = True, n_points: int = 4) -> Deviation:
    return Deviation(feature_id=feature_id, value=7.5, score=z or 0.0, z=z, sustained=sustained, n_points=n_points)


def test_no_deviations_means_no_risk():
    assert model().score([], 1.0).score == 0.0


def test_a_sustained_deviation_saturates_its_weighted_z():
    # 1 - exp(-|z| * w * p / s) = 1 - exp(-2 * 1 * 1 / 2)
    assert model().score([dev()], 1.0).score == pytest.approx(1 - math.exp(-1.0))


def test_isolated_or_unscored_deviations_add_no_risk():
    result = model().score([dev(sustained=False), dev(z=None)], 1.0)

    assert result.score == 0.0
    assert result.drivers == []


def test_domain_weight_scales_the_contribution():
    # muscle weighs half: 1 - exp(-2 * 0.5 / 2)
    assert model().score([dev(EMG)], 1.0).score == pytest.approx(1 - math.exp(-0.5))


def test_features_without_a_domain_add_no_risk():
    assert model().score([dev("exercise.rep_count")], 1.0).score == 0.0


def test_persistence_grows_with_the_points_up_to_the_configured_count():
    one, two, four, eight = (model().score([dev(n_points=n)], 1.0).score for n in (1, 2, 4, 8))

    assert one == pytest.approx(1 - math.exp(-2 * 0.25 / 2))
    assert two == pytest.approx(1 - math.exp(-2 * 0.5 / 2))
    assert four == eight == pytest.approx(1 - math.exp(-1.0))


def test_drivers_carry_signed_z_and_shares_ordered_by_contribution():
    deviations = [dev(EMG, z=-1.0), dev(STRETCH, z=-3.0), dev(IMU, z=1.0)]

    result = model().score(deviations, 1.0)

    assert [d.feature_id for d in result.drivers] == [STRETCH, IMU, EMG]
    assert [d.deviation_z for d in result.drivers] == [-3.0, 1.0, -1.0]
    assert all(d.value == 7.5 for d in result.drivers)
    # raw contributions 3, 1 and 0.5 (muscle weighs half) out of 4.5: each driver gets its share of the score
    assert [d.contribution for d in result.drivers] == pytest.approx([result.score * 3 / 4.5, result.score * 1 / 4.5, result.score * 0.5 / 4.5])
    assert sum(d.contribution for d in result.drivers) == pytest.approx(result.score)


def test_drivers_with_equal_contribution_are_ordered_by_feature():
    result = model().score([dev(STRETCH), dev(IMU)], 1.0)

    assert [d.feature_id for d in result.drivers] == [IMU, STRETCH]


def test_confidence_is_the_completeness():
    assert model().score([dev()], 0.8).confidence == 0.8
    assert model().score([], 0.0).confidence == 0.0


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ({"persistence_points": 0}, "persistence_points must be at least 1"),
        ({"saturation": 0.0}, "saturation must be positive"),
        ({"saturation": -1.0}, "saturation must be positive"),
    ],
)
def test_rejects_invalid_parameters(bad, message):
    with pytest.raises(ValueError, match=f"^{message}$"):
        DeviationRiskModel(WEIGHTS, **{"persistence_points": 4, "saturation": 2.0, **bad})


def test_rejects_negative_domain_weights():
    with pytest.raises(ValueError, match="^domain weights must not be negative$"):
        DeviationRiskModel({**WEIGHTS, AssessmentDomain.MUSCLE: -0.1}, persistence_points=4, saturation=2.0)


def test_a_domain_left_out_of_the_weights_adds_no_risk():
    partial = DeviationRiskModel({AssessmentDomain.MUSCLE: 1.0}, persistence_points=4, saturation=2.0)

    assert partial.score([dev(STRETCH)], 1.0).score == 0.0


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_rejects_a_non_finite_saturation(value):
    with pytest.raises(ValueError, match="^saturation must be finite$"):
        DeviationRiskModel(WEIGHTS, persistence_points=4, saturation=value)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_rejects_non_finite_domain_weights(value):
    with pytest.raises(ValueError, match="^domain weights must be finite$"):
        DeviationRiskModel({**WEIGHTS, AssessmentDomain.MUSCLE: value}, persistence_points=4, saturation=2.0)
