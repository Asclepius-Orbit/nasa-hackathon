"""Interfaces del motor. Son Protocols: las implementaciones no necesitan importar nada de aquí."""

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
    """Desviación de una feature respecto a su baseline.

    `score` es la magnitud que consume el modelo de riesgo; los campos con valor por defecto son
    opcionales para que una estrategia simple (como la nula) no tenga que rellenarlos.
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
    """Veredicto de un detector sobre una serie: ¿hay un cambio sostenido, y hacia dónde?"""

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
    """Todo lo que la política de triaje puede mirar para decidir."""

    risk: RiskResult
    deviations: Sequence[Deviation]
    completeness: Completeness
    self_reports: Sequence[SelfReport]
    as_of: datetime


class BaselineStrategy(Protocol):
    def deviations(self, baseline: Baseline, feature_sets: Sequence[FeatureSet]) -> list[Deviation]: ...


class DeviationScorer(Protocol):
    """Convierte un valor en una puntuación de desviación respecto a su entrada de baseline."""

    def score(self, value: float, entry: BaselineEntry) -> float: ...


class ChangeDetector(Protocol):
    """Decide si una serie de desviaciones (más antigua primero) muestra un cambio sostenido."""

    def detect(self, series: Sequence[float]) -> ChangeSignal: ...


class VotePolicy(Protocol):
    """Combina los veredictos de varios detectores en uno."""

    def decide(self, signals: Sequence[ChangeSignal]) -> ChangeSignal: ...


class BaselineBuilder(Protocol):
    def build(self, request: BaselineRequest) -> Baseline: ...


class FeatureExtractor(Protocol):
    """Datos crudos de una sesión -> FeatureSet. La extracción real llega con #15."""

    def extract(self, request: FeaturesRequest) -> FeatureSet: ...


class RiskModel(Protocol):
    """Desviaciones -> riesgo. `completeness` (en [0, 1]) es la parte de lo esperado que se pudo comparar."""

    def score(self, deviations: Sequence[Deviation], completeness: float) -> RiskResult: ...


class TriagePolicy(Protocol):
    def decide(self, context: TriageContext) -> TriageDecision: ...
