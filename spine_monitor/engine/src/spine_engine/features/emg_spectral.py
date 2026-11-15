"""``emg_spectral`` family (#43): median EMG frequency per repetition and its slope across repetitions.

For each EMG stream declared with ``placement.muscle`` and ``side``:

- ``emg.{muscle}.{side}.median_freq_hz``: mean, over the valid repetitions, of the median frequency of the power
  spectrum (PSD) of the samples of the active phase of each repetition. The median is the frequency that splits the
  total power into two halves: it is interpolated linearly over the cumulative PSD. A spectrum without power (null
  signal) gives 0 Hz. Unit: Hz.
- ``emg.{muscle}.{side}.median_freq_slope_hz_per_rep``: slope of the least-squares linear fit of the
  median against the repetition index (``RepWindow.index``, that of the repetition, not its position among the
  valid ones; so a discarded repetition leaves a gap, like the simulator's ground truth). Unit: Hz/rep.
  A negative slope is fatigue: the median drops.

PSD: Welch with 1 s segments (``round(rate_hz)`` samples, 1 Hz resolution) or the whole repetition if it is
shorter, Hann window, 50 % overlap and subtraction of each segment's mean. It is pure numpy (``scipy`` is not a
dependency of the engine); the scale of the PSD does not affect the median.

A repetition is valid if its active phase contributes at least ``MIN_ACTIVE_SAMPLES`` samples: with fewer, the Hann
window and the spectral resolution (``rate_hz`` / samples) do not leave a meaningful median. If the active phase falls in
several segments of the stream, the longest piece is used (joining pieces from different boots would invent a spectrum).
``n`` is the number of valid repetitions. At least 2 valid repetitions are needed (D-60).

Only raw EMG is valid: rectification and the envelope destroy the spectrum. If any chunk of the stream is not
``raw``, everything comes out ``signal_not_raw``. Never raises (D-51).

``skipped`` reasons, in order of precedence: declared stream without chunks ``stream_missing``; any non-raw
chunk ``signal_not_raw``; no repetitions or fewer than 2 valid ``too_few_repetitions``; repetitions but no
samples in any of them ``no_data``.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

import numpy as np
from numpy.typing import NDArray
from spine_contracts import EmgChunk, StreamChunk
from spine_contracts.catalog import expand_template
from spine_contracts.models.domain.feature_set_schema import Reason

from spine_engine.features.context import ExtractionContext
from spine_engine.features.emg import _emg_streams, _EmgStream, _micros, _Piece, _pieces, _Read
from spine_engine.features.registry import ComputedFeature, FamilyResult, SkippedFeature
from spine_engine.features.repetitions import RepetitionService
from spine_engine.features.signals import segment_samples

MEDIAN_FREQ_HZ = "emg.{muscle}.{side}.median_freq_hz"
MEDIAN_FREQ_SLOPE = "emg.{muscle}.{side}.median_freq_slope_hz_per_rep"

# Minimum active samples of a valid repetition: 64 samples are 64 ms at 1 kHz, a resolution of ~16 Hz,
# the minimum for the Hann window to leave more than a handful of bins with power.
MIN_ACTIVE_SAMPLES = 64
_SEGMENT_SECONDS = 1.0  # duration of the Welch segment
_OVERLAP = 0.5  # overlap between Welch segments
_RAW = "raw"

type _Spans = list[tuple[int, tuple[datetime, datetime]]]  # (repetition index, active phase)


def _median_frequency_hz(values: NDArray[np.float64], rate_hz: float) -> float:
    """Median frequency of the Welch PSD (Hann, 50 % overlap, mean removed), 0 if there is no power."""
    size = min(values.size, max(2, round(_SEGMENT_SECONDS * rate_hz)))
    step = max(1, round(size * (1.0 - _OVERLAP)))
    window = np.hanning(size)
    psd = np.zeros(size // 2 + 1)
    for start in range(0, values.size - size + 1, step):
        segment = values[start : start + size]
        psd += np.abs(np.fft.rfft((segment - segment.mean()) * window)) ** 2
    cumulative = np.cumsum(psd)
    if cumulative[-1] <= 0.0:
        return 0.0
    return float(np.interp(cumulative[-1] / 2.0, cumulative, np.fft.rfftfreq(size, 1.0 / rate_hz)))


def _longest(pieces: Sequence[_Piece]) -> dict[int, _Piece]:
    """The longest piece of each repetition (window index)."""
    best: dict[int, _Piece] = {}
    for piece in pieces:
        if piece.window not in best or piece.values.size > best[piece.window].values.size:
            best[piece.window] = piece
    return best


def _spectral_reads(context: ExtractionContext, stream_id: str) -> tuple[_Read, ...]:
    """Split at gaps and rate changes so Welch only sees contiguous, uniformly sampled data."""
    reads: list[_Read] = []
    for segment in context.segments(stream_id):
        groups: list[list[StreamChunk]] = []
        for chunk in segment.chunks:
            previous = groups[-1][-1] if groups else None
            if (
                previous is None
                or chunk.seq != previous.seq + 1
                or chunk.sample_rate_hz != previous.sample_rate_hz
                or (chunk.dropped_samples or 0)
                or abs(chunk.t0_device_ms - (previous.t0_device_ms + previous.n_samples * 1000 / previous.sample_rate_hz)) > 1
            ):
                groups.append([])
            groups[-1].append(chunk)
        for group in groups:
            samples = segment_samples(context.clock, replace(segment, chunks=tuple(group)), ("emg",))
            micros = np.fromiter((_micros(t) for t in samples.times), dtype=np.int64, count=len(samples.times))
            reads.append(_Read(micros, samples.channels["emg"], group[0].sample_rate_hz))
    return tuple(reads)


class EmgSpectralFamily:
    """Median EMG frequency from the windows of the injected repetitions service.

    ``feature_ids`` are all the concrete catalog ids; in each request only those of the EMG streams
    declared in the session are emitted.
    """

    family = "emg_spectral"
    feature_ids: Sequence[str] = tuple(i for template in (MEDIAN_FREQ_HZ, MEDIAN_FREQ_SLOPE) for i in expand_template(template))

    def __init__(self, repetitions: RepetitionService) -> None:
        self._repetitions = repetitions

    def extract(self, context: ExtractionContext) -> FamilyResult:
        windows = self._repetitions.windows(context).windows
        computed: list[ComputedFeature] = []
        skipped: list[SkippedFeature] = []
        for stream in _emg_streams(context):
            ids = (
                MEDIAN_FREQ_HZ.format(muscle=stream.muscle, side=stream.side),
                MEDIAN_FREQ_SLOPE.format(muscle=stream.muscle, side=stream.side),
            )
            outcome = self._stream(context, stream, [(w.index, w.active) for w in windows])
            if isinstance(outcome, Reason):
                skipped.extend(SkippedFeature(feature_id, outcome) for feature_id in ids)
                continue
            indexes, medians = outcome
            used = (stream.stream_id,)
            computed.append(ComputedFeature(ids[0], float(np.mean(medians)), used, len(medians)))
            computed.append(ComputedFeature(ids[1], float(np.polyfit(indexes, medians, 1)[0]), used, len(medians)))
        return FamilyResult(tuple(computed), tuple(skipped))

    @staticmethod
    def _stream(context: ExtractionContext, stream: _EmgStream, spans: _Spans) -> Reason | tuple[list[int], list[float]]:
        """Indices and medians of the stream's valid repetitions, or the reason there are none."""
        if context.stream_missing(stream.stream_id):
            return Reason.STREAM_MISSING
        kinds = {c.signal_kind.value for s in context.segments(stream.stream_id) for c in s.chunks if isinstance(c, EmgChunk)}
        if kinds != {_RAW}:
            return Reason.SIGNAL_NOT_RAW
        if not spans:
            return Reason.TOO_FEW_REPETITIONS
        pieces = [p for p in _pieces(_spectral_reads(context, stream.stream_id), [span for _, span in spans]) if np.isfinite(p.values).all()]
        if not pieces:
            return Reason.NO_DATA
        valid = sorted((spans[window][0], piece) for window, piece in _longest(pieces).items() if piece.values.size >= MIN_ACTIVE_SAMPLES)
        if len(valid) < 2:
            return Reason.TOO_FEW_REPETITIONS
        return [index for index, _ in valid], [_median_frequency_hz(p.values, p.rate_hz) for _, p in valid]
