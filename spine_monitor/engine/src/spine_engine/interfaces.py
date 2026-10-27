"""Interfaces del motor. Son Protocols: las implementaciones no necesitan importar nada de aquí."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from spine_contracts import (
    AssessmentDomain,
    Baseline,
    Driver,
    Escalation,
    FeatureSet,
    FiredRule,
    Level,
)


@dataclass(frozen=True)
class Deviation:
    feature_id: str
    value: float
    score: float


@dataclass(frozen=True)
class RiskResult:
    score: float
    drivers: list[Driver] = field(default_factory=list)
    confidence: float = 0.0


def no_escalation() -> Escalation:
    return Escalation(required=False, reasons=[])


@dataclass(frozen=True)
class TriageDecision:
    level: Level
    recommendations: list[str] = field(default_factory=list)
    fired_rules: list[FiredRule] = field(default_factory=list)
    escalation: Escalation = field(default_factory=no_escalation)
    domain_levels: dict[AssessmentDomain, Level] = field(default_factory=dict)


class BaselineStrategy(Protocol):
    def deviations(self, baseline: Baseline, feature_sets: Sequence[FeatureSet]) -> list[Deviation]: ...


class RiskModel(Protocol):
    def score(self, deviations: Sequence[Deviation]) -> RiskResult: ...


class TriagePolicy(Protocol):
    def decide(self, risk: RiskResult, deviations: Sequence[Deviation]) -> TriageDecision: ...
