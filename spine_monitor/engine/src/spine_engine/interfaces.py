"""Interfaces del motor. Son Protocols: las implementaciones no necesitan importar nada de aquí."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from spine_contracts import Baseline, Driver, Level, SensorReading


@dataclass(frozen=True)
class Deviation:
    metric: str
    value: float
    score: float


@dataclass(frozen=True)
class RiskResult:
    score: float
    drivers: list[Driver] = field(default_factory=list)


@dataclass(frozen=True)
class TriageDecision:
    level: Level
    escalate_to_ground: bool = False
    recommendations: list[str] = field(default_factory=list)


class BaselineStrategy(Protocol):
    def deviations(self, baseline: Baseline, readings: Sequence[SensorReading]) -> list[Deviation]: ...


class RiskModel(Protocol):
    def score(self, deviations: Sequence[Deviation]) -> RiskResult: ...


class TriagePolicy(Protocol):
    def decide(self, risk: RiskResult, deviations: Sequence[Deviation]) -> TriageDecision: ...
