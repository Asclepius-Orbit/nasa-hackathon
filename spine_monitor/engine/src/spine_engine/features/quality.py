"""Common quality of the data feeding a feature. No clinical thresholds.

- ``coverage`` = present / expected samples (seq gaps + ``dropped_samples``, see ``ordering``).
- ``saturated_fraction`` only where there is something to judge it by: stretch with ``circuit.adc_bits`` (counts at
  0 or at the ADC maximum) and IMU with ``config.accel_range_g`` / ``gyro_range_dps`` (|value| ≥ range).
  EMG arrives in mV and does not carry the explicit full scale: it is not judged. Without data -> ``None``, never an invented 0.
- ``score = coverage · (1 − saturated_fraction)`` (absent fraction = 0), clamped to [0, 1].
- Contract enum flags, in its order: ``clipping`` (IMU), ``packet_gap`` (gaps, lost
  samples or restart), ``out_of_calibration`` (expired calibration), ``saturated_adc`` (stretch),
  ``clock_uncertain``. The calibration and clock ones are decided by the caller.

Coverage 0 means there is no data: the caller emits ``no_data``.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from spine_contracts import ImuChunk, Quality, QualityFlag, StreamChunk, StretchChunk
from spine_contracts.models.common_schema import AssessedBy

from spine_engine.features.ordering import Segment

_FLAG_ORDER = list(QualityFlag)


def _stretch_saturation(chunk: StretchChunk) -> tuple[int, int] | None:
    if chunk.circuit is None or chunk.circuit.adc_bits is None:
        return None
    top = 2**chunk.circuit.adc_bits - 1
    counts = chunk.data.adc_counts
    return sum(1 for c in counts if c <= 0 or c >= top), len(counts)


def _imu_saturation(chunk: ImuChunk) -> tuple[int, int] | None:
    config = chunk.config
    if config is None:
        return None
    data = chunk.data
    channels: list[tuple[list[float], float]] = []
    if config.accel_range_g is not None:
        channels += [(ch.root, float(config.accel_range_g)) for ch in (data.accel_x, data.accel_y, data.accel_z)]
    if config.gyro_range_dps is not None:
        channels += [(ch.root, float(config.gyro_range_dps)) for ch in (data.gyro_x, data.gyro_y, data.gyro_z)]
    if not channels:
        return None
    n = min(len(values) for values, _ in channels)
    saturated = sum(1 for i in range(n) if any(abs(values[i]) >= limit for values, limit in channels))
    return saturated, n


def saturation_counts(chunk: StreamChunk) -> tuple[int, int] | None:
    """``(saturated samples, judged samples)`` of the chunk, or ``None`` if there is nothing to judge it by."""
    match chunk:
        case StretchChunk():
            return _stretch_saturation(chunk)
        case ImuChunk():
            return _imu_saturation(chunk)
        case _:
            return None


_SATURATION_FLAG = {"stretch": QualityFlag.SATURATED_ADC, "imu": QualityFlag.CLIPPING}


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    """Quality computed by the engine for a set of segments."""

    coverage: float
    saturated_fraction: float | None
    score: float
    flags: tuple[QualityFlag, ...]

    def to_quality(self) -> Quality:
        """Contract ``quality`` object, with ``assessed_by="engine"``."""
        return Quality(score=self.score, flags=list(self.flags), assessed_by=AssessedBy.ENGINE)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def assess_quality(segments: Sequence[Segment], *, calibration_expired: bool = False, clock_uncertain: bool = False) -> QualityAssessment:
    """Evaluate coverage, saturation, score and flags of the segments of a feature."""
    present = sum(s.present_samples for s in segments)
    expected = sum(s.expected_samples for s in segments)
    coverage = _clamp(present / expected) if expected > 0 else 0.0

    flags: set[QualityFlag] = set()
    saturated = judged = 0
    for segment in segments:
        for chunk in segment.chunks:
            counts = saturation_counts(chunk)
            if counts is None:
                continue
            saturated += counts[0]
            judged += counts[1]
            if counts[0]:
                flags.add(_SATURATION_FLAG[chunk.stream_kind])
    saturated_fraction = _clamp(saturated / judged) if judged > 0 else None

    restarted = any(len({s.boot_id for s in segments if s.stream_id == stream}) > 1 for stream in {s.stream_id for s in segments})
    if coverage < 1.0 and expected > 0 or restarted:
        flags.add(QualityFlag.PACKET_GAP)
    if calibration_expired:
        flags.add(QualityFlag.OUT_OF_CALIBRATION)
    if clock_uncertain:
        flags.add(QualityFlag.CLOCK_UNCERTAIN)

    score = _clamp(coverage * (1.0 - (saturated_fraction or 0.0)))
    return QualityAssessment(coverage, saturated_fraction, score, tuple(sorted(flags, key=_FLAG_ORDER.index)))
