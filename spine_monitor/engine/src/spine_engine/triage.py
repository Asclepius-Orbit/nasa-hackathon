"""Configurable triage mechanics; policy content must be clinically reviewed."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from spine_contracts import AssessmentDomain, DailySelfReport, Escalation, FiredRule, Level, RedFlagReport, Urgency
from spine_contracts.models.domain.assessment_schema import Domain2, Evidence, Operator

from spine_engine.interfaces import TriageContext, TriageDecision
from spine_engine.pipeline import domain_of

_LEVEL = {Level.GREEN: 0, Level.AMBER: 1, Level.RED: 2}
_URGENCY = {Urgency.ROUTINE: 0, Urgency.PRIORITY: 1, Urgency.IMMEDIATE: 2}


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str = Field(pattern=r"^RULE_[A-Z0-9_]+$")
    datum: Literal["risk", "deviation", "self_report", "red_flag"]
    key: str = ""
    operator: Literal[">", ">=", "<", "<=", "==", "!="] = ">="
    threshold: float = Field(default=1, allow_inf_nan=False)
    level: Level
    domain: Literal["spine_length", "muscle", "kinematics", "symptoms", "safety"] = "safety"
    recommendations: list[str] = Field(default_factory=list)
    urgency: Urgency | None = None

    @model_validator(mode="after")
    def valid_flag_rule(self):
        if self.datum == "red_flag" and (not self.key or self.urgency is None):
            raise ValueError("red flag rules require a flag and an urgency")
        if self.datum == "red_flag" and self.domain != "safety":
            raise ValueError("red flag rules belong to the safety domain")
        if self.datum == "self_report" and self.key not in {"pain_nrs", "pain_nrs_worst_24h", "stiffness_nrs"}:
            raise ValueError("unsupported self report field")
        if any(not recommendation.startswith("REC_") for recommendation in self.recommendations):
            raise ValueError("recommendations must start with REC_")
        return self


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amber_cutoff: float | None = Field(default=None, ge=0, le=1)
    red_cutoff: float | None = Field(default=None, ge=0, le=1)
    unknown_flag_urgency: Urgency = Urgency.IMMEDIATE
    rules: list[Rule] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_config(self):
        if (self.amber_cutoff is None) != (self.red_cutoff is None):
            raise ValueError("both risk cutoffs must be supplied together")
        if self.amber_cutoff is not None and self.red_cutoff is not None and self.amber_cutoff > self.red_cutoff:
            raise ValueError("amber cutoff must not exceed red cutoff")
        if len({rule.rule_id for rule in self.rules}) != len(self.rules):
            raise ValueError("rule ids must be unique")
        return self

    @classmethod
    def from_file(cls, path: Path):
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def _matches(value: float, operator: str, threshold: float) -> bool:
    return {">": value > threshold, ">=": value >= threshold, "<": value < threshold,
            "<=": value <= threshold, "==": value == threshold, "!=": value != threshold}[operator]


class DeclarativeTriagePolicy:
    def __init__(self, config: PolicyConfig) -> None:
        self.config = config

    def decide(self, context: TriageContext) -> TriageDecision:
        model_level = Level.GREEN
        if self.config.red_cutoff is not None and context.risk.score >= self.config.red_cutoff:
            model_level = Level.RED
        elif self.config.amber_cutoff is not None and context.risk.score >= self.config.amber_cutoff:
            model_level = Level.AMBER
        level = model_level
        domain_levels: dict[AssessmentDomain, Level] = dict.fromkeys(AssessmentDomain, model_level)
        fired: list[FiredRule] = []
        recommendations: list[str] = []
        urgencies: list[Urgency] = []
        reasons: list[str] = []
        flags = {flag.value for report in context.self_reports if report.timestamp <= context.as_of
                 and isinstance(report, (DailySelfReport, RedFlagReport)) for flag in (report.red_flags or [])}
        recognized: set[str] = set()
        for rule in self.config.rules:
            if rule.datum == "red_flag":
                if rule.key not in flags:
                    continue
                values = [1.0]
            elif rule.datum == "risk":
                values = [context.risk.score]
            elif rule.datum == "self_report":
                values = [float(value) for report in context.self_reports if report.kind == "daily"
                          and report.timestamp <= context.as_of
                          if (value := getattr(report, rule.key, None)) is not None]
            else:
                values = [d.value for d in context.deviations if d.feature_id == rule.key]
            for value in values:
                if not _matches(value, rule.operator, rule.threshold):
                    continue
                if rule.datum == "red_flag":
                    recognized.add(rule.key)
                level = max(level, rule.level, key=_LEVEL.__getitem__)
                if rule.domain != "safety":
                    domain = AssessmentDomain(rule.domain)
                    domain_levels[domain] = max(domain_levels.get(domain, Level.GREEN), rule.level, key=_LEVEL.__getitem__)
                fired.append(FiredRule(rule_id=rule.rule_id, domain=Domain2(rule.domain), level=rule.level,
                    overrides_model=_LEVEL[rule.level] > _LEVEL[model_level],
                    evidence=Evidence(datum=rule.key or rule.datum, value=value, operator=Operator(rule.operator), threshold=rule.threshold)))
                recommendations.extend(rule.recommendations)
                if rule.urgency is not None:
                    urgencies.append(rule.urgency)
                    reasons.append(rule.rule_id)
        for flag in sorted(flags - recognized):
            urgencies.append(self.config.unknown_flag_urgency)
            reasons.append(flag)
            level = max(level, Level.RED, key=_LEVEL.__getitem__)
            fired.append(FiredRule(rule_id="RULE_UNKNOWN_RED_FLAG", domain=Domain2.SAFETY, level=Level.RED,
                overrides_model=_LEVEL[Level.RED] > _LEVEL[model_level], description=f"Unrecognized red flag: {flag}", evidence=None))
        if context.completeness.score < 1 or context.completeness.missing:
            level = max(level, Level.AMBER, key=_LEVEL.__getitem__)
            evidence = (Evidence(datum="completeness", value=context.completeness.score, operator=Operator.field__1, threshold=1)
                        if context.completeness.score < 1 else None)
            fired.append(FiredRule(rule_id="RULE_INCOMPLETE_DATA", domain=Domain2.SAFETY, level=Level.AMBER,
                overrides_model=_LEVEL[Level.AMBER] > _LEVEL[model_level], description="Incomplete data", evidence=evidence))
            for item in context.completeness.missing:
                domain = domain_of(item.what)
                if domain is None:
                    domain = next((candidate for candidate in AssessmentDomain if candidate.value == item.what), None)
                if item.what == "self_report":
                    domain = AssessmentDomain.SYMPTOMS
                if domain is not None:
                    domain_levels[domain] = max(domain_levels.get(domain, Level.GREEN), Level.AMBER, key=_LEVEL.__getitem__)
        escalation = Escalation(required=bool(urgencies), reasons=reasons)
        if urgencies:
            escalation.urgency = max(urgencies, key=_URGENCY.__getitem__)
        return TriageDecision(level=level, domain_levels=domain_levels, fired_rules=fired,
                              recommendations=list(dict.fromkeys(recommendations)), escalation=escalation)
