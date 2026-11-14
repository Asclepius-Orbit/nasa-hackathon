import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from spine_contracts import AssessmentDomain
from spine_engine.interfaces import Deviation
from spine_engine.risk import DeviationRiskModel

pytestmark = pytest.mark.property

FEATURES = ["stretch.full_spine.peak_elongation_mm", "emg.latissimus_dorsi.left.rms_pct_mvc", "imu.t9.sagittal.rom_deg", "symptoms.pain.lumbar"]
z_values = st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False)
unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
weights = st.fixed_dictionaries({d: st.floats(min_value=0.0, max_value=10.0, allow_nan=False) for d in AssessmentDomain})


@st.composite
def models(draw: st.DrawFn) -> DeviationRiskModel:
    return DeviationRiskModel(
        draw(weights),
        persistence_points=draw(st.integers(min_value=1, max_value=20)),
        saturation=draw(st.floats(min_value=0.1, max_value=100.0, allow_nan=False)),
    )


@st.composite
def deviation_lists(draw: st.DrawFn) -> list[Deviation]:
    return [
        Deviation(
            feature_id=feature_id,
            value=0.0,
            score=z,
            z=z,
            sustained=draw(st.booleans()),
            n_points=draw(st.integers(min_value=1, max_value=30)),
        )
        for feature_id in draw(st.lists(st.sampled_from(FEATURES), unique=True))
        for z in [draw(z_values)]
    ]


def with_z(deviations: list[Deviation], z: list[float]) -> list[Deviation]:
    return [Deviation(d.feature_id, d.value, v, v, sustained=d.sustained, n_points=d.n_points) for d, v in zip(deviations, z, strict=True)]


@given(m=models(), deviations=deviation_lists(), completeness=unit)
def test_score_and_confidence_stay_in_the_unit_interval(m, deviations, completeness):
    result = m.score(deviations, completeness)

    assert 0.0 <= result.score <= 1.0
    assert 0.0 <= result.confidence <= 1.0


@given(m=models(), deviations=deviation_lists(), growth=st.lists(st.floats(min_value=0.0, max_value=50.0, allow_nan=False), min_size=4, max_size=4))
@example(m=DeviationRiskModel(dict.fromkeys(AssessmentDomain, 1.0), persistence_points=1, saturation=1.0), deviations=[], growth=[0.0] * 4)
def test_more_deviation_never_lowers_the_risk(m, deviations, growth):
    # Push each |z| outward (keeping its sign): the risk can only stay or grow.
    larger = with_z(deviations, [d.z + g if d.z >= 0 else d.z - g for d, g in zip(deviations, growth, strict=False)])

    assert m.score(larger, 1.0).score >= m.score(deviations, 1.0).score


@given(m=models(), deviations=deviation_lists())
@example(m=DeviationRiskModel(dict.fromkeys(AssessmentDomain, 1.0), persistence_points=1, saturation=1.0), deviations=[])
def test_flipping_the_sign_of_z_does_not_change_the_risk(m, deviations):
    flipped = with_z(deviations, [-d.z for d in deviations])

    original, mirrored = m.score(deviations, 1.0), m.score(flipped, 1.0)

    assert mirrored.score == original.score
    assert [d.contribution for d in mirrored.drivers] == [d.contribution for d in original.drivers]
    assert [d.deviation_z for d in mirrored.drivers] == [-d.deviation_z for d in original.drivers]


@given(m=models(), deviations=deviation_lists(), a=unit, b=unit)
@example(m=DeviationRiskModel(dict.fromkeys(AssessmentDomain, 1.0), persistence_points=1, saturation=1.0), deviations=[], a=0.0, b=1.0)
def test_less_completeness_never_raises_the_confidence(m, deviations, a, b):
    low, high = sorted((a, b))

    assert m.score(deviations, low).confidence <= m.score(deviations, high).confidence


@given(m=models(), deviations=deviation_lists())
def test_drivers_are_ordered_and_their_shares_add_up_to_the_score(m, deviations):
    result = m.score(deviations, 1.0)
    contributions = [d.contribution for d in result.drivers]

    assert contributions == sorted(contributions, reverse=True)
    assert sum(contributions) == pytest.approx(result.score, abs=1e-9)
