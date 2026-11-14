"""Calidad común de los datos que alimentan una feature. Sin umbrales clínicos.

- ``coverage`` = muestras presentes / esperadas (huecos de seq + ``dropped_samples``, ver ``ordering``).
- ``saturated_fraction`` solo donde hay con qué juzgarla: stretch con ``circuit.adc_bits`` (cuentas en
  0 o en el máximo del ADC) e IMU con ``config.accel_range_g`` / ``gyro_range_dps`` (|valor| ≥ rango).
  EMG llega en mV y no trae el fondo de escala explícito: no se juzga. Sin datos -> ``None``, nunca 0 inventado.
- ``score = coverage · (1 − saturated_fraction)`` (fracción ausente = 0), acotado a [0, 1].
- Flags del enum del contrato, en su orden: ``clipping`` (IMU), ``packet_gap`` (huecos, muestras
  perdidas o reinicio), ``out_of_calibration`` (calibración caducada), ``saturated_adc`` (stretch),
  ``clock_uncertain``. Las dos de calibración y reloj las decide quien llama.

Cobertura 0 significa que no hay datos: quien llama emite ``no_data``.
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
    """``(muestras saturadas, muestras juzgadas)`` del chunk, o ``None`` si no hay con qué juzgarlo."""
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
    """Calidad calculada por el engine para un conjunto de segmentos."""

    coverage: float
    saturated_fraction: float | None
    score: float
    flags: tuple[QualityFlag, ...]

    def to_quality(self) -> Quality:
        """Objeto ``quality`` del contrato, con ``assessed_by="engine"``."""
        return Quality(score=self.score, flags=list(self.flags), assessed_by=AssessedBy.ENGINE)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def assess_quality(segments: Sequence[Segment], *, calibration_expired: bool = False, clock_uncertain: bool = False) -> QualityAssessment:
    """Evalúa cobertura, saturación, score y flags de los segmentos de una feature."""
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
