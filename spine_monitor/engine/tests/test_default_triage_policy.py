"""The default policy shipped with the engine is PROVISIONAL (pending Aguado); its content lives only in the JSON."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import spine_engine
from spine_contracts import Completeness, DailySelfReport, Level, RedFlagReport, Source, Urgency
from spine_engine.factory import default_pipeline
from spine_engine.interfaces import RiskResult, TriageContext
from spine_engine.settings import EngineSettings
from spine_engine.triage import DeclarativeTriagePolicy, PolicyConfig

PACKAGE = Path(spine_engine.__file__).parent
RAW = json.loads((PACKAGE / "triage-policy.json").read_text(encoding="utf-8"))
RULES = RAW["rules"]
FLAG_RULES = [rule for rule in RULES if rule["datum"] == "red_flag"]
MARKER = "provisional, pendiente de Aguado"
AS_OF = datetime(2026, 1, 1, tzinfo=UTC)
SCHEMA_FLAGS = json.loads((PACKAGE.parents[2] / "contracts/schemas/v1/domain/self-report.schema.json").read_text(encoding="utf-8"))


def context(score=0.0, reports=()):
    return TriageContext(RiskResult(score), [], Completeness(score=1.0, missing=[]), list(reports), AS_OF)


def flag_report(flag):
    return RedFlagReport(
        schema_version="1.0.0",
        report_id="00000000-0000-0000-0000-000000000001",
        astronaut_id="AST-01",
        timestamp=AS_OF,
        source=Source.DEVICE,
        kind="red_flag",
        red_flags=[flag],
    )


def policy():
    return DeclarativeTriagePolicy(PolicyConfig.default())


def test_default_policy_is_the_json_file():
    assert PolicyConfig.default() == PolicyConfig.from_file(PACKAGE / "triage-policy.json")


def test_default_pipeline_uses_default_policy_unless_overridden(tmp_path):
    assert default_pipeline(EngineSettings(_env_file=None))._triage_policy.config == PolicyConfig.default()
    override = tmp_path / "p.json"
    override.write_text('{"amber_cutoff": 0.1, "red_cutoff": 0.2}', encoding="utf-8")
    config = default_pipeline(EngineSettings(_env_file=None, triage_policy_file=override))._triage_policy.config
    assert config == PolicyConfig(amber_cutoff=0.1, red_cutoff=0.2)


def test_every_schema_red_flag_has_exactly_one_rule():
    schema_flags = SCHEMA_FLAGS["$defs"]["red_flag"]["enum"]
    assert sorted(rule["key"] for rule in FLAG_RULES) == sorted(schema_flags)


@pytest.mark.parametrize("rule", FLAG_RULES, ids=lambda rule: rule["key"])
def test_red_flag_case_table(rule):
    decision = policy().decide(context(0.0, [flag_report(rule["key"])]))
    assert decision.level == Level(rule["level"])
    assert decision.escalation.required
    assert decision.escalation.urgency == Urgency(rule["urgency"])
    assert decision.escalation.reasons == [rule["rule_id"]]
    assert decision.recommendations == rule["recommendations"]
    assert [fired.rule_id for fired in decision.fired_rules] == [rule["rule_id"]]


def test_red_flag_overrides_low_risk():
    rule = next(rule for rule in FLAG_RULES if rule["level"] == "red")
    decision = policy().decide(context(0.0, [flag_report(rule["key"])]))
    assert decision.level == Level.RED
    assert decision.fired_rules[0].overrides_model


def test_no_flag_means_no_escalation():
    decision = policy().decide(context(0.0))
    assert not decision.escalation.required and decision.recommendations == []


def test_risk_cutoffs_from_json():
    amber, red = RAW["amber_cutoff"], RAW["red_cutoff"]
    assert 0 <= amber < red <= 1
    p = policy()
    assert p.decide(context(red)).level == Level.RED
    assert p.decide(context(1.0)).level == Level.RED
    assert p.decide(context(amber)).level == Level.AMBER
    assert p.decide(context((amber + red) / 2)).level == Level.AMBER
    if amber > 0:
        assert p.decide(context(amber - 1e-6)).level == Level.GREEN
    assert p.decide(context(red - 1e-6)).level == Level.AMBER


SELF_REPORT_RULES = [rule for rule in RULES if rule["datum"] == "self_report"]


@pytest.mark.parametrize("rule", SELF_REPORT_RULES, ids=lambda rule: rule["rule_id"])
def test_self_report_rules_fire_at_the_threshold_and_not_below(rule):
    def daily(value):
        return DailySelfReport(
            schema_version="1.0.0",
            report_id="00000000-0000-0000-0000-000000000002",
            astronaut_id="AST-01",
            timestamp=AS_OF,
            source=Source.DEVICE,
            kind="daily",
            **{"pain_nrs": 0, "stiffness_nrs": 0, rule["key"]: value},
        )

    assert rule["operator"] == ">="
    at = policy().decide(context(0.0, [daily(int(rule["threshold"]))]))
    assert rule["rule_id"] in [fired.rule_id for fired in at.fired_rules]
    assert at.level == Level(rule["level"])
    below = policy().decide(context(0.0, [daily(int(rule["threshold"]) - 1)]))
    assert rule["rule_id"] not in [fired.rule_id for fired in below.fired_rules]


def test_every_rule_is_marked_provisional_and_well_formed():
    assert RULES
    for rule in RULES:
        assert rule["source"].startswith(MARKER), rule["rule_id"]
        assert len(rule["source"]) > len(MARKER) + 5
        assert re.fullmatch(r"RULE_[A-Z0-9_]+", rule["rule_id"])
        assert all(re.fullmatch(r"REC_[A-Z0-9_]+", rec) for rec in rule["recommendations"])
    assert len({rule["rule_id"] for rule in RULES}) == len(RULES)
    assert RAW["source"].startswith(MARKER)


def test_recommendation_ids_are_known_to_the_ui():
    codes = (PACKAGE.parents[3] / "app/ui/src/renderer/strings/codes.ts").read_text(encoding="utf-8")
    known = set(re.findall(r"^\s{2}(REC_[A-Z0-9_]+):", codes, flags=re.M))
    assert known, "regex found no REC ids in codes.ts"
    used = {rec for rule in RULES for rec in rule["recommendations"]}
    assert used <= known


def test_no_production_module_defines_rule_ids_besides_builtins():
    found: dict[str, set[str]] = {}
    for path in PACKAGE.rglob("*.py"):
        ids = set(re.findall(r"RULE_[A-Z0-9_]+", path.read_text(encoding="utf-8")))
        if ids:
            found[path.name] = ids
    assert found == {"triage.py": {"RULE_UNKNOWN_RED_FLAG", "RULE_INCOMPLETE_DATA"}}
