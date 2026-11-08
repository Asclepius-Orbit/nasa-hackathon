"""Lumbar length helpers of the UI views: baseline lookup, delta, own pre-flight range and latest measure."""

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from monitor.views.lumbar import (
    ELONGATION_FEATURE,
    baseline_entry,
    first_lumbar_entry,
    latest_elongation,
    lumbar_delta_mm,
    preflight_range_mm,
)

PROTOCOL, GRAVITY = "lat_pulldown_standing_v1", "earth_1g"


def entry(mean: float | None = 6.5, lo: float | None = 5.0, hi: float | None = 8.0, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"feature_id": ELONGATION_FEATURE, "protocol_id": PROTOCOL, "gravity_environment": GRAVITY, "n": 3}
    for key, value in (("mean", mean), ("min", lo), ("max", hi)):
        if value is not None:
            body[key] = value
    return {**body, **over}


def feature_set(value: float | None, start: str, *, protocol: str = PROTOCOL, gravity: str = GRAVITY, feature: str = ELONGATION_FEATURE) -> dict[str, Any]:
    features = [] if value is None else [{"feature_id": feature, "value": value, "unit": "mm", "quality": {"score": 1.0, "flags": []}}]
    return {
        "protocol_id": protocol,
        "gravity_environment": gravity,
        "computed_at": "2030-01-01T00:00:00Z",
        "window": {"start": start, "end": start},
        "features": features,
    }


def test_the_feature_id_is_the_lumbar_elongation():
    assert ELONGATION_FEATURE == "stretch.lumbar.peak_elongation_mm"


# --- baseline_entry ------------------------------------------------------------------------------


def test_baseline_entry_finds_the_lumbar_entry_of_the_protocol_and_gravity():
    wanted = entry(mean=1.0)
    baseline = {
        "entries": [
            entry(mean=2.0, feature_id="stretch.lumbar.elongation_mm"),
            entry(mean=3.0, protocol_id="other_v1"),
            entry(mean=4.0, gravity_environment="microgravity"),
            wanted,
        ]
    }

    assert baseline_entry(baseline, PROTOCOL, GRAVITY) is wanted


@pytest.mark.parametrize(("protocol", "gravity"), [("other_v1", GRAVITY), (PROTOCOL, "microgravity")])
def test_baseline_entry_is_none_when_protocol_or_gravity_differ(protocol, gravity):
    assert baseline_entry({"entries": [entry()]}, protocol, gravity) is None


def test_baseline_entry_is_none_without_baseline_or_entries():
    assert baseline_entry(None, PROTOCOL, GRAVITY) is None
    assert baseline_entry({"entries": []}, PROTOCOL, GRAVITY) is None


def test_first_lumbar_entry_skips_other_features_and_is_none_without_one():
    first = entry(mean=1.0)
    baseline = {"entries": [entry(feature_id="emg.x"), first, entry(mean=9.0, protocol_id="other_v1")]}

    assert first_lumbar_entry(baseline) is first
    assert first_lumbar_entry({"entries": [entry(feature_id="emg.x")]}) is None
    assert first_lumbar_entry(None) is None


# --- lumbar_delta_mm -----------------------------------------------------------------------------


def test_delta_is_value_minus_mean():
    assert lumbar_delta_mm(8.5, entry(mean=6.5)) == 2.0
    assert lumbar_delta_mm(4.0, entry(mean=6.5)) == -2.5


def test_delta_of_a_genuine_zero_is_zero():
    assert lumbar_delta_mm(6.5, entry(mean=6.5)) == 0.0
    assert lumbar_delta_mm(0.0, entry(mean=0.0)) == 0.0


@pytest.mark.parametrize(("value", "the_entry"), [(None, entry()), (6.0, None), (None, None), (6.0, entry(mean=None))])
def test_delta_is_null_when_anything_is_missing_never_zero(value, the_entry):
    assert lumbar_delta_mm(value, the_entry) is None


# --- preflight_range_mm --------------------------------------------------------------------------


def test_range_is_min_and_max_relative_to_the_mean():
    assert preflight_range_mm(entry(mean=6.5, lo=5.0, hi=8.0)) == {"lo": -1.5, "hi": 1.5}
    assert preflight_range_mm(entry(mean=6.0, lo=4.0, hi=9.0)) == {"lo": -2.0, "hi": 3.0}


@pytest.mark.parametrize("the_entry", [None, entry(mean=None), entry(lo=None), entry(hi=None)])
def test_range_is_null_without_entry_or_any_of_mean_min_max(the_entry):
    assert preflight_range_mm(the_entry) is None


# --- latest_elongation ---------------------------------------------------------------------------


def test_latest_elongation_returns_value_protocol_and_gravity_of_the_most_recent_measure():
    sets = [
        feature_set(5.0, "2030-01-01T10:00:00Z"),
        feature_set(7.0, "2030-01-03T10:00:00Z", protocol="other_v1", gravity="microgravity"),
        feature_set(6.0, "2030-01-02T10:00:00Z"),
    ]

    assert latest_elongation(sets) == (7.0, "other_v1", "microgravity")


def test_latest_elongation_ignores_sets_without_the_feature_and_other_features():
    sets = [
        feature_set(5.0, "2030-01-01T10:00:00Z"),
        feature_set(None, "2030-01-02T10:00:00Z"),
        feature_set(9.0, "2030-01-03T10:00:00Z", feature="stretch.lumbar.elongation_mm"),
    ]

    assert latest_elongation(sets) == (5.0, PROTOCOL, GRAVITY)


def test_latest_elongation_is_none_without_measures():
    assert latest_elongation([]) is None
    assert latest_elongation([feature_set(None, "2030-01-01T10:00:00Z")]) is None


def test_latest_elongation_compares_instants_not_text_across_offsets():
    later_in_utc = feature_set(2.0, "2030-01-01T10:00:00+00:00")
    earlier_in_utc = feature_set(1.0, "2030-01-01T11:00:00+02:00")

    assert latest_elongation([later_in_utc, earlier_in_utc]) == (2.0, PROTOCOL, GRAVITY)


def test_latest_elongation_falls_back_to_computed_at_without_window():
    old = {**feature_set(1.0, "2030-01-01T10:00:00Z"), "computed_at": "2030-01-01T10:00:00Z"}
    new = {**feature_set(2.0, "2030-01-01T10:00:00Z"), "computed_at": "2030-01-02T10:00:00Z"}
    del old["window"], new["window"]

    assert latest_elongation([new, old]) == (2.0, PROTOCOL, GRAVITY)


def test_latest_elongation_keeps_the_last_of_equal_instants():
    sets = [feature_set(1.0, "2030-01-01T10:00:00Z"), feature_set(2.0, "2030-01-01T10:00:00Z")]

    assert latest_elongation(sets) == (2.0, PROTOCOL, GRAVITY)


# --- properties ----------------------------------------------------------------------------------

finite = st.floats(min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False)


@pytest.mark.property
@given(value=finite, mean=finite)
def test_property_delta_added_to_the_mean_gives_back_the_value(value, mean):
    delta = lumbar_delta_mm(value, entry(mean=mean))

    assert delta is not None
    assert delta + mean == pytest.approx(value, abs=1e-9)


@pytest.mark.property
@given(mean=finite, below=st.floats(0, 100), above=st.floats(0, 100))
def test_property_range_always_brackets_zero_and_spans_min_to_max(mean, below, above):
    result = preflight_range_mm(entry(mean=mean, lo=mean - below, hi=mean + above))

    assert result is not None
    assert result["lo"] <= 0 <= result["hi"]
    assert result["hi"] - result["lo"] == pytest.approx(below + above, abs=1e-6)


@pytest.mark.property
@given(value=st.none() | finite, mean=st.none() | finite)
def test_property_delta_is_null_exactly_when_something_is_missing(value, mean):
    assert (lumbar_delta_mm(value, entry(mean=mean)) is None) == (value is None or mean is None)
