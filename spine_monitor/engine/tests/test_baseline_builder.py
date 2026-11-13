import json
from pathlib import Path

import pytest
from spine_contracts import BaselineRequest, GravityEnvironment, dump, validation
from spine_engine.baseline.builder import StatisticalBaselineBuilder
from spine_engine.errors import InsufficientDataError

EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "baseline-request.json"
ROM = "imu.t9.sagittal.rom_deg"


def request() -> BaselineRequest:
    return BaselineRequest.model_validate(json.loads(EXAMPLE.read_text(encoding="utf-8")))


def two_session_request() -> BaselineRequest:
    """The example plus a second earth session with a slightly lower ROM."""
    req = request()
    second = req.feature_sets[0].model_copy(deep=True)
    second.session_id = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a04"
    for feature in second.features:
        if feature.feature_id == ROM:
            feature.value = 27.6
    req.feature_sets.append(second)
    return req


def entry_of(baseline, feature_id=ROM, gravity=GravityEnvironment.EARTH_1G):
    return next(e for e in baseline.entries if e.feature_id == feature_id and e.gravity_environment == gravity)


def test_example_request_builds_a_baseline_that_conforms_to_the_schema():
    baseline = StatisticalBaselineBuilder().build(request())

    assert validation.errors("domain/baseline", dump(baseline)) == []
    assert baseline.version == 1
    assert baseline.astronaut_id == "AST-01"
    assert baseline.built_from_session_ids == ["3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"]


def test_statistics_per_feature():
    entry = entry_of(StatisticalBaselineBuilder().build(two_session_request()))

    assert entry.n == 2
    assert entry.mean == pytest.approx(28.0)
    assert entry.median == pytest.approx(28.0)
    assert entry.std == pytest.approx(0.4)
    assert entry.mad == pytest.approx(0.4)
    assert (entry.min, entry.max) == (27.6, 28.4)


def test_baseline_includes_reports_in_session_window():
    from spine_contracts import AssessRequest

    assess = EXAMPLE.with_name("assess-request.json")
    report = AssessRequest.model_validate_json(assess.read_text(encoding="utf-8")).self_reports[0]
    req = request()
    report.timestamp = req.feature_sets[0].window.end
    req.self_reports = [report]
    entry = entry_of(StatisticalBaselineBuilder().build(req), "symptoms.pain_nrs_7d_mean")
    assert entry.mean == report.pain_nrs


def test_median_and_mad_resist_an_outlier_session():
    req = request()
    template = req.feature_sets[0]
    sets = []
    for i, value in enumerate([28.0, 28.2, 28.1, 28.3, 60.0]):
        feature_set = template.model_copy(deep=True)
        feature_set.session_id = f"3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f2a0{i}"
        for feature in feature_set.features:
            if feature.feature_id == ROM:
                feature.value = value
        sets.append(feature_set)
    req.feature_sets = sets

    entry = entry_of(StatisticalBaselineBuilder().build(req))

    assert entry.median == 28.2
    assert entry.mad == pytest.approx(0.1)
    assert entry.mean > 33


def test_entries_are_split_by_gravity_and_protocol():
    req = two_session_request()
    microgravity = req.feature_sets[0].model_copy(deep=True)
    microgravity.gravity_environment = GravityEnvironment.MICROGRAVITY
    other_protocol = req.feature_sets[0].model_copy(deep=True)
    other_protocol.protocol_id = "spine_length_rest_v1"
    req.feature_sets += [microgravity, other_protocol]

    baseline = StatisticalBaselineBuilder().build(req)

    keys = [(e.feature_id, e.protocol_id, e.gravity_environment) for e in baseline.entries]
    assert len(keys) == len(set(keys))
    assert entry_of(baseline, gravity=GravityEnvironment.MICROGRAVITY).n == 1
    assert entry_of(baseline).n == 2


def test_a_feature_seen_in_one_session_still_gets_an_entry_with_n_one():
    req = request()
    req.feature_sets = req.feature_sets[:1]

    entry = entry_of(StatisticalBaselineBuilder().build(req))

    assert (entry.n, entry.std, entry.mad) == (1, 0.0, 0.0)


def test_version_follows_the_previous_baseline():
    req = request()
    req.previous_baseline_version = 3

    assert StatisticalBaselineBuilder().build(req).version == 4


def test_skipped_features_have_no_entry():
    baseline = StatisticalBaselineBuilder().build(request())

    assert not any(e.feature_id == "emg.latissimus_dorsi.right.rms_pct_mvc" for e in baseline.entries)


def test_feature_sets_without_gravity_cannot_make_a_baseline():
    req = request()
    for feature_set in req.feature_sets:
        feature_set.gravity_environment = None

    with pytest.raises(InsufficientDataError):
        StatisticalBaselineBuilder().build(req)


def test_a_baseline_needs_at_least_one_session_id():
    req = request()
    for feature_set in req.feature_sets:
        feature_set.session_id = None

    with pytest.raises(InsufficientDataError):
        StatisticalBaselineBuilder().build(req)
