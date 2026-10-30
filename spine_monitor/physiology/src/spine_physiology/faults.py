"""Fallos de calidad (sobre arrays de muestras + quality por chunk) y fallos de dispositivo (planes).

Dueño: B4. Debe implementar:
- `plan_quality_faults`: episodios probabilísticos por flag (probabilidad por chunk de empezar,
  duración uniforme en `duration_ms`), solo para los `kinds` del flag. probability 0 ⇒ ninguno;
  probability 1 ⇒ al menos un episodio que empieza en el primer chunk.
- `apply_quality_faults`: altera la señal de cada episodio y devuelve la quality por chunk (flag + score
  mínimo de lo que lo toque). Semántica de `amplitude` (relativa a la señal limpia del canal en el chunk):
  clipping: recorte a ±amplitude × pico | lead_off: señal sustituida por ruido de amplitude × RMS |
  motion_artifact: ráfaga de baja frecuencia (< 20 Hz) de amplitude × RMS | powerline_noise: seno de
  `frequency_hz` de amplitude × RMS | low_snr: ruido blanco de amplitude × RMS | saturated_adc: cuentas
  clavadas al máximo del ADC (o al rango del canal) | out_of_calibration: offset de amplitude × RMS |
  sensor_warmup: deriva lenta que decae, de amplitude × RMS.
  En el cordón solo se alteran `adc_counts` (enteros, recortados al ADC); como length_mm se deriva de ellas,
  su quality hereda la de adc_counts.
- Planes de dispositivo: reinicios (Poisson por hora), pérdidas de chunks en ráfagas, deriva de reloj
  por arranque y latencia de recepción (jitter de received_at).
`QualityState`, `CLEAN`, `QUALITY_KEYS` y las utilidades de merge ya están implementadas.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from spine_contracts import Quality, QualityFlag, StreamKind

from spine_physiology.config import ClockFaultConfig, PacketLossConfig, QualityFaultsConfig, RebootConfig, SignalsConfig

# Claves de `quality` de cada tipo de chunk, en el orden del contrato.
QUALITY_KEYS: dict[StreamKind, tuple[str, ...]] = {
    StreamKind.EMG: ("emg",),
    StreamKind.IMU: ("accel", "gyro"),
    StreamKind.STRETCH: ("adc_counts", "length_mm"),
}


def quality_key(kind: StreamKind, channel: str) -> str:
    """Clave de quality de un canal de datos: accel_x -> accel, gyro_z -> gyro, resistance_ohm -> adc_counts..."""
    if kind is StreamKind.IMU:
        return channel.split("_", 1)[0]
    if kind is StreamKind.STRETCH:
        return "length_mm" if channel == "length_mm" else "adc_counts"
    return "emg"


@dataclass(frozen=True, slots=True)
class QualityState:
    score: float
    flags: frozenset[QualityFlag]

    def merge(self, other: QualityState) -> QualityState:
        """Peor de los dos: score mínimo y unión de flags."""
        return QualityState(min(self.score, other.score), self.flags | other.flags)

    def to_contract(self) -> Quality:
        """Quality del contrato, evaluada por el gateway; flags en el orden del enum."""
        flags = [f for f in QualityFlag if f in self.flags]
        return Quality(score=self.score, flags=flags, assessed_by="gateway")


CLEAN = QualityState(1.0, frozenset())

# Quality de un chunk: clave de QUALITY_KEYS -> estado.
type ChunkQuality = dict[str, QualityState]


def clean_quality(kind: StreamKind, n_chunks: int) -> list[ChunkQuality]:
    return [dict.fromkeys(QUALITY_KEYS[kind], CLEAN) for _ in range(n_chunks)]


def merge_quality(a: Sequence[ChunkQuality], b: Sequence[ChunkQuality]) -> list[ChunkQuality]:
    """Merge chunk a chunk (misma longitud); claves ausentes en un lado se toman del otro."""
    if len(a) != len(b):
        raise ValueError(f"quality sequences differ in length: {len(a)} != {len(b)}")
    out: list[ChunkQuality] = []
    for qa, qb in zip(a, b, strict=True):
        merged = dict(qa)
        for key, state in qb.items():
            merged[key] = merged[key].merge(state) if key in merged else state
        out.append(merged)
    return out


def mask_quality(
    mask: np.ndarray,
    *,
    kind: StreamKind,
    key: str,
    flag: QualityFlag,
    score: float,
    samples_per_chunk: int,
) -> list[ChunkQuality]:
    """Quality por chunk a partir de una máscara por muestra (p. ej. band_recovering del cordón):
    los chunks con alguna muestra marcada llevan `flag` y `score` en `key`; el resto, CLEAN.
    """
    raise NotImplementedError


@dataclass(frozen=True, slots=True)
class FaultEpisode:
    """Episodio de un fallo de calidad inyectado, en muestras desde el inicio del array (fin exclusivo)."""

    flag: QualityFlag
    stream_id: str
    start_sample: int
    end_sample: int
    score: float
    amplitude: float


def plan_quality_faults(
    rng: np.random.Generator,
    faults: QualityFaultsConfig,
    *,
    stream_id: str,
    kind: StreamKind,
    n_samples: int,
    rate_hz: float,
    samples_per_chunk: int,
) -> tuple[FaultEpisode, ...]:
    """Episodios de todos los flags aplicables a `kind`, ordenados por inicio."""
    raise NotImplementedError


def apply_quality_faults(
    rng: np.random.Generator,
    episodes: Sequence[FaultEpisode],
    *,
    kind: StreamKind,
    channels: Mapping[str, np.ndarray],
    rate_hz: float,
    samples_per_chunk: int,
    signals: SignalsConfig,
    faults: QualityFaultsConfig,
) -> tuple[dict[str, np.ndarray], list[ChunkQuality]]:
    """(canales alterados — copias, nunca in place —, quality por chunk con todas las QUALITY_KEYS[kind]).

    `channels`: emg -> {"emg"}; imu -> los 6 de IMU_CHANNELS; stretch -> {"adc_counts"} (int64).
    Sin episodios devuelve copias idénticas y `clean_quality`.
    """
    raise NotImplementedError


@dataclass(frozen=True, slots=True)
class RebootEvent:
    """Reinicio espontáneo: el dispositivo cae en `at` y vuelve (boot nuevo) tras `offline_s`."""

    at: datetime
    offline_s: float


def plan_reboots(rng: np.random.Generator, cfg: RebootConfig, *, start: datetime, end: datetime) -> tuple[RebootEvent, ...]:
    """Reinicios Poisson en [start, end), ordenados y sin solaparse (cada uno tras volver del anterior)."""
    raise NotImplementedError


def plan_packet_loss(rng: np.random.Generator, cfg: PacketLossConfig, n_chunks: int) -> np.ndarray:
    """Máscara bool por chunk (True = perdido): ráfagas de `burst_chunks` que empiezan con `probability`."""
    raise NotImplementedError


def sample_drift_ppm(rng: np.random.Generator, cfg: ClockFaultConfig) -> float:
    """Deriva del reloj del dispositivo para un arranque (uniforme en `drift_ppm`)."""
    raise NotImplementedError


def sample_latency_ms(rng: np.random.Generator, cfg: ClockFaultConfig, n: int) -> np.ndarray:
    """Latencia de recepción de `n` chunks (uniforme en `latency_ms`), float64."""
    raise NotImplementedError
