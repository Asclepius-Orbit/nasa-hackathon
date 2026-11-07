"""Registro de extractores por familia de features (#40).

Cada familia (EMG, IMU, stretch...) implementa :class:`FeatureFamilyExtractor` y se registra aquí; el
extractor compuesto las ejecuta en orden de registro. El registro por defecto está vacío: las métricas
llegan en #41-#45 y esta infraestructura no inventa ninguna.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from spine_contracts import Quality
from spine_contracts.models.domain.feature_set_schema import Reason

from spine_engine.features.context import ExtractionContext


@dataclass(frozen=True, slots=True)
class ComputedFeature:
    """Feature calculada por una familia.

    ``streams`` indica los streams que usó: la calidad común se evalúa sobre sus segmentos. ``quality``
    solo se rellena si la familia necesita sustituir la calidad común. La unidad la pone el catálogo.
    """

    feature_id: str
    value: float
    streams: tuple[str, ...]
    n: int | None = None
    quality: Quality | None = None


@dataclass(frozen=True, slots=True)
class SkippedFeature:
    """Feature que la familia no pudo calcular y el motivo."""

    feature_id: str
    reason: Reason


@dataclass(frozen=True, slots=True)
class FamilyResult:
    """Salida de una familia: lo calculado y lo omitido con motivo."""

    computed: tuple[ComputedFeature, ...] = ()
    skipped: tuple[SkippedFeature, ...] = ()


class FeatureFamilyExtractor(Protocol):
    """Una familia de features. ``feature_ids`` son los ids concretos del catálogo que declara producir."""

    family: str
    feature_ids: Sequence[str]

    def extract(self, context: ExtractionContext) -> FamilyResult: ...


class ExtractorRegistry:
    """Familias en orden de registro (determinista)."""

    def __init__(self) -> None:
        self._families: list[FeatureFamilyExtractor] = []

    def register(self, family: FeatureFamilyExtractor) -> None:
        self._families.append(family)

    def __iter__(self) -> Iterator[FeatureFamilyExtractor]:
        return iter(tuple(self._families))


def default_registry() -> ExtractorRegistry:
    """Registro por defecto: vacío hasta que #41-#45 añadan sus familias."""
    return ExtractorRegistry()
