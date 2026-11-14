from datetime import UTC, datetime, timedelta

import pytest
from spine_contracts import Completeness, FeatureSet, Level
from spine_engine.interfaces import RiskResult, TriageContext
from spine_engine.measurements import EMG, IMU, STRETCH, UNITS, classify, measurements
from spine_engine.triage import DeclarativeTriagePolicy, PolicyConfig

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(("key", "value", "level"), [
    (STRETCH, 3.99, Level.GREEN), (STRETCH, 4, Level.AMBER), (STRETCH, 7.49, Level.AMBER), (STRETCH, 7.5, Level.RED),
    (IMU, 25, Level.GREEN), (IMU, 34.99, Level.GREEN), (IMU, 35, Level.AMBER), (IMU, 41.99, Level.AMBER), (IMU, 42, Level.RED),
    (EMG, 1023, Level.GREEN), (EMG, 922, Level.GREEN), (EMG, 921, Level.AMBER), (EMG, 819, Level.AMBER),
    (EMG, 818, Level.AMBER), (EMG, 717, Level.AMBER), (EMG, 716, Level.RED), (EMG, 614, Level.RED), (EMG, 613, Level.RED),
])
def test_report_limits(key, value, level):
    assert classify(key, value)[0] == level


def feature_set(key, value, *, unit=None, seconds=5, end=NOW):
    return FeatureSet.model_validate({"schema_version": "1.0.0", "astronaut_id": "AST-01", "session_id": None,
        "computed_at": NOW, "window": {"start": end - timedelta(seconds=seconds), "end": end},
        "features": [{"feature_id": key, "value": value, "unit": unit or UNITS[key],
                      "quality": {"score": 1, "flags": [], "assessed_by": "engine"}}]})


def test_invalid_and_stale_inputs_do_not_classify():
    assert not measurements([feature_set(IMU, 24.99)], NOW)
    assert not measurements([feature_set(EMG, -1)], NOW)
    assert not measurements([feature_set(STRETCH, 4, unit="ohm")], NOW)
    assert not measurements([feature_set(STRETCH, 4, seconds=4)], NOW)
    assert not measurements([feature_set(STRETCH, 4, end=NOW - timedelta(seconds=6))], NOW)


def test_measured_rule_overrides_without_deviation_and_records_evidence():
    fs = feature_set(STRETCH, 7.5)
    context = TriageContext(RiskResult(0), [], Completeness(score=1, missing=[]), [], NOW, [fs])
    decision = DeclarativeTriagePolicy(PolicyConfig()).decide(context)
    assert decision.level == Level.RED
    evidence = decision.fired_rules[0].evidence
    assert (evidence.value, evidence.unit, evidence.threshold, evidence.source) == (7.5, "%", 7.5, "Polfer and Aguado device report")


def test_missing_measurements_reduce_completeness():
    from spine_engine.pipeline import _completeness
    result = _completeness([], [], {})
    assert result.score == 0
    assert {item.what for item in result.missing} == {STRETCH, IMU, EMG}

