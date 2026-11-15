"""Quality faults (on sample arrays + quality per chunk) and device faults (plans).

- `plan_quality_faults`: probabilistic episodes per flag (per-chunk probability of starting,
  uniform duration in `duration_ms`), only for the flag's `kinds`. probability 0 => none;
  probability 1 => at least one episode starting at the first chunk.
- `apply_quality_faults`: alters the signal of each episode and returns the quality per chunk (flag + minimum
  score of what touches it). Semantics of `amplitude` (relative to the channel's clean signal in the chunk):
  clipping: clip to ±amplitude × peak | lead_off: signal replaced by noise of amplitude × RMS |
  motion_artifact: low-frequency burst (`motion_artifact` band) of amplitude × RMS | powerline_noise: sine of
  `frequency_hz` of amplitude × RMS | low_snr: white noise of amplitude × RMS | saturated_adc: counts
  pinned to the ADC maximum (or the channel range) | out_of_calibration: offset of amplitude × RMS |
  sensor_warmup: slow decaying drift, of amplitude × RMS.
  On the cord only `adc_counts` are altered (integers, clipped to the ADC); since length_mm is derived from them,
  its quality inherits that of adc_counts.
- Device plans: restarts (Poisson per hour), chunk losses in bursts, clock drift
  per boot and reception latency (received_at jitter).
`QualityState`, `CLEAN`, `QUALITY_KEYS` and the merge utilities are the common base for all modules.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from spine_contracts import Quality, QualityFlag, StreamKind
from spine_contracts.models.common_schema import AssessedBy

from spine_physiology.config import ClockFaultConfig, PacketLossConfig, QualityFaultsConfig, RebootConfig, SignalsConfig

# `quality` keys of each chunk type, in the contract's order.
QUALITY_KEYS: dict[StreamKind, tuple[str, ...]] = {
    StreamKind.EMG: ("emg",),
    StreamKind.IMU: ("accel", "gyro"),
    StreamKind.STRETCH: ("adc_counts", "length_mm"),
}


def quality_key(kind: StreamKind, channel: str) -> str:
    """Quality key of a data channel: accel_x -> accel, gyro_z -> gyro, resistance_ohm -> adc_counts..."""
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
        """The worse of the two: minimum score and union of flags."""
        return QualityState(min(self.score, other.score), self.flags | other.flags)

    def to_contract(self) -> Quality:
        """Contract quality, assessed by the gateway; flags in enum order."""
        flags = [f for f in QualityFlag if f in self.flags]
        return Quality(score=self.score, flags=flags, assessed_by=AssessedBy.GATEWAY)


CLEAN = QualityState(1.0, frozenset())

# Quality of a chunk: QUALITY_KEYS key -> state.
type ChunkQuality = dict[str, QualityState]


def clean_quality(kind: StreamKind, n_chunks: int) -> list[ChunkQuality]:
    return [dict.fromkeys(QUALITY_KEYS[kind], CLEAN) for _ in range(n_chunks)]


def merge_quality(a: Sequence[ChunkQuality], b: Sequence[ChunkQuality]) -> list[ChunkQuality]:
    """Chunk-by-chunk merge (same length); keys missing on one side are taken from the other."""
    if len(a) != len(b):
        raise ValueError(f"quality sequences differ in length: {len(a)} != {len(b)}")
    out: list[ChunkQuality] = []
    for qa, qb in zip(a, b, strict=True):
        merged = dict(qa)
        for key, state in qb.items():
            merged[key] = merged[key].merge(state) if key in merged else state
        out.append(merged)
    return out


def _n_chunks(n_samples: int, samples_per_chunk: int) -> int:
    """Chunks needed for `n_samples` (the last may be partial)."""
    return -(-n_samples // samples_per_chunk) if n_samples > 0 else 0


def _chunks_hit(mask: np.ndarray, samples_per_chunk: int) -> np.ndarray:
    """Bool per chunk: some sample of the mask falls inside."""
    if mask.size == 0:
        return np.zeros(0, dtype=bool)
    starts = np.arange(0, mask.size, samples_per_chunk)
    return np.add.reduceat(mask.astype(np.int64), starts) > 0


def mask_quality(
    mask: np.ndarray,
    *,
    kind: StreamKind,
    key: str,
    flag: QualityFlag,
    score: float,
    samples_per_chunk: int,
) -> list[ChunkQuality]:
    """Quality per chunk from a per-sample mask (e.g. the cord's band_recovering):
    chunks with some marked sample carry `flag` and `score` in `key`; the rest, CLEAN.
    """
    if key not in QUALITY_KEYS[kind]:
        raise ValueError(f"quality key {key!r} not valid for {kind.value} (expected one of {QUALITY_KEYS[kind]})")
    state = QualityState(score, frozenset({flag}))
    out: list[ChunkQuality] = []
    for hit in _chunks_hit(np.asarray(mask, dtype=bool), samples_per_chunk):
        chunk_quality = dict.fromkeys(QUALITY_KEYS[kind], CLEAN)
        if hit:
            chunk_quality[key] = state
        out.append(chunk_quality)
    return out


@dataclass(frozen=True, slots=True)
class FaultEpisode:
    """Episode of an injected quality fault, in samples from the start of the array (exclusive end)."""

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
    """Episodes of all flags applicable to `kind`, ordered by start."""
    n_chunks = _n_chunks(n_samples, samples_per_chunk)
    episodes: list[FaultEpisode] = []
    for flag, cfg in faults.items():
        if kind not in cfg.kinds:
            continue
        # Each chunk has `probability` of starting an episode; candidates falling inside an
        # ongoing episode are discarded (a flag never overlaps itself).
        candidates = np.flatnonzero(rng.random(n_chunks) < cfg.probability)
        pos = 0
        while pos < candidates.size:
            chunk = int(candidates[pos])
            duration_ms = float(rng.uniform(cfg.duration_ms.min, cfg.duration_ms.max))
            length = max(1, math.ceil(duration_ms * rate_hz / 1000))
            start = chunk * samples_per_chunk
            end = min(n_samples, start + length)
            episodes.append(FaultEpisode(flag, stream_id, start, end, cfg.score, cfg.amplitude))
            pos = int(np.searchsorted(candidates, _n_chunks(end, samples_per_chunk), side="left"))
    episodes.sort(key=lambda e: e.start_sample)
    return tuple(episodes)


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
    """(altered channels — copies, never in place —, quality per chunk with all QUALITY_KEYS[kind]).

    `channels`: emg -> {"emg"}; imu -> the 6 of IMU_CHANNELS; stretch -> {"adc_counts"} (int64).
    Without episodes it returns identical copies and `clean_quality`.

    `saturated_adc` ignores `amplitude`: pins the signal to full scale (maximum ADC counts of the
    cord; `accel_range_g` / `gyro_range_dps` on the IMU; the channel peak on the EMG).
    """
    lengths = {len(arr) for arr in channels.values()}
    if len(lengths) != 1:
        raise ValueError(f"channels must have the same length, got {sorted(lengths)}")
    n = lengths.pop()
    out = {name: np.array(arr, copy=True) for name, arr in channels.items()}
    quality = clean_quality(kind, _n_chunks(n, samples_per_chunk))
    if not episodes or n == 0:
        return out, quality

    centered = kind is StreamKind.STRETCH
    work = {name: np.asarray(arr, dtype=np.float64).copy() for name, arr in channels.items()}
    # Clean-signal references per chunk (RMS, peak, mean), computed before altering anything.
    stats = {name: _chunk_stats(arr, samples_per_chunk, centered) for name, arr in work.items()}

    for episode in episodes:
        start, end = episode.start_sample, min(episode.end_sample, n)
        if end <= start:
            continue
        chunk_index = np.arange(start, end) // samples_per_chunk
        for name, signal in work.items():
            rms, peak, mean = stats[name]
            _apply_episode(
                rng, episode, kind=kind, channel=name, segment=signal[start:end], start=start,
                ref_rms=rms[chunk_index], ref_peak=peak[chunk_index], ref_mean=mean[chunk_index],
                rate_hz=rate_hz, signals=signals, faults=faults,
            )  # fmt: skip
        state = QualityState(episode.score, frozenset({episode.flag}))
        first, last = start // samples_per_chunk, (end - 1) // samples_per_chunk
        for key in QUALITY_KEYS[kind]:
            for c in range(first, last + 1):
                quality[c][key] = quality[c][key].merge(state)

    for name, signal in work.items():
        original = channels[name]
        if kind is StreamKind.STRETCH and name == "adc_counts":
            adc_max = (1 << signals.stretch.circuit.adc_bits) - 1
            out[name] = np.clip(np.rint(signal), 0, adc_max).astype(original.dtype)
        elif np.issubdtype(original.dtype, np.integer):
            out[name] = np.rint(signal).astype(original.dtype)
        else:
            out[name] = signal.astype(original.dtype)
    if kind is StreamKind.IMU:
        # Faults (offset, noise, artifacts) can push the IMU out of the sensor range: clip to full scale.
        for name in out:
            limit = signals.imu.accel_range_g if name.startswith("accel") else signals.imu.gyro_range_dps
            np.clip(out[name], -limit, limit, out=out[name])
    return out, quality


def _chunk_stats(x: np.ndarray, samples_per_chunk: int, centered: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(RMS, peak, mean) of the clean signal per chunk. `centered`: RMS with respect to the chunk mean
    (cord, whose counts carry a large DC level). A chunk without energy uses that of the whole
    channel and, if there is none either, 1.0 (nominal channel unit) so the fault always alters something.
    """
    n = x.size
    starts = np.arange(0, n, samples_per_chunk)
    counts = np.diff(np.append(starts, n))
    mean = np.add.reduceat(x, starts) / counts
    mean_sq = np.add.reduceat(x * x, starts) / counts
    rms = np.sqrt(np.maximum(mean_sq - mean * mean if centered else mean_sq, 0.0))
    peak = np.maximum.reduceat(np.abs(x), starts)
    global_rms = float(np.sqrt(max(np.mean((x - x.mean()) ** 2 if centered else x * x), 0.0)))
    global_peak = float(np.max(np.abs(x)))
    rms = np.where(rms > 0, rms, global_rms if global_rms > 0 else 1.0)
    peak = np.where(peak > 0, peak, global_peak if global_peak > 0 else 1.0)
    return rms, peak, mean


def _apply_episode(
    rng: np.random.Generator,
    episode: FaultEpisode,
    *,
    kind: StreamKind,
    channel: str,
    segment: np.ndarray,
    start: int,
    ref_rms: np.ndarray,
    ref_peak: np.ndarray,
    ref_mean: np.ndarray,
    rate_hz: float,
    signals: SignalsConfig,
    faults: QualityFaultsConfig,
) -> None:
    """Alters `segment` (view of the working channel) in place according to the episode's flag."""
    m = segment.size
    amp = episode.amplitude
    t = np.arange(m) / rate_hz  # seconds since the start of the episode
    flag = episode.flag
    if flag is QualityFlag.CLIPPING:
        limit = amp * ref_peak
        np.clip(segment, -limit, limit, out=segment)
    elif flag is QualityFlag.LEAD_OFF:
        centre = ref_mean if kind is StreamKind.STRETCH else 0.0
        segment[:] = centre + rng.normal(0.0, 1.0, m) * amp * ref_rms
    elif flag is QualityFlag.MOTION_ARTIFACT:
        # Low-frequency burst (< 20 Hz): three sines with a sine window (rises and falls).
        cfg = faults.motion_artifact
        top = min(cfg.band_high_hz, cfg.max_fraction_of_rate * rate_hz)
        freqs = rng.uniform(min(cfg.band_low_hz, top / 2), top, 3)
        phases = rng.uniform(0.0, 2 * math.pi, 3)
        shape = sum(np.sin(2 * math.pi * f * t + p) for f, p in zip(freqs, phases, strict=True)) * math.sqrt(2 / 3)
        segment += amp * ref_rms * shape * np.sin(math.pi * (np.arange(m) + 0.5) / m)
    elif flag is QualityFlag.POWERLINE_NOISE:
        t_abs = (start + np.arange(m)) / rate_hz  # continuous phase across chunks
        phase = rng.uniform(0.0, 2 * math.pi)
        segment += amp * ref_rms * np.sin(2 * math.pi * faults.powerline_noise.frequency_hz * t_abs + phase)
    elif flag is QualityFlag.LOW_SNR:
        segment += rng.normal(0.0, 1.0, m) * amp * ref_rms
    elif flag is QualityFlag.SATURATED_ADC:
        segment[:] = _full_scale(kind, channel, signals, ref_peak)
    elif flag is QualityFlag.OUT_OF_CALIBRATION:
        segment += rng.choice((-1.0, 1.0)) * amp * ref_rms
    elif flag is QualityFlag.SENSOR_WARMUP:
        tau = max(m / rate_hz * faults.sensor_warmup.decay_fraction, 1 / rate_hz)
        segment += rng.choice((-1.0, 1.0)) * amp * ref_rms * np.exp(-t / tau)
    else:
        raise ValueError(f"flag {flag.value} is not an injectable quality fault")


def _full_scale(kind: StreamKind, channel: str, signals: SignalsConfig, ref_peak: np.ndarray) -> float | np.ndarray:
    if kind is StreamKind.STRETCH:
        return float((1 << signals.stretch.circuit.adc_bits) - 1)
    if kind is StreamKind.IMU:
        return float(signals.imu.accel_range_g if channel.startswith("accel") else signals.imu.gyro_range_dps)
    return ref_peak


@dataclass(frozen=True, slots=True)
class RebootEvent:
    """Spontaneous restart: the device drops at `at` and returns (new boot) after `offline_s`."""

    at: datetime
    offline_s: float


def plan_reboots(rng: np.random.Generator, cfg: RebootConfig, *, start: datetime, end: datetime) -> tuple[RebootEvent, ...]:
    """Poisson restarts in [start, end), ordered and non-overlapping (each after the previous one returns)."""
    if cfg.probability_per_hour <= 0:
        return ()
    mean_gap_s = 3600.0 / cfg.probability_per_hour
    events: list[RebootEvent] = []
    at = start
    while True:
        at = at + timedelta(seconds=float(rng.exponential(mean_gap_s)))
        if at >= end:
            break
        offline_s = float(rng.uniform(cfg.offline_s.min, cfg.offline_s.max))
        events.append(RebootEvent(at=at, offline_s=offline_s))
        at = at + timedelta(seconds=offline_s)
    return tuple(events)


def plan_packet_loss(rng: np.random.Generator, cfg: PacketLossConfig, n_chunks: int) -> np.ndarray:
    """Bool mask per chunk (True = lost): bursts of `burst_chunks` starting with `probability`."""
    lost = np.zeros(n_chunks, dtype=bool)
    candidates = np.flatnonzero(rng.random(n_chunks) < cfg.probability)
    pos = 0
    while pos < candidates.size:
        chunk = int(candidates[pos])
        burst = int(rng.integers(cfg.burst_chunks.min, cfg.burst_chunks.max + 1))
        lost[chunk : chunk + burst] = True
        pos = int(np.searchsorted(candidates, chunk + burst, side="left"))
    return lost


def sample_drift_ppm(rng: np.random.Generator, cfg: ClockFaultConfig) -> float:
    """Device clock drift for a boot (uniform in `drift_ppm`)."""
    return float(rng.uniform(cfg.drift_ppm.min, cfg.drift_ppm.max))


def sample_latency_ms(rng: np.random.Generator, cfg: ClockFaultConfig, n: int) -> np.ndarray:
    """Reception latency of `n` chunks (uniform in `latency_ms`), float64."""
    return rng.uniform(cfg.latency_ms.min, cfg.latency_ms.max, n).astype(np.float64)
