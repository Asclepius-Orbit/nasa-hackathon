"""Paso puro de `/simulate`: aplicar los overrides a la base antes de reevaluar."""

import json
import math
from datetime import timedelta
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from spine_contracts import AssessRequest, SimulateRequest
from spine_contracts.models.engine.simulate_request_schema import Mode, Override
from spine_engine.errors import InvalidOverrideError, UnknownFeatureError
from spine_engine.simulation import apply_overrides

EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "simulate-request.json"
REQUEST = SimulateRequest.model_validate(json.loads(EXAMPLE.read_text(encoding="utf-8")))
PEAK = "stretch.lumbar.peak_elongation_mm"


def _with_history() -> AssessRequest:
    """La base del ejemplo con un conjunto anterior (mismas features, otros valores) delante."""
    base = REQUEST.base.model_copy(deep=True)
    older = base.feature_sets[0].model_copy(deep=True)
    older.computed_at = older.computed_at - timedelta(days=1)
    for feature in older.features:
        feature.value = feature.value + 1000.0
    base.feature_sets = [base.feature_sets[0], older]  # el más reciente va primero a propósito
    return base


BASE = _with_history()
IDS = [f.feature_id for f in BASE.feature_sets[0].features]
values = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
overrides = st.lists(
    st.builds(Override, feature_id=st.sampled_from(IDS), mode=st.sampled_from(list(Mode)), value=values),
    min_size=1,
    unique_by=lambda o: o.feature_id,
)


def _latest(base: AssessRequest, feature_id: str) -> float:
    latest = max(base.feature_sets, key=lambda s: s.computed_at)
    return next(f.value for f in latest.features if f.feature_id == feature_id)


def test_example_adds_to_the_latest_value():
    result = apply_overrides(REQUEST.base, REQUEST.overrides)

    assert _latest(result, PEAK) == pytest.approx(_latest(REQUEST.base, PEAK) + 5)


@pytest.mark.property
@given(overrides=overrides)
@example(overrides=[Override(feature_id=PEAK, mode=Mode.SCALE, value=0.0)])
@example(overrides=[Override(feature_id=PEAK, mode=Mode.ADD, value=-1e6)])
def test_each_mode_does_its_arithmetic_on_the_latest_value(overrides):
    result = apply_overrides(BASE, overrides)

    for o in overrides:
        before = _latest(BASE, o.feature_id)
        expected = {Mode.SET: o.value, Mode.ADD: before + o.value, Mode.SCALE: before * o.value}[o.mode]
        assert _latest(result, o.feature_id) == pytest.approx(expected)


@pytest.mark.property
@given(overrides=overrides)
def test_only_the_latest_value_of_each_feature_changes(overrides):
    result = apply_overrides(BASE, overrides)
    touched = {o.feature_id for o in overrides}

    for before, after in zip(BASE.feature_sets, result.feature_sets, strict=True):
        is_latest = before.computed_at == max(s.computed_at for s in BASE.feature_sets)
        for old, new in zip(before.features, after.features, strict=True):
            assert (new.feature_id, new.unit, new.quality) == (old.feature_id, old.unit, old.quality)
            if not (is_latest and old.feature_id in touched):
                assert new.value == old.value


@pytest.mark.property
@given(overrides=overrides)
def test_the_request_is_never_mutated(overrides):
    snapshot = BASE.model_dump_json()

    result = apply_overrides(BASE, overrides)

    assert BASE.model_dump_json() == snapshot
    assert result is not BASE


@pytest.mark.property
def test_empty_overrides_change_nothing():
    assert apply_overrides(BASE, []) == BASE


def test_unknown_feature_id_is_rejected():
    with pytest.raises(UnknownFeatureError):
        apply_overrides(BASE, [Override(feature_id="stretch.not.in_catalog", mode=Mode.SET, value=1)])


def test_feature_without_a_value_in_the_base_is_rejected():
    absent = "emg.latissimus_dorsi.left.rms_pct_mvc"
    base = BASE.model_copy(deep=True)
    for fs in base.feature_sets:
        fs.features = [f for f in fs.features if f.feature_id != absent]

    with pytest.raises(UnknownFeatureError):
        apply_overrides(base, [Override(feature_id=absent, mode=Mode.SET, value=1)])


@pytest.mark.parametrize("mode", [Mode.ADD, Mode.SCALE])
def test_non_finite_result_is_rejected(mode):
    huge = Override(feature_id=PEAK, mode=mode, value=1.7e308)

    with pytest.raises(InvalidOverrideError):
        apply_overrides(BASE, [huge, huge])  # se componen: el segundo desborda a infinito


def test_non_finite_set_is_rejected():
    with pytest.raises(InvalidOverrideError):
        apply_overrides(BASE, [Override.model_construct(feature_id=PEAK, mode=Mode.SET, value=math.nan)])


def test_unknown_feature_message_names_the_feature_and_the_catalog():
    with pytest.raises(UnknownFeatureError) as caught:
        apply_overrides(BASE, [Override(feature_id="stretch.not.in_catalog", mode=Mode.SET, value=1)])

    assert str(caught.value) == "The feature 'stretch.not.in_catalog' is not in the catalog."


def test_feature_without_a_value_message_names_the_feature():
    absent = "emg.latissimus_dorsi.left.rms_pct_mvc"
    base = BASE.model_copy(deep=True)
    for fs in base.feature_sets:
        fs.features = [f for f in fs.features if f.feature_id != absent]

    with pytest.raises(UnknownFeatureError) as caught:
        apply_overrides(base, [Override(feature_id=absent, mode=Mode.SET, value=1)])

    assert str(caught.value) == f"The feature '{absent}' has no value in the base to override."


def test_non_finite_message_names_the_feature():
    with pytest.raises(InvalidOverrideError) as caught:
        apply_overrides(BASE, [Override.model_construct(feature_id=PEAK, mode=Mode.SET, value=math.inf)])

    assert str(caught.value) == f"The override on '{PEAK}' does not give a finite value."
