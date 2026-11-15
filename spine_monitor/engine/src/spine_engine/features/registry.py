"""Registry of extractors per feature family (#40).

Each family (EMG, IMU, stretch...) implements :class:`FeatureFamilyExtractor` and registers here; the
composite extractor runs them in registration order. The default registry is empty: the metrics
arrive in #41-#45 and this infrastructure does not invent any.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from spine_contracts import Quality
from spine_contracts.models.domain.feature_set_schema import Reason

from spine_engine.features.context import ExtractionContext
from spine_engine.settings import EngineSettings


@dataclass(frozen=True, slots=True)
class ComputedFeature:
    """Feature computed by a family.

    ``streams`` lists the streams it used: the common quality is evaluated over their segments. ``quality``
    is only filled in if the family needs to replace the common quality. The unit is set by the catalog.
    """

    feature_id: str
    value: float
    streams: tuple[str, ...]
    n: int | None = None
    quality: Quality | None = None
    window: tuple[datetime, datetime] | None = None


@dataclass(frozen=True, slots=True)
class SkippedFeature:
    """Feature the family could not compute, and the reason."""

    feature_id: str
    reason: Reason


@dataclass(frozen=True, slots=True)
class FamilyResult:
    """Output of a family: what was computed and what was omitted with a reason."""

    computed: tuple[ComputedFeature, ...] = ()
    skipped: tuple[SkippedFeature, ...] = ()


class FeatureFamilyExtractor(Protocol):
    """A feature family. ``feature_ids`` are the concrete catalog ids it declares it produces."""

    family: str
    feature_ids: Sequence[str]

    def extract(self, context: ExtractionContext) -> FamilyResult: ...


class ExtractorRegistry:
    """Families in registration order (deterministic)."""

    def __init__(self) -> None:
        self._families: list[FeatureFamilyExtractor] = []

    def register(self, family: FeatureFamilyExtractor) -> None:
        self._families.append(family)

    def __iter__(self) -> Iterator[FeatureFamilyExtractor]:
        return iter(tuple(self._families))


def default_registry(settings: EngineSettings | None = None) -> ExtractorRegistry:
    """Registry of the exercise, EMG, IMU, spectral and cord families."""
    # local import: exercise imports the types of this module (avoids the cycle)
    from spine_engine.features.emg import EmgFamily
    from spine_engine.features.emg_spectral import EmgSpectralFamily
    from spine_engine.features.exercise import ExerciseFamily
    from spine_engine.features.imu import ImuFamily
    from spine_engine.features.measured import MeasuredFamily
    from spine_engine.features.repetitions import RepetitionService
    from spine_engine.features.repetitions_imu import detect_imu_windows
    from spine_engine.features.stretch import StretchFamily

    settings = settings or EngineSettings()

    # a single repetitions service (with IMU fallback) to share with the families of #42-#45
    repetitions = RepetitionService(detect_imu_windows)
    registry = ExtractorRegistry()
    registry.register(ExerciseFamily(repetitions))
    registry.register(EmgFamily(repetitions))
    registry.register(ImuFamily(repetitions))
    registry.register(EmgSpectralFamily(repetitions))
    registry.register(
        StretchFamily(repetitions, default_recovery_wait_s=settings.stretch_recovery_wait_s, recovery_fraction=settings.stretch_recovery_fraction)
    )
    registry.register(MeasuredFamily())
    return registry
