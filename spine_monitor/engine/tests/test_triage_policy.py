from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import AssessmentDomain, Completeness, DailySelfReport, Level, MissingItem, MissingReason, RedFlagReport, Source, Urgency
from spine_engine.factory import default_pipeline
from spine_engine.interfaces import RiskResult, TriageContext
from spine_engine.settings import EngineSettings
from spine_engine.triage import DeclarativeTriagePolicy, PolicyConfig


def context(score=0.0, completeness=1.0):
    return TriageContext(RiskResult(score), [], Completeness(score=completeness, missing=[]), [], datetime(2026, 1, 1, tzinfo=UTC))


def test_model_cutoffs_are_inclusive_and_optional():
    assert DeclarativeTriagePolicy(PolicyConfig()).decide(context(1)).level == Level.GREEN
    policy = DeclarativeTriagePolicy(PolicyConfig(amber_cutoff=0.3, red_cutoff=0.8))
    assert policy.decide(context(0.3)).level == Level.AMBER
    assert policy.decide(context(0.8)).level == Level.RED


def test_incomplete_data_produces_amber_floor():
    assert DeclarativeTriagePolicy(PolicyConfig()).decide(context(completeness=0.9)).level == Level.AMBER


def test_unknown_flags_escalate_and_future_reports_are_ignored():
    report = RedFlagReport(schema_version="1.0.0", report_id="00000000-0000-0000-0000-000000000001",
                           astronaut_id="AST-01", timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                           source=Source.DEVICE, kind="red_flag", red_flags=["leg_weakness"])
    policy = DeclarativeTriagePolicy(PolicyConfig())
    ctx = context()
    decision = policy.decide(TriageContext(ctx.risk, [], ctx.completeness, [report], ctx.as_of))
    assert decision.level == Level.RED
    assert decision.escalation.required and decision.escalation.urgency == Urgency.IMMEDIATE
    assert any(rule.domain.value == "safety" for rule in decision.fired_rules)
    past = datetime(2025, 1, 1, tzinfo=UTC)
    assert not policy.decide(TriageContext(ctx.risk, [], ctx.completeness, [report], past)).escalation.required


def test_invalid_configuration_is_rejected():
    with pytest.raises(ValueError):
        PolicyConfig(amber_cutoff=0.7, red_cutoff=0.3)
    with pytest.raises(ValueError):
        PolicyConfig(amber_cutoff=0.3)


def test_red_flag_rules_require_urgency():
    with pytest.raises(ValueError):
        PolicyConfig(rules=[{"rule_id": "RULE_TEST", "datum": "red_flag", "key": "leg_weakness", "level": "red"}])


def test_default_pipeline_uses_the_declarative_policy():
    pipeline = default_pipeline(EngineSettings(_env_file=None))
    assert isinstance(pipeline._triage_policy, DeclarativeTriagePolicy)


def test_missing_identifiable_domain_is_amber_without_contaminating_other_domains():
    ctx = context()
    missing = Completeness(score=0.5, missing=[MissingItem(what="emg.test.value", reason=MissingReason.NO_DATA)])
    decision = DeclarativeTriagePolicy(PolicyConfig()).decide(TriageContext(ctx.risk, [], missing, [], ctx.as_of))
    assert decision.level == Level.AMBER
    assert any(rule.domain.value == "safety" for rule in decision.fired_rules)
    assert decision.domain_levels[AssessmentDomain.MUSCLE] == Level.AMBER
    assert decision.domain_levels[AssessmentDomain.KINEMATICS] == Level.GREEN


def test_declarative_rule_explains_threshold_and_escalation():
    policy = DeclarativeTriagePolicy(PolicyConfig(rules=[{"rule_id": "RULE_EXAMPLE", "datum": "risk", "threshold": 0.5,
        "level": "red", "domain": "muscle", "recommendations": ["REC_EXAMPLE"], "urgency": "priority"}]))
    decision = policy.decide(context(0.5))
    assert decision.level == Level.RED
    assert decision.domain_levels[AssessmentDomain.MUSCLE] == Level.RED
    assert decision.fired_rules[0].evidence.threshold == 0.5
    assert decision.recommendations == ["REC_EXAMPLE"]
    assert decision.escalation.urgency == Urgency.PRIORITY


def test_rule_reads_daily_report_value_only_up_to_as_of():
    report = DailySelfReport(schema_version="1.0.0", report_id="00000000-0000-0000-0000-000000000002",
        astronaut_id="AST-01", timestamp=datetime(2026, 1, 1, tzinfo=UTC), source=Source.DEVICE,
        kind="daily", pain_nrs=7, stiffness_nrs=2)
    policy = DeclarativeTriagePolicy(PolicyConfig(rules=[{"rule_id": "RULE_EXAMPLE", "datum": "self_report",
        "key": "pain_nrs", "threshold": 7, "level": "amber", "domain": "symptoms"}]))
    ctx = context()
    result = policy.decide(TriageContext(ctx.risk, [], ctx.completeness, [report], ctx.as_of))
    assert result.domain_levels[AssessmentDomain.SYMPTOMS] == Level.AMBER


@pytest.mark.property
@given(st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False),
       st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False))
def test_increasing_risk_never_reduces_model_level(a, b):
    policy = DeclarativeTriagePolicy(PolicyConfig(amber_cutoff=0.3, red_cutoff=0.8))
    rank = {Level.GREEN: 0, Level.AMBER: 1, Level.RED: 2}
    assert rank[policy.decide(context(min(a, b))).level] <= rank[policy.decide(context(max(a, b))).level]


def test_loads_policy_from_json(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"amber_cutoff": 0.3, "red_cutoff": 0.8}', encoding="utf-8")
    assert DeclarativeTriagePolicy(PolicyConfig.from_file(path)).decide(context(0.8)).level == Level.RED


def test_recognized_flag_uses_its_configured_urgency():
    report = RedFlagReport(schema_version="1.0.0", report_id="00000000-0000-0000-0000-000000000001",
        astronaut_id="AST-01", timestamp=datetime(2026, 1, 1, tzinfo=UTC), source=Source.DEVICE,
        kind="red_flag", red_flags=["leg_weakness"])
    config = PolicyConfig(rules=[{"rule_id": "RULE_EXAMPLE", "datum": "red_flag", "key": "leg_weakness",
                                  "level": "amber", "urgency": "priority"}])
    ctx = context()
    result = DeclarativeTriagePolicy(config).decide(TriageContext(ctx.risk, [], ctx.completeness, [report], ctx.as_of))
    assert result.level == Level.AMBER
    assert result.escalation.urgency == Urgency.PRIORITY
    assert [rule.rule_id for rule in result.fired_rules] == ["RULE_EXAMPLE"]
