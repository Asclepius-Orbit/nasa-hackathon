import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from spine_contracts import (
    AssessmentDomain,
    AssessRequest,
    Completeness,
    Driver,
    Escalation,
    FiredRule,
    GravityEnvironment,
    Level,
    MissingItem,
    MissingReason,
    Urgency,
    dump,
    validation,
)
from spine_contracts.models.domain.feature_set_schema import Reason, SkippedItem
from spine_engine import (
    AssessmentPipeline,
    Deviation,
    NullBaselineStrategy,
    RiskResult,
    TriageDecision,
    stub_pipeline,
)
from spine_engine.interfaces import TriageContext

EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "assess-request.json"
INFLIGHT_SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a03"


def request() -> AssessRequest:
    return AssessRequest.model_validate(json.loads(EXAMPLE.read_text(encoding="utf-8")))


def test_stub_pipeline_is_green_but_says_it_knows_nothing():
    assessment = stub_pipeline().evaluate(request())

    assert assessment.astronaut_id == "AST-01"
    assert assessment.mission_day == 87
    assert assessment.as_of.isoformat() == "2027-02-09T09:00:00+00:00"
    assert assessment.level == Level.GREEN
    assert assessment.risk_score == 0.0
    assert assessment.confidence == 0.0
    assert assessment.drivers == []
    assert assessment.trend is None
    assert assessment.fired_rules == []
    assert assessment.recommendations == []
    assert not assessment.escalation.required


def test_stub_pipeline_output_conforms_to_the_json_schema():
    assert validation.errors("domain/assessment", dump(stub_pipeline().evaluate(request()))) == []


def test_domains_without_comparison_are_not_evaluated():
    domains = {d.domain: d for d in stub_pipeline().evaluate(request()).domains}

    assert set(domains) == set(AssessmentDomain)
    assert domains[AssessmentDomain.MUSCLE].evaluated
    assert domains[AssessmentDomain.SPINE_LENGTH].evaluated
    assert domains[AssessmentDomain.KINEMATICS].evaluated
    assert not domains[AssessmentDomain.SYMPTOMS].evaluated


def test_completeness_reports_what_could_not_be_compared():
    completeness = stub_pipeline().evaluate(request()).completeness

    # Four comparable sensor features out of five sensor and four symptom features.
    assert completeness.score == pytest.approx(4 / 9)
    assert MissingItem(what="exercise.rep_count", reason=MissingReason.NO_BASELINE) in completeness.missing


def test_skipped_features_count_as_missing():
    req = request()
    req.feature_sets[0].skipped = [SkippedItem(feature_id="emg.latissimus_dorsi.right.rms_pct_mvc", reason="stream_missing")]

    completeness = stub_pipeline().evaluate(req).completeness

    assert MissingItem(what="emg.latissimus_dorsi.right.rms_pct_mvc", reason=MissingReason.SENSOR_OFFLINE) in completeness.missing
    assert completeness.score < 0.8


@pytest.mark.parametrize("reason", [r.value for r in Reason])
def test_every_skip_reason_of_the_contract_maps_to_a_missing_reason(reason):
    # A new contract reason must never reach the pipeline as a KeyError.
    req = request()
    req.feature_sets[0].skipped = [SkippedItem(feature_id="emg.latissimus_dorsi.right.rms_pct_mvc", reason=reason)]

    completeness = stub_pipeline().evaluate(req).completeness

    assert any(item.what == "emg.latissimus_dorsi.right.rms_pct_mvc" for item in completeness.missing)


def test_calibration_expired_counts_as_missing_data():
    req = request()
    req.feature_sets[0].skipped = [SkippedItem(feature_id="emg.latissimus_dorsi.right.rms_pct_mvc", reason="calibration_expired")]

    completeness = stub_pipeline().evaluate(req).completeness

    assert MissingItem(what="emg.latissimus_dorsi.right.rms_pct_mvc", reason=MissingReason.NO_DATA) in completeness.missing


def test_insufficient_sample_rate_counts_as_missing_data():
    req = request()
    req.feature_sets[0].skipped = [SkippedItem(feature_id="imu.t9.sagittal.rom_deg", reason="insufficient_sample_rate")]

    completeness = stub_pipeline().evaluate(req).completeness

    assert MissingItem(what="imu.t9.sagittal.rom_deg", reason=MissingReason.NO_DATA) in completeness.missing


def test_null_skipped_in_a_feature_set_means_nothing_was_skipped():
    # The schema allows `skipped: null`; the completeness must read it as "none skipped".
    req = request()
    req.feature_sets[0].skipped = None

    completeness = stub_pipeline().evaluate(req).completeness

    assert completeness.score == pytest.approx(4 / 9)


def test_null_self_reports_mean_no_self_reports():
    # The schema allows `self_reports: null`; based_on must list none instead of failing.
    req = request()
    req.self_reports = None

    assert stub_pipeline().evaluate(req).based_on.self_report_ids == []


def test_assessment_uses_symptom_features_from_reports():
    req = request()
    assert req.self_reports
    report = req.self_reports[0]
    report.timestamp = req.as_of
    assessment = stub_pipeline().evaluate(req)
    assert any(m.what == "symptoms.pain_nrs_7d_mean" for m in assessment.completeness.missing)


def test_assessment_compares_symptoms_to_reference_baseline():
    from spine_contracts import BaselineRequest
    from spine_engine.baseline.builder import StatisticalBaselineBuilder

    req = request()
    report = req.self_reports[0]
    report.timestamp = req.as_of
    reference = req.feature_sets[0].model_copy(deep=True)
    reference.gravity_environment = GravityEnvironment.EARTH_1G
    reference.window = None
    reference.computed_at = req.as_of
    req.baseline = StatisticalBaselineBuilder().build(BaselineRequest(
        schema_version=req.schema_version, astronaut_id=req.astronaut_id, feature_sets=[reference], self_reports=[report]
    ))
    assert req.daily_context
    req.daily_context[0].date = req.as_of.date()
    assessment = stub_pipeline().evaluate(req)
    assert next(d for d in assessment.domains if d.domain == AssessmentDomain.SYMPTOMS).evaluated


def test_based_on_traces_the_inputs():
    based_on = stub_pipeline().evaluate(request()).based_on

    assert based_on.baseline_version == 1
    assert based_on.session_ids == [INFLIGHT_SESSION]
    assert based_on.self_report_ids == ["5e1f0e90-0000-4000-8000-000000000001"]


def test_based_on_lists_each_input_once():
    # Regression (found by the Schemathesis fuzz): based_on ids are uniqueItems in the contract, but a
    # request may repeat a self-report or carry two feature sets of the same session.
    req = request()
    assert req.self_reports
    req.self_reports = [*req.self_reports, req.self_reports[0]]
    req.feature_sets = [*req.feature_sets, req.feature_sets[0]]

    assessment = stub_pipeline().evaluate(req)

    assert assessment.based_on.session_ids == [INFLIGHT_SESSION]
    assert assessment.based_on.self_report_ids == ["5e1f0e90-0000-4000-8000-000000000001"]
    assert validation.errors("domain/assessment", dump(assessment)) == []


# Fakes sin herencia: el pipeline solo depende de la forma (Protocol).
class FakeBaseline:
    def __init__(self):
        self.calls = []

    def deviations(self, baseline, feature_sets):
        self.calls.append((baseline, list(feature_sets)))
        return [Deviation(feature_id="stretch.full_spine.peak_elongation_mm", value=1.0, score=9.0)]


class FakeRisk:
    def __init__(self):
        self.received = None

    def score(self, deviations, completeness):
        self.received = list(deviations)
        self.completeness = completeness
        drivers = [Driver(feature_id="stretch.full_spine.peak_elongation_mm", value=1.0, contribution=0.9)]
        return RiskResult(score=0.9, drivers=drivers, confidence=0.7)


class FakeTriage:
    def __init__(self):
        self.received = None

    def decide(self, context):
        self.received = context
        return TriageDecision(
            level=Level.RED,
            recommendations=["REC_X"],
            fired_rules=[FiredRule(rule_id="RULE_X", level=Level.RED, overrides_model=True)],
            escalation=Escalation(required=True, urgency=Urgency.PRIORITY, reasons=["because"]),
            domain_levels={AssessmentDomain.SPINE_LENGTH: Level.RED},
        )


def test_pipeline_wires_custom_implementations():
    baseline, risk, triage = FakeBaseline(), FakeRisk(), FakeTriage()
    req = request()

    assessment = AssessmentPipeline(baseline, risk, triage).evaluate(req)

    deviations = [Deviation(feature_id="stretch.full_spine.peak_elongation_mm", value=1.0, score=9.0)]
    assert baseline.calls[0][0] == req.baseline
    assert baseline.calls[0][1][:-1] == req.feature_sets
    assert baseline.calls[0][1][-1].session_id is None
    assert risk.received == deviations
    assert risk.completeness == assessment.completeness.score
    assert triage.received.deviations == deviations
    assert triage.received.risk.score == 0.9
    assert assessment.level == Level.RED
    assert assessment.risk_score == 0.9
    assert assessment.confidence == 0.7
    assert assessment.drivers[0].feature_id == "stretch.full_spine.peak_elongation_mm"
    assert assessment.recommendations == ["REC_X"]
    assert assessment.fired_rules[0].overrides_model
    assert assessment.escalation.required
    assert assessment.escalation.urgency == Urgency.PRIORITY


def test_pipeline_gives_the_triage_policy_the_full_context():
    triage = FakeTriage()
    req = request()

    assessment = AssessmentPipeline(FakeBaseline(), FakeRisk(), triage).evaluate(req)

    context = triage.received
    assert isinstance(context, TriageContext)
    assert context.completeness == assessment.completeness
    assert list(context.self_reports) == req.self_reports
    assert context.as_of == req.as_of


def test_null_self_reports_reach_the_triage_policy_as_an_empty_sequence():
    triage = FakeTriage()
    req = request()
    req.self_reports = None

    AssessmentPipeline(FakeBaseline(), FakeRisk(), triage).evaluate(req)

    assert list(triage.received.self_reports) == []


def test_triage_context_is_immutable():
    context = FakeTriageContextFactory()
    with pytest.raises(FrozenInstanceError):
        context.as_of = context.as_of  # pyright: ignore[reportAttributeAccessIssue]


def FakeTriageContextFactory() -> TriageContext:
    req = request()
    return TriageContext(risk=RiskResult(score=0.0), deviations=[], completeness=Completeness(score=1.0, missing=[]), self_reports=[], as_of=req.as_of)


def test_domain_levels_come_from_the_policy_then_the_overall_level():
    domains = {d.domain: d for d in AssessmentPipeline(FakeBaseline(), FakeRisk(), FakeTriage()).evaluate(request()).domains}

    assert domains[AssessmentDomain.SPINE_LENGTH].level == Level.RED
    assert domains[AssessmentDomain.SPINE_LENGTH].evaluated
    # Not evaluated: never a real level, whatever the overall one is.
    assert domains[AssessmentDomain.SYMPTOMS].level == Level.GREEN
    assert not domains[AssessmentDomain.SYMPTOMS].evaluated


def test_null_baseline_falls_back_to_the_earth_entry_for_other_gravity():
    req = request()
    by_feature = {d.feature_id: d for d in NullBaselineStrategy().deviations(req.baseline, req.feature_sets)}

    # in-flight 31.0 deg vs earth baseline mean 28.1
    assert by_feature["imu.t9.sagittal.rom_deg"] == Deviation("imu.t9.sagittal.rom_deg", 31.0, 31.0 - 28.1)
    assert "exercise.rep_count" not in by_feature


def test_null_baseline_prefers_the_entry_of_the_same_gravity():
    req = request()
    entry = req.baseline.entries[2].model_copy(update={"gravity_environment": GravityEnvironment.MICROGRAVITY, "mean": 30.0})
    req.baseline.entries.append(entry)

    result = {d.feature_id: d for d in NullBaselineStrategy().deviations(req.baseline, req.feature_sets)}

    assert result["imu.t9.sagittal.rom_deg"].score == 31.0 - 30.0


def test_null_baseline_uses_the_latest_value_of_each_feature():
    req = request()
    older = req.feature_sets[0].model_copy(deep=True)
    older.computed_at = req.feature_sets[0].computed_at.replace(year=2026)
    for feature in older.features:
        feature.value = 0.0

    result = NullBaselineStrategy().deviations(req.baseline, [req.feature_sets[0], older])

    assert all(d.value != 0.0 for d in result)
