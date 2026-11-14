import json
from datetime import timedelta
from pathlib import Path

import pytest
from spine_contracts import AssessmentDomain, AssessRequest, GravityEnvironment, dump, validation
from spine_engine import AssessmentPipeline, NullRiskModel, NullTriagePolicy
from spine_engine.deviation.detectors import AllAgreeVote, CompositeDetector, CusumDetector, KalmanDetector
from spine_engine.deviation.scoring import RobustZScorer
from spine_engine.deviation.strategy import StatisticalBaselineStrategy
from spine_engine.factory import default_baseline_strategy, default_pipeline
from spine_engine.interfaces import ChangeSignal
from spine_engine.settings import EngineSettings

EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "assess-request.json"
ROM = "imu.t9.sagittal.rom_deg"


def request() -> AssessRequest:
    return AssessRequest.model_validate(json.loads(EXAMPLE.read_text(encoding="utf-8")))


def by_feature(deviations):
    return {d.feature_id: d for d in deviations}


def with_history(req: AssessRequest, values: list[float]) -> list:
    """The in-flight feature set repeated once a day with the given ROM values, oldest first."""
    template = req.feature_sets[0]
    sets = []
    for day, value in enumerate(values):
        feature_set = template.model_copy(deep=True)
        feature_set.computed_at = template.computed_at + timedelta(days=day - len(values))
        feature_set.session_id = None
        for feature in feature_set.features:
            if feature.feature_id == ROM:
                feature.value = value
        sets.append(feature_set)
    return sets


def test_deviation_is_the_robust_z_of_the_latest_value_against_the_earth_entry():
    req = request()
    entry = next(e for e in req.baseline.entries if e.feature_id == ROM)

    result = by_feature(default_baseline_strategy(EngineSettings()).deviations(req.baseline, req.feature_sets))

    expected = RobustZScorer().score(31.0, entry)
    assert result[ROM].z == expected
    assert result[ROM].score == expected
    assert result[ROM].reference_gravity == GravityEnvironment.EARTH_1G
    assert result[ROM].protocol_id == req.feature_sets[0].protocol_id
    assert "exercise.rep_count" not in result


def test_same_gravity_entry_is_preferred_over_earth():
    req = request()
    microgravity = req.baseline.entries[2].model_copy(update={"gravity_environment": GravityEnvironment.MICROGRAVITY, "median": 31.0})
    req.baseline.entries.append(microgravity)

    result = by_feature(default_baseline_strategy(EngineSettings()).deviations(req.baseline, req.feature_sets))

    assert result[ROM].reference_gravity == GravityEnvironment.MICROGRAVITY
    assert result[ROM].z == 0.0


@pytest.mark.parametrize(("name", "protocol", "value"), [
    ("straight_resistance_ohm", "spine_resistance_straight_v1", 4500.0),
    ("max_flexion_resistance_ohm", "spine_resistance_max_flexion_v1", 5650.0),
])
def test_posture_resistance_always_compares_to_earth(name, protocol, value):
    req = request()
    feature_id = f"stretch.full_spine.{name}"
    earth = req.baseline.entries[0].model_copy(update={
        "feature_id": feature_id, "protocol_id": protocol, "gravity_environment": GravityEnvironment.EARTH_1G, "median": value,
    })
    space = earth.model_copy(update={"gravity_environment": GravityEnvironment.MICROGRAVITY, "median": value + 100})
    req.baseline.entries = [earth, space]
    feature_set = req.feature_sets[0]
    feature_set.protocol_id = protocol
    feature_set.features = [feature_set.features[0].model_copy(update={"feature_id": feature_id, "value": value + 100})]
    result = by_feature(default_baseline_strategy(EngineSettings()).deviations(req.baseline, [feature_set]))
    assert result[feature_id].reference_gravity == GravityEnvironment.EARTH_1G
    assert result[feature_id].z == RobustZScorer().score(value + 100, earth)
    req.baseline.entries = [space]
    assert feature_id not in by_feature(default_baseline_strategy(EngineSettings()).deviations(req.baseline, [feature_set]))


def test_a_sustained_shift_in_the_history_is_flagged_but_a_spike_is_not():
    req = request()
    strategy = default_baseline_strategy(EngineSettings())

    shifted = by_feature(strategy.deviations(req.baseline, with_history(req, [28.2, 28.0, 28.3, 31.0, 31.2, 31.1, 31.3, 31.0])))
    spike = by_feature(strategy.deviations(req.baseline, with_history(req, [28.2, 28.0, 28.3, 28.1, 28.2, 40.0, 28.1, 28.2])))

    assert shifted[ROM].sustained
    assert shifted[ROM].direction == "up"
    assert shifted[ROM].n_points == 8
    assert not spike[ROM].sustained


def test_history_of_another_protocol_or_gravity_does_not_feed_the_detector():
    req = request()
    history = with_history(req, [31.0] * 6)
    for feature_set in history[:-1]:
        feature_set.gravity_environment = GravityEnvironment.EARTH_1G

    result = by_feature(default_baseline_strategy(EngineSettings()).deviations(req.baseline, history))

    assert result[ROM].n_points == 1
    assert not result[ROM].sustained


def test_scorer_and_detector_are_injected():
    class FixedScorer:
        def score(self, value, entry):
            return 42.0

    class AlwaysChanging:
        def detect(self, series):
            return ChangeSignal(sustained=True, direction="down")

    req = request()

    result = by_feature(StatisticalBaselineStrategy(FixedScorer(), AlwaysChanging()).deviations(req.baseline, req.feature_sets))

    assert result[ROM].score == 42.0
    assert result[ROM].sustained
    assert result[ROM].direction == "down"


def test_plugs_into_the_pipeline_and_the_assessment_still_conforms_to_the_schema():
    pipeline = default_pipeline(EngineSettings())

    assessment = pipeline.evaluate(request())

    assert validation.errors("domain/assessment", dump(assessment)) == []
    assert assessment.completeness.score == pytest.approx(4 / 9)
    assert assessment.drivers == []


def test_strategy_swaps_into_an_existing_pipeline_without_changing_it():
    detector = CompositeDetector([CusumDetector(), KalmanDetector()], AllAgreeVote())
    pipeline = AssessmentPipeline(StatisticalBaselineStrategy(RobustZScorer(), detector), NullRiskModel(), NullTriagePolicy())

    assert pipeline.evaluate(request()).astronaut_id == "AST-01"


def test_default_pipeline_scores_a_sustained_shift_with_the_configured_risk_model():
    req = request()
    req.feature_sets = with_history(req, [28.2, 28.0, 28.3, 25.0, 25.2, 25.1, 25.3, 25.0])

    assessment = default_pipeline(EngineSettings()).evaluate(req)

    assert 0.0 < assessment.risk_score < 1.0
    assert assessment.confidence == assessment.completeness.score
    drivers = {d.feature_id: d for d in assessment.drivers}
    assert drivers[ROM].deviation_z is not None and drivers[ROM].deviation_z < 0
    assert sum(d.contribution for d in assessment.drivers) == pytest.approx(assessment.risk_score)
    assert validation.errors("domain/assessment", dump(assessment)) == []


def test_risk_parameters_come_from_the_settings():
    req = request()
    req.feature_sets = with_history(req, [28.2, 28.0, 28.3, 25.0, 25.2, 25.1, 25.3, 25.0])
    # Only the ROM series (kinematics) is weighed; the other in-flight features are left out.
    weights = {**dict.fromkeys(AssessmentDomain, 0.0), AssessmentDomain.KINEMATICS: 1.0}

    muted = default_pipeline(EngineSettings(risk_domain_weights=dict.fromkeys(AssessmentDomain, 0.0))).evaluate(req)
    soft = default_pipeline(EngineSettings(risk_domain_weights=weights, risk_saturation=1e6)).evaluate(req)
    sharp = default_pipeline(EngineSettings(risk_domain_weights=weights, risk_saturation=1e-3)).evaluate(req)
    patient = default_pipeline(EngineSettings(risk_domain_weights=weights, risk_saturation=1e6, risk_persistence_points=80)).evaluate(req)

    assert muted.risk_score == 0.0
    assert sharp.risk_score == 1.0
    assert [d.feature_id for d in soft.drivers] == [ROM]
    assert 0.0 < patient.risk_score < soft.risk_score < 1e-3


def test_partial_risk_weights_keep_the_other_domains_scoring():
    req = request()
    req.feature_sets = with_history(req, [28.2, 28.0, 28.3, 25.0, 25.2, 25.1, 25.3, 25.0])

    assessment = default_pipeline(EngineSettings(_env_file=None, risk_domain_weights={AssessmentDomain.MUSCLE: 2.0})).evaluate(req)

    assert ROM in {d.feature_id for d in assessment.drivers}
