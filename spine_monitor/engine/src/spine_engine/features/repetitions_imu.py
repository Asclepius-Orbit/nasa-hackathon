"""IMU fallback for repetitions (#41): windows from the gyroscope when there are no valid events.

Gyroscope only (``gyro_x/y/z`` in deg/s): in microgravity the accelerometer does not see gravity and is useless
to see the flexion. Each repetition is a back-and-forth flexion, so on the dominant axis it leaves two
lobes of angular velocity of opposite sign (out, back). The algorithm, per segment (``boot_id``):

1. Samples of the segment in ``seq`` order; the instant of each one is ``t0_device_ms + i/sample_rate_hz``
      of its chunk. Non-finite ones are replaced by the axis median (no movement).
2. Each axis has its median subtracted (gyroscope bias; rest dominates) and the axis with the most
      energy is chosen (tie: x, then y, then z).
3. Centered moving average of ``SMOOTH_S`` and noise estimated with the MAD of the first differences (robust
      against the movement itself, which is smooth).
4. Lobes: contiguous runs of the same sign with ``|ω|`` above the low threshold and a peak above the high one. Thresholds
      relative to the segment maximum (``PEAK_FRACTION`` and ``EDGE_FRACTION``) with a noise floor
      (``NOISE_K_PEAK`` and ``NOISE_K_EDGE`` sigmas). Lobes of the same sign with peaks closer than
      ``MIN_PEAK_SEPARATION_S`` are merged (a lobe split by noise).
5. Out->back pairing: a lobe of sign ``s`` immediately followed by one of sign ``-s``. Both
      ``s = +1`` and ``s = -1`` are tried and the one with more pairs wins (tie: smaller total gap inside the pairs; then
      ``+1``). A lobe without a partner (e.g. a single peak) is discarded.

Several boots: each segment is detected separately (samples of two ``boot_id`` are never joined) and the
windows are sorted by time with consecutive indices; rest goes from each end to the next start.
Several IMU streams: the first by ``stream_id`` that yields any window is used. Deterministic and non-raising:
whatever is incoherent (no IMU, no clock anchor, no signal) gives ``()``.
"""

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from spine_contracts import ImuChunk, StreamKind

from spine_engine.features.context import ExtractionContext
from spine_engine.features.ordering import Segment
from spine_engine.features.repetitions import RepWindow
from spine_engine.features.signals import segment_samples

# Moving average: attenuates noise without blurring lobes of ~1 s or more.
SMOOTH_S = 0.1
# A lobe counts if its peak reaches this fraction of the segment maximum...
PEAK_FRACTION = 0.25
# ...and its edges reach where |ω| drops below this fraction (the lower, the less the rep is trimmed).
EDGE_FRACTION = 0.03
# Noise floors, in sigmas of the smoothed noise: neither the peak nor the edge is mistaken for noise.
NOISE_K_PEAK = 8.0
NOISE_K_EDGE = 3.0
# Two peaks of the same sign closer than this are the same lobe.
MIN_PEAK_SEPARATION_S = 0.25
# Lobe edges already extend toward rest: do not merge neighboring
# repetitions with a short pause just because the edges end up close.
MIN_REP_GAP_S = 0.1
# A pause between repetitions longer than this times the median of the pauses is a rest between sets.
SET_GAP_FACTOR = 5.0
# MAD -> sigma for Gaussian noise; differences of white noise have sigma·√2.
_MAD_TO_SIGMA = 1.4826
_GYRO_AXES = ("gyro_x", "gyro_y", "gyro_z")


@dataclass(frozen=True, slots=True)
class _Lobe:
    first: int
    last: int
    sign: int
    peak: int
    peak_value: float


def detect_imu_windows(context: ExtractionContext) -> tuple[RepWindow, ...]:
    """Repetition windows from the gyroscope of the first IMU stream (by ``stream_id``) that yields them."""
    imu_streams = sorted(s.stream_id for s in context.session.streams if s.stream_kind is StreamKind.IMU)
    for stream_id in imu_streams:
        if context.stream_missing(stream_id):
            continue
        spans = [span for segment in context.segments(stream_id) for span in _segment_spans(context, segment)]
        if spans:
            return _windows(sorted(spans))
    return ()


def _windows(spans: Sequence[tuple[datetime, datetime]]) -> tuple[RepWindow, ...]:
    last = len(spans) - 1
    gaps = [(spans[i + 1][0] - spans[i][1]).total_seconds() for i in range(last)]
    # without events there is no set_*: a pause much longer than the usual one between repetitions is the rest between sets
    typical = statistics.median(gaps) if gaps else 0.0
    between_sets = [typical > 0.0 and gap > SET_GAP_FACTOR * typical for gap in gaps]
    return tuple(
        RepWindow(
            index=i + 1,
            start=start,
            end=end,
            rest_start=end if i < last and not between_sets[i] else None,
            rest_end=spans[i + 1][0] if i < last and not between_sets[i] else None,
        )
        for i, (start, end) in enumerate(spans)
    )


def _segment_spans(context: ExtractionContext, segment: Segment) -> list[tuple[datetime, datetime]]:
    chunks = [c for c in segment.chunks if isinstance(c, ImuChunk)]
    if not chunks:
        return []
    samples = segment_samples(context.clock, segment, _GYRO_AXES)
    rate = chunks[0].sample_rate_hz
    signal = _dominant_axis([samples.channels[name].tolist() for name in _GYRO_AXES])
    if signal is None:
        return []
    spans: list[tuple[datetime, datetime]] = []
    for out, back in _pairs(_lobes(signal, rate)):
        start, end = samples.times[out.first], samples.ends[back.last]
        if end > start:
            spans.append((start, end))
    return _merge_touching(spans)


def _merge_touching(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """A repetition that noise splits into two back-and-forth pairs stuck together is a single one: without rest between them there are not two."""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in spans:
        if merged and (start - merged[-1][1]).total_seconds() < MIN_REP_GAP_S:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _dominant_axis(axes: Sequence[list[float]]) -> list[float] | None:
    best: list[float] | None = None
    best_energy = 0.0
    for axis in axes:
        finite = [x for x in axis if math.isfinite(x)]
        if not finite:
            continue
        center = statistics.median(finite)
        centered = [x - center if math.isfinite(x) else 0.0 for x in axis]
        energy = math.fsum(x * x for x in centered)
        if energy > best_energy:
            best, best_energy = centered, energy
    return best


def _smooth(signal: Sequence[float], width: int) -> list[float]:
    half = width // 2
    prefix = [0.0]
    for x in signal:
        prefix.append(prefix[-1] + x)
    n = len(signal)
    out: list[float] = []
    for i in range(n):
        lo, hi = max(i - half, 0), min(i + half + 1, n)
        out.append((prefix[hi] - prefix[lo]) / (hi - lo))
    return out


def _noise_sigma(signal: Sequence[float]) -> float:
    diffs = [abs(b - a) for a, b in zip(signal, signal[1:], strict=False)]
    if not diffs:
        return 0.0
    return _MAD_TO_SIGMA * statistics.median(diffs) / math.sqrt(2.0)


def _lobes(signal: Sequence[float], rate: float) -> list[_Lobe]:
    width = max(1, round(SMOOTH_S * rate)) | 1  # impar: ventana centrada
    smoothed = _smooth(signal, width)
    peak = max(abs(x) for x in smoothed)
    if peak <= 0.0:
        return []
    sigma = _noise_sigma(signal) / math.sqrt(width)
    high = max(PEAK_FRACTION * peak, NOISE_K_PEAK * sigma)
    low = max(EDGE_FRACTION * peak, NOISE_K_EDGE * sigma)
    lobes: list[_Lobe] = []
    i, n = 0, len(smoothed)
    while i < n:
        sign = 1 if smoothed[i] > 0 else -1
        if abs(smoothed[i]) <= low:
            i += 1
            continue
        j = i
        while j + 1 < n and abs(smoothed[j + 1]) > low and (smoothed[j + 1] > 0) == (sign > 0):
            j += 1
        top = max(range(i, j + 1), key=lambda k: abs(smoothed[k]))
        if abs(smoothed[top]) >= high:
            lobes.append(_Lobe(_descend(smoothed, i, -1, sign), _descend(smoothed, j, 1, sign), sign, top, abs(smoothed[top])))
        i = j + 1
    return _merge_close(lobes, MIN_PEAK_SEPARATION_S * rate)


def _descend(smoothed: Sequence[float], k: int, step: int, sign: int) -> int:
    """Extends the edge while |ω| keeps decreasing without changing sign: recovers the almost still tail of the lobe."""
    n = len(smoothed)
    while 0 <= k + step < n and sign * smoothed[k + step] > 0 and abs(smoothed[k + step]) < abs(smoothed[k]):
        k += step
    return k


def _merge_close(lobes: Sequence[_Lobe], min_separation: float) -> list[_Lobe]:
    merged: list[_Lobe] = []
    for lobe in lobes:
        previous = merged[-1] if merged else None
        if previous is not None and previous.sign == lobe.sign and lobe.peak - previous.peak < min_separation:
            stronger = previous if previous.peak_value >= lobe.peak_value else lobe
            merged[-1] = _Lobe(previous.first, lobe.last, lobe.sign, stronger.peak, stronger.peak_value)
        else:
            merged.append(lobe)
    return merged


def _pairs(lobes: Sequence[_Lobe]) -> list[tuple[_Lobe, _Lobe]]:
    best: list[tuple[_Lobe, _Lobe]] = []
    best_gap = math.inf
    for out_sign in (1, -1):
        pairs: list[tuple[_Lobe, _Lobe]] = []
        i = 0
        while i + 1 < len(lobes):
            if lobes[i].sign == out_sign and lobes[i + 1].sign == -out_sign:
                pairs.append((lobes[i], lobes[i + 1]))
                i += 2
            else:
                i += 1
        gap = sum(back.first - out.last for out, back in pairs)
        if len(pairs) > len(best) or (len(pairs) == len(best) and pairs and gap < best_gap):
            best, best_gap = pairs, gap
    return best
