"""Engine interfaces. They are Protocols: implementations do not need to import anything from here."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from spine_contracts import (
    AssessmentDomain,
    Baseline,
    BaselineEntry,
    BaselineRequest,
    Completeness,
    Driver,
    Escalation,
    FeatureSet,
    FeaturesRequest,
    FiredRule,
    GravityEnvironment,
    Level,
    SelfReport,
)

Direction = Literal["up", "down"]


@dataclass(frozen=True)
class Deviation:
    """Deviation of a feature from its baseline.

    `score` is the magnitude consumed by the risk model; fields with a default value are
    optional so that a simple strategy (such as the null one) does not have to fill them in.
    """

    feature_id: str
    value: float
    score: float
    z: float | None = None
    protocol_id: str | None = None
    reference_gravity: GravityEnvironment | None = None
    sustained: bool = False
    direction: Direction | None = None
    n_points: int = 1


@dataclass(frozen=True)
class ChangeSignal:
    """Verdict of a detector on a series: is there a sustained change, and in which direction?"""

    sustained: bool = False
    direction: Direction | None = None


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


@dataclass(frozen=True)
class TriageContext:
    """Everything the triage policy can look at to decide."""

    risk: RiskResult
    deviations: Sequence[Deviation]
    completeness: Completeness
    self_reports: Sequence[SelfReport]
    as_of: datetime
    feature_sets: Sequence[FeatureSet] = ()


class BaselineStrategy(Protocol):
    def deviations(self, baseline: Baseline, feature_sets: Sequence[FeatureSet]) -> list[Deviation]: ...


class DeviationScorer(Protocol):
    """Converts a value into a deviation score against its baseline entry."""

    def score(self, value: float, entry: BaselineEntry) -> float: ...


class ChangeDetector(Protocol):
    """Decides whether a series of deviations (oldest first) shows a sustained change."""

    def detect(self, series: Sequence[float]) -> ChangeSignal: ...


class VotePolicy(Protocol):
    """Combines the verdicts of several detectors into one."""

    def decide(self, signals: Sequence[ChangeSignal]) -> ChangeSignal: ...


class BaselineBuilder(Protocol):
    def build(self, request: BaselineRequest) -> Baseline: ...


class FeatureExtractor(Protocol):
    """Raw session data -> FeatureSet. The real extraction arrives with #15."""

    def extract(self, request: FeaturesRequest) -> FeatureSet: ...


class RiskModel(Protocol):
    """Deviations -> risk. `completeness` (in [0, 1]) is the part of what was expected that could be compared."""

    def score(self, deviations: Sequence[Deviation], completeness: float) -> RiskResult: ...


class TriagePolicy(Protocol):
    def decide(self, context: TriageContext) -> TriageDecision: ...
