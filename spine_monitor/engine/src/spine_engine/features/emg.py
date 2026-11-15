"""``emg`` amplitude family (#42): active and rest RMS, %MVC, windowed peak and left/right symmetry.

For each EMG stream declared with ``placement.muscle`` and ``side``:

- ``emg.{muscle}.{side}.rms_mv``: RMS of the samples of the active phase of all repetitions.
- ``emg.{muscle}.{side}.rest_rms_mv``: RMS of the samples of the rests of the repetitions.
- ``emg.{muscle}.{side}.rms_pct_mvc``: ``rms_mv`` as a % of ``mvc_rms_mv`` from ``calibration.emg-mvc``.
- ``emg.{muscle}.{side}.peak_pct_mvc``: the highest windowed RMS (``window_ms`` of the calibration) as a % of the MVC.
  Windows do not cross from one repetition to another nor from one ``boot_id`` to another; if a repetition has fewer samples
  than the window, all its samples form a single window.

And per muscle, ``emg.{muscle}.symmetry_index_pct`` = 100·|L-R| / ((L+R)/2) over the ``rms_pct_mvc`` of the two sides.

Amplitude does not need raw signal: the computation is the same on raw, rectified or envelope; the value is the same
for raw and rectified, not for the envelope. That is why the MVC is only used if it is for the same muscle and side as the
stream and was measured on a signal type compatible with that of all its chunks (raw and rectified with each other; envelope
only with envelope); otherwise, whatever carries %MVC comes out ``no_calibration`` and ``rms_mv``/``rest_rms_mv`` are computed anyway.
Two streams declared with the same muscle and side (unhandled edge case): each emits its features with the same
id (the composite keeps the first one computed) and the symmetry always uses the first. The samples of
each window come from ``segment_samples`` (half-open UTC window ``start <= t < end``). ``n`` is the number of
repetitions that contributed samples. Never raises.

``skipped`` reasons: no calibration ``no_calibration`` and expired ``calibration_expired`` (only affect whatever
carries %MVC); no repetitions ``too_few_repetitions``; no samples in the windows ``no_data``; declared stream without
chunks ``stream_missing``. Symmetry needs the MVC of both sides: if either is missing, ``no_calibration``
(even if the other is expired); a muscle with only one side declared, ``stream_missing``; null ``(L+R)/2``, ``no_data``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from numpy.typing import NDArray
from spine_contracts import EmgChunk, EmgMvcCalibration, StreamKind
from spine_contracts.catalog import expand_template
from spine_contracts.models.domain.feature_set_schema import Reason

from spine_engine.features.context import ExtractionContext
from spine_engine.features.registry import ComputedFeature, FamilyResult, SkippedFeature
from spine_engine.features.repetitions import RepetitionService, RepWindow
from spine_engine.features.signals import segment_samples

RMS_MV = "emg.{muscle}.{side}.rms_mv"
REST_RMS_MV = "emg.{muscle}.{side}.rest_rms_mv"
RMS_PCT_MVC = "emg.{muscle}.{side}.rms_pct_mvc"
PEAK_PCT_MVC = "emg.{muscle}.{side}.peak_pct_mvc"
SYMMETRY_INDEX_PCT = "emg.{muscle}.symmetry_index_pct"

_CHANNEL = "emg"
# Signal types with the same RMS: raw and rectified (squaring erases the sign); the envelope is separate.
_SIGNAL_GROUP = {"raw": "rms", "rectified": "rms", "envelope": "envelope"}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)
_TEMPLATES = {"rms_mv": RMS_MV, "rest_rms_mv": REST_RMS_MV, "rms_pct_mvc": RMS_PCT_MVC, "peak_pct_mvc": PEAK_PCT_MVC}


@dataclass(frozen=True, slots=True)
class _Piece:
    """Samples of a repetition (``window``) within a segment, with its sampling rate."""

    window: int
    values: NDArray[np.float64]
    rate_hz: float


@dataclass(frozen=True, slots=True)
class _EmgStream:
    stream_id: str
    muscle: str
    side: str


@dataclass(frozen=True, slots=True)
class _Pct:
    """``rms_pct_mvc`` of one side and the repetitions that back it."""

    value: float
    n: int


def _emg_streams(context: ExtractionContext) -> tuple[_EmgStream, ...]:
    found: dict[str, _EmgStream] = {}
    for declared in context.session.streams:
        muscle = getattr(declared.placement, "muscle", None)
        side = getattr(declared.placement, "side", None)
        if declared.stream_kind is StreamKind.EMG and muscle is not None and side is not None:
            found.setdefault(declared.stream_id, _EmgStream(declared.stream_id, muscle.value, side.value))
    return tuple(found.values())


@dataclass(frozen=True, slots=True)
class _Read:
    """Samples of a segment read once: UTC instant in integer microseconds and value."""

    micros: NDArray[np.int64]
    values: NDArray[np.float64]
    rate_hz: float


def _micros(instant: datetime) -> int:
    """UTC microseconds since the epoch, as integers: compares the same as ``datetime`` (no float rounding)."""
    return (instant - _EPOCH) // _MICROSECOND


def _read(context: ExtractionContext, stream_id: str) -> tuple[_Read, ...]:
    """Read each segment of the stream once: the cost is linear in the samples, not in samples times repetitions."""
    reads: list[_Read] = []
    for segment in context.segments(stream_id):
        samples = segment_samples(context.clock, segment, (_CHANNEL,))
        micros = np.fromiter((_micros(t) for t in samples.times), dtype=np.int64, count=len(samples.times))
        reads.append(_Read(micros, samples.channels[_CHANNEL], segment.chunks[0].sample_rate_hz))
    return tuple(reads)


def _pieces(reads: Sequence[_Read], spans: Sequence[tuple[datetime, datetime]]) -> tuple[_Piece, ...]:
    """Samples of each UTC span (``start <= t < end``), one piece per (span, segment) that has samples."""
    pieces: list[_Piece] = []
    for index, (start, end) in enumerate(spans):
        for read in reads:
            values = read.values[(read.micros >= _micros(start)) & (read.micros < _micros(end))]
            if values.size:
                pieces.append(_Piece(index, values, read.rate_hz))
    return tuple(pieces)


def _rms(pieces: Sequence[_Piece]) -> float:
    return float(np.sqrt(np.mean(np.concatenate([p.values for p in pieces]) ** 2)))


def _peak_rms(pieces: Sequence[_Piece], window_ms: float) -> float:
    """Highest windowed RMS across all pieces; the window never crosses from one piece to another."""
    best = 0.0
    for piece in pieces:
        length = min(piece.values.size, max(1, round(window_ms / 1000.0 * piece.rate_hz)))
        windowed = np.convolve(piece.values**2, np.full(length, 1.0 / length), mode="valid")
        best = max(best, float(np.sqrt(windowed.max())))
    return best


def _n(pieces: Sequence[_Piece]) -> int:
    return len({p.window for p in pieces})


def _mvc(context: ExtractionContext, stream: _EmgStream) -> EmgMvcCalibration | Reason:
    """Usable MVC of the stream: resolved, for the same muscle and side, and measured on a compatible signal type."""
    resolution = context.calibration(stream.stream_id)
    if resolution.reason is not None:
        return resolution.reason
    calibration = resolution.calibration
    if not isinstance(calibration, EmgMvcCalibration) or (calibration.muscle.value, calibration.side.value) != (stream.muscle, stream.side):
        return Reason.NO_CALIBRATION
    kinds = {c.signal_kind.value for s in context.segments(stream.stream_id) for c in s.chunks if isinstance(c, EmgChunk)}
    measured = _SIGNAL_GROUP[calibration.signal_kind.value]
    return calibration if all(_SIGNAL_GROUP[kind] == measured for kind in kinds) else Reason.NO_CALIBRATION


class EmgFamily:
    """EMG amplitude features from the windows of the injected repetitions service.

    ``feature_ids`` are all the concrete catalog ids (templates expanded with muscles and sides); in each
    request only those of the EMG streams declared in the session are emitted.
    """

    family = "emg"
    feature_ids: Sequence[str] = tuple(
        i for template in (RMS_MV, REST_RMS_MV, RMS_PCT_MVC, PEAK_PCT_MVC, SYMMETRY_INDEX_PCT) for i in expand_template(template)
    )

    def __init__(self, repetitions: RepetitionService) -> None:
        self._repetitions = repetitions

    def extract(self, context: ExtractionContext) -> FamilyResult:
        windows = self._repetitions.windows(context).windows
        computed: list[ComputedFeature] = []
        skipped: list[SkippedFeature] = []
        outcomes: dict[tuple[str, str], tuple[str, _Pct | Reason]] = {}
        for stream in _emg_streams(context):
            pct = self._stream(context, windows, stream, computed, skipped)
            outcomes.setdefault((stream.muscle, stream.side), (stream.stream_id, pct))
        for muscle in dict.fromkeys(muscle for muscle, _ in outcomes):
            self._symmetry(muscle, outcomes, computed, skipped)
        return FamilyResult(tuple(computed), tuple(skipped))

    def _stream(
        self,
        context: ExtractionContext,
        windows: tuple[RepWindow, ...],
        stream: _EmgStream,
        computed: list[ComputedFeature],
        skipped: list[SkippedFeature],
    ) -> _Pct | Reason:
        """Compute the four features of a stream and return its ``rms_pct_mvc`` (or why there is none)."""
        ids = {name: template.format(muscle=stream.muscle, side=stream.side) for name, template in _TEMPLATES.items()}
        used = (stream.stream_id,)

        def skip(name: str, reason: Reason) -> None:
            skipped.append(SkippedFeature(ids[name], reason))

        if context.stream_missing(stream.stream_id):
            for name in _TEMPLATES:
                skip(name, Reason.STREAM_MISSING)
            return Reason.STREAM_MISSING

        reads = _read(context, stream.stream_id)
        active = _pieces(reads, [w.active for w in windows])
        rest = _pieces(reads, [rest for w in windows if (rest := w.rest) is not None])
        no_active = Reason.TOO_FEW_REPETITIONS if not windows else Reason.NO_DATA
        mvc = _mvc(context, stream)
        rms_mv = _rms(active) if active else None
        if rms_mv is None:
            skip("rms_mv", no_active)
        else:
            computed.append(ComputedFeature(ids["rms_mv"], rms_mv, used, _n(active)))
        if rest:
            computed.append(ComputedFeature(ids["rest_rms_mv"], _rms(rest), used, _n(rest)))
        else:
            skip("rest_rms_mv", Reason.NO_DATA)
        if isinstance(mvc, Reason):
            skip("rms_pct_mvc", mvc)
            skip("peak_pct_mvc", mvc)
            return mvc
        if rms_mv is None:
            skip("rms_pct_mvc", no_active)
            skip("peak_pct_mvc", no_active)
            return no_active
        pct = 100.0 * rms_mv / mvc.mvc_rms_mv
        peak = 100.0 * _peak_rms(active, mvc.window_ms) / mvc.mvc_rms_mv
        n = _n(active)
        computed.append(ComputedFeature(ids["rms_pct_mvc"], pct, used, n))
        computed.append(ComputedFeature(ids["peak_pct_mvc"], peak, used, n))
        return _Pct(pct, n)

    @staticmethod
    def _symmetry(
        muscle: str,
        outcomes: dict[tuple[str, str], tuple[str, _Pct | Reason]],
        computed: list[ComputedFeature],
        skipped: list[SkippedFeature],
    ) -> None:
        feature_id = SYMMETRY_INDEX_PCT.format(muscle=muscle)
        left, right = outcomes.get((muscle, "left")), outcomes.get((muscle, "right"))
        if left is None or right is None:
            skipped.append(SkippedFeature(feature_id, Reason.STREAM_MISSING))
            return
        sides = (left[1], right[1])
        reasons = [s for s in sides if isinstance(s, Reason)]
        if reasons:
            skipped.append(SkippedFeature(feature_id, Reason.NO_CALIBRATION if Reason.NO_CALIBRATION in reasons else reasons[0]))
            return
        left_pct, right_pct = (s for s in sides if isinstance(s, _Pct))
        mean = (left_pct.value + right_pct.value) / 2.0
        if mean <= 0.0:
            skipped.append(SkippedFeature(feature_id, Reason.NO_DATA))
            return
        index = 100.0 * abs(left_pct.value - right_pct.value) / mean
        computed.append(ComputedFeature(feature_id, index, (left[0], right[0]), min(left_pct.n, right_pct.n)))
