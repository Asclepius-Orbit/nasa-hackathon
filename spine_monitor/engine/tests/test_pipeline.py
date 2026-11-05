import json
from pathlib import Path

from spine_contracts import (
    AssessmentDomain,
    AssessRequest,
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
from spine_contracts.models.domain.feature_set_schema import SkippedItem
from spine_engine import (
    AssessmentPipeline,
    Deviation,
    NullBaselineStrategy,
    RiskResult,
    TriageDecision,
    stub_pipeline,
)

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

    # 4 of the 5 features have a baseline entry; the repetition count has none.
    assert completeness.score == 0.8
    assert completeness.missing == [MissingItem(what="exercise.rep_count", reason=MissingReason.NO_BASELINE)]


def test_skipped_features_count_as_missing():
    req = request()
    req.feature_sets[0].skipped = [SkippedItem(feature_id="emg.latissimus_dorsi.right.rms_pct_mvc", reason="stream_missing")]

    completeness = stub_pipeline().evaluate(req).completeness

    assert MissingItem(what="emg.latissimus_dorsi.right.rms_pct_mvc", reason=MissingReason.SENSOR_OFFLINE) in completeness.missing
    assert completeness.score < 0.8


def test_null_skipped_in_a_feature_set_means_nothing_was_skipped():
    # The schema allows `skipped: null`; the completeness must read it as "none skipped".
    req = request()
    req.feature_sets[0].skipped = None

    completeness = stub_pipeline().evaluate(req).completeness

    assert completeness.score == 0.8


def test_null_self_reports_mean_no_self_reports():
    # The schema allows `self_reports: null`; based_on must list none instead of failing.
    req = request()
    req.self_reports = None

    assert stub_pipeline().evaluate(req).based_on.self_report_ids == []


def test_based_on_traces_the_inputs():
    based_on = stub_pipeline().evaluate(request()).based_on

    assert based_on.baseline_version == 1
    assert based_on.session_ids == [INFLIGHT_SESSION]
    assert based_on.self_report_ids == ["5e1f0e90-0000-4000-8000-000000000001"]


# Fakes sin herencia: el pipeline solo depende de la forma (Protocol).
class FakeBaseline:
    def __init__(self):
        self.calls = []

    def deviations(self, baseline, feature_sets):
        self.calls.append((baseline, list(feature_sets)))
        return [Deviation(feature_id="stretch.lumbar.peak_elongation_mm", value=1.0, score=9.0)]


class FakeRisk:
    def __init__(self):
        self.received = None

    def score(self, deviations):
        self.received = list(deviations)
        drivers = [Driver(feature_id="stretch.lumbar.peak_elongation_mm", value=1.0, contribution=0.9)]
        return RiskResult(score=0.9, drivers=drivers, confidence=0.7)


class FakeTriage:
    def __init__(self):
        self.received = None

    def decide(self, risk, deviations):
        self.received = (risk, list(deviations))
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

    deviations = [Deviation(feature_id="stretch.lumbar.peak_elongation_mm", value=1.0, score=9.0)]
    assert baseline.calls == [(req.baseline, req.feature_sets)]
    assert risk.received == deviations
    assert triage.received[1] == deviations
    assert triage.received[0].score == 0.9
    assert assessment.level == Level.RED
    assert assessment.risk_score == 0.9
    assert assessment.confidence == 0.7
    assert assessment.drivers[0].feature_id == "stretch.lumbar.peak_elongation_mm"
    assert assessment.recommendations == ["REC_X"]
    assert assessment.fired_rules[0].overrides_model
    assert assessment.escalation.required
    assert assessment.escalation.urgency == Urgency.PRIORITY


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
    assert by_feature["imu.l3.sagittal.rom_deg"] == Deviation("imu.l3.sagittal.rom_deg", 31.0, 31.0 - 28.1)
    assert "exercise.rep_count" not in by_feature


def test_null_baseline_prefers_the_entry_of_the_same_gravity():
    req = request()
    entry = req.baseline.entries[2].model_copy(update={"gravity_environment": GravityEnvironment.MICROGRAVITY, "mean": 30.0})
    req.baseline.entries.append(entry)

    result = {d.feature_id: d for d in NullBaselineStrategy().deviations(req.baseline, req.feature_sets)}

    assert result["imu.l3.sagittal.rom_deg"].score == 31.0 - 30.0


def test_null_baseline_uses_the_latest_value_of_each_feature():
    req = request()
    older = req.feature_sets[0].model_copy(deep=True)
    older.computed_at = req.feature_sets[0].computed_at.replace(year=2026)
    for feature in older.features:
        feature.value = 0.0

    result = NullBaselineStrategy().deviations(req.baseline, [req.feature_sets[0], older])

    assert all(d.value != 0.0 for d in result)
