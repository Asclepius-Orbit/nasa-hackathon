from datetime import UTC, datetime
from itertools import permutations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import FeatureSet
from spine_contracts.models.domain.feature_set_schema import Reason
from spine_engine.interfaces import Deviation
from spine_engine.pipeline import _completeness

pytestmark = pytest.mark.property

IDS = ["emg.latissimus_dorsi.left.rms_pct_mvc", "imu.t9.sagittal.rom_deg", "stretch.full_spine.peak_elongation_mm", "exercise.rep_count"]
NOW = datetime(2027, 2, 9, 9, 0, tzinfo=UTC)


def feature_set(computed: list[str], skipped: list[tuple[str, str]]) -> FeatureSet:
    return FeatureSet.model_validate(
        {
            "schema_version": "1.0.0",
            "astronaut_id": "AST-01",
            "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a03",
            "protocol_id": "lat_pulldown_standing_v1",
            "gravity_environment": "microgravity",
            "source": "simulator",
            "computed_at": NOW.isoformat(),
            "engine_version": "0.1.0",
            "window": {"start": NOW.isoformat(), "end": NOW.isoformat()},
            "features": [
                {"feature_id": fid, "value": 1.0, "unit": "%", "n": 1, "quality": {"score": 1.0, "flags": [], "assessed_by": "engine"}} for fid in computed
            ],
            "skipped": [{"feature_id": fid, "reason": reason} for fid, reason in skipped],
        }
    )


@st.composite
def sibling_sets(draw: st.DrawFn) -> list[FeatureSet]:
    reasons = st.sampled_from([r.value for r in Reason])
    sets = []
    for _ in range(draw(st.integers(min_value=1, max_value=4))):
        computed = draw(st.lists(st.sampled_from(IDS), unique=True))
        skipped = [(fid, draw(reasons)) for fid in draw(st.lists(st.sampled_from(IDS), unique=True)) if fid not in computed]
        sets.append(feature_set(computed, skipped))
    return sets


def deviations_for(ids: list[str]) -> list[Deviation]:
    return [Deviation(feature_id=fid, value=0.0, score=0.0, z=0.0, sustained=False, n_points=3) for fid in ids]


@given(sets=sibling_sets(), compared=st.lists(st.sampled_from(IDS), unique=True))
def test_a_feature_computed_by_any_set_is_never_missing_because_of_a_skip(sets, compared):
    computed = {f.feature_id for fs in sets for f in fs.features}
    compared = [fid for fid in compared if fid in computed]

    completeness = _completeness(sets, deviations_for(compared))

    skip_missing = {m.what for m in completeness.missing if m.reason != "no_baseline"}
    assert skip_missing.isdisjoint(computed)


@given(sets=sibling_sets())
def test_the_missing_reason_does_not_depend_on_the_order_of_the_sets(sets):
    expected = sorted(_completeness(sets, []).missing, key=lambda m: m.what)

    for ordering in permutations(sets):
        assert sorted(_completeness(list(ordering), []).missing, key=lambda m: m.what) == expected
