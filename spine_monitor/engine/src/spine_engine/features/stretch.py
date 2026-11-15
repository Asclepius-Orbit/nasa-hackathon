"""``stretch`` family (#45): cord elongation, peak and range per repetition, recovery and rest resistance.

Work is always done from the ADC counts. Per chunk: with a complete ``circuit`` (``adc_bits`` and ``series_resistor_ohm``)
the resistance comes from ``adc_counts`` (``stretch_curve.adc_to_resistance``); otherwise from ``data.resistance_ohm``; a
a chunk with neither contributes no samples. Non-finite resistances (ADC at 0 or saturated, NaN) are discarded.
The per-sample elongation comes from the curve and the rest length of ``calibration.stretch``. The recovery
wait is ``recovery_wait_s`` of that calibration (even if expired) or, if it has none, the
``default_recovery_wait_s`` of the constructor. Per stretch stream with ``placement.spine_region``:

- Passive samples: those that fall in no repetition's ``[active start, active end + wait)`` (with no
    repetitions, e.g. ``spine_check``, all of them). ``elongation_mm`` = mean of their elongation, ``elongation_pct`` =
  ``100·elongation_mm / rest_length_mm`` and ``rest_resistance_ohm`` = mean of their resistance (needs no
    calibration, as the catalog says). ``n`` = number of passive samples.
- Per repetition (active window ``start <= t < end``): ``peak_elongation_mm`` = maximum elongation, ``range_mm`` =
    max − min. Published = mean over the repetitions with samples; ``n`` counts them.
- ``recovery_time_s``: per repetition, seconds from the end of the active phase to the first sample with elongation
    ``<= recovery_fraction · peak of that repetition``, searched in ``[end, end + wait)``
    clipped to the end of the rest of the ``RepWindow`` if it has one. A repetition that does not recover there is excluded;
    published = mean of those that recover, ``n`` counts them; if none recovers, ``no_data``.

``skipped`` reasons in order: declared stream without chunks ``stream_missing`` (all of them); for everything except
``rest_resistance_ohm``, no calibration ``no_calibration``, expired ``calibration_expired`` and an unusable
curve (``StretchCurveError``) or a calibration that is not for a cord ``no_calibration``; without (passive)
samples ``no_data`` (includes having no ``circuit`` or ``resistance_ohm``, or no UTC); for the repetition ones,
fewer than ``MIN_REPETITIONS`` (2) repetitions with samples ``too_few_repetitions``, unless there are repetitions and
none has samples (``no_data``). Never raises.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import numpy as np
from numpy.typing import NDArray
from spine_contracts import StreamKind, StretchCalibration, StretchChunk
from spine_contracts.catalog import expand_template
from spine_contracts.models.domain.feature_set_schema import Reason

from spine_engine.features.context import ExtractionContext
from spine_engine.features.exercise import MIN_REPETITIONS
from spine_engine.features.registry import ComputedFeature, FamilyResult, SkippedFeature
from spine_engine.features.repetitions import RepetitionService, RepWindow
from spine_engine.features.signals import segment_samples
from spine_engine.features.stretch_curve import StretchCurveError, adc_to_resistance, elongation_mm, resistance_to_length
from spine_engine.settings import DEFAULT_STRETCH_RECOVERY_FRACTION

_NAMES = ("elongation_mm", "elongation_pct", "peak_elongation_mm", "range_mm", "recovery_time_s", "rest_resistance_ohm")
_POSTURE_NAMES = {
    "spine_resistance_straight_v1": "straight_resistance_ohm",
    "spine_resistance_max_flexion_v1": "max_flexion_resistance_ohm",
}
_PER_REP = ("peak_elongation_mm", "range_mm", "recovery_time_s")
_CALIBRATED = ("elongation_mm", "elongation_pct", *_PER_REP)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class _Series:
    """Valid samples of the stream (finite resistance): UTC instant in integer µs and resistance (Ω)."""

    micros: NDArray[np.int64]
    resistance: NDArray[np.float64]


def _micros(instant: datetime) -> int:
    return (instant - _EPOCH) // _MICROSECOND


def _read(context: ExtractionContext, stream_id: str) -> _Series:
    """Resistance per sample, chunk by chunk: from ``adc_counts`` with the chunk's ``circuit`` or, without it, ``resistance_ohm``."""
    micros: list[NDArray[np.int64]] = []
    resistance: list[NDArray[np.float64]] = []
    for segment in context.segments(stream_id):
        for chunk in segment.chunks:
            if not isinstance(chunk, StretchChunk):
                continue
            one = replace(segment, chunks=(chunk,))
            circuit = chunk.circuit
            if circuit is not None and circuit.adc_bits is not None and circuit.series_resistor_ohm is not None:
                samples = segment_samples(context.clock, one, ("adc_counts",))
                ohms = adc_to_resistance(samples.channels["adc_counts"], adc_bits=circuit.adc_bits, series_resistor_ohm=circuit.series_resistor_ohm)
            else:
                samples = segment_samples(context.clock, one, ("resistance_ohm",))
                ohms = samples.channels["resistance_ohm"]
            micros.append(np.fromiter((_micros(t) for t in samples.times), dtype=np.int64, count=len(samples.times)))
            resistance.append(ohms)
    all_micros = np.concatenate([np.zeros(0, dtype=np.int64), *micros])
    all_ohms = np.concatenate([np.zeros(0, dtype=np.float64), *resistance])
    keep = np.isfinite(all_ohms)
    return _Series(all_micros[keep], all_ohms[keep])


def _calibration(context: ExtractionContext, stream_id: str) -> StretchCalibration | Reason:
    resolution = context.calibration(stream_id)
    if resolution.reason is not None:
        return resolution.reason
    calibration = resolution.calibration
    return calibration if isinstance(calibration, StretchCalibration) else Reason.NO_CALIBRATION


@dataclass(frozen=True, slots=True)
class _Elongation:
    """Elongation (mm) per sample of the series and the rest length of the calibration."""

    mm: NDArray[np.float64]
    rest_length_mm: float


def _elongation(series: _Series, calibration: StretchCalibration | Reason) -> _Elongation | Reason:
    """Elongation with the calibration curve; an unusable curve is ``no_calibration``."""
    if isinstance(calibration, Reason):
        return calibration
    rest = calibration.rest_length_mm
    try:
        return _Elongation(elongation_mm(resistance_to_length(series.resistance, calibration.curve.points), rest), rest)
    except StretchCurveError:
        return Reason.NO_CALIBRATION


def _span(micros: NDArray[np.int64], start: datetime, end: datetime) -> NDArray[np.bool_]:
    """Mask of the samples in ``start <= t < end``."""
    return (micros >= _micros(start)) & (micros < _micros(end))


def _recovery_s(micros: NDArray[np.int64], elongation: NDArray[np.float64], rep: RepWindow, peak: float, wait: timedelta, fraction: float) -> float | None:
    """Seconds from the end of the active phase to the first sample with elongation ``<= recovery_fraction · peak``.

    Searched in ``[end, end + wait)`` clipped to the end of rest if there is one; ``None`` if it does not recover there.
    """
    end = rep.end + wait
    if rep.rest_end is not None:
        end = min(end, rep.rest_end)
    found = np.flatnonzero(_span(micros, rep.end, end) & (elongation <= fraction * peak))
    if not found.size:
        return None
    return float(micros[found].min() - _micros(rep.end)) / 1e6


type _Outcome = tuple[float, int] | Reason


class StretchFamily:
    """Cord features per declared stretch stream with ``placement.spine_region``."""

    family = "stretch"
    feature_ids: Sequence[str] = tuple(i for name in (*_NAMES, *_POSTURE_NAMES.values()) for i in expand_template(f"stretch.{{spine_region}}.{name}"))

    def __init__(self, repetitions: RepetitionService, *, default_recovery_wait_s: float, recovery_fraction: float = DEFAULT_STRETCH_RECOVERY_FRACTION) -> None:
        self._repetitions = repetitions
        self._default_recovery_wait_s = default_recovery_wait_s
        self._recovery_fraction = recovery_fraction

    def extract(self, context: ExtractionContext) -> FamilyResult:
        computed: list[ComputedFeature] = []
        skipped: list[SkippedFeature] = []
        for stream in context.session.streams:
            region = stream.placement.spine_region
            if stream.stream_kind is StreamKind.STRETCH and region is not None:
                outcomes = self._stream(context, stream.stream_id)
                posture_name = _POSTURE_NAMES.get(context.session.protocol_id)
                if posture_name is not None:
                    outcomes[posture_name] = outcomes["rest_resistance_ohm"]
                for name in ((posture_name,) if posture_name else _NAMES):
                    feature_id = f"stretch.{region.value}.{name}"
                    outcome = outcomes[name]
                    if isinstance(outcome, Reason):
                        skipped.append(SkippedFeature(feature_id, outcome))
                    else:
                        computed.append(ComputedFeature(feature_id, outcome[0], (stream.stream_id,), outcome[1]))
        return FamilyResult(tuple(computed), tuple(skipped))

    def _stream(self, context: ExtractionContext, stream_id: str) -> dict[str, _Outcome]:
        if context.stream_missing(stream_id):
            return dict.fromkeys(_NAMES, Reason.STREAM_MISSING)
        series = _read(context, stream_id)
        calibration = _calibration(context, stream_id)
        windows = self._repetitions.windows(context).windows
        wait = timedelta(seconds=self._recovery_wait_s(context, stream_id))
        passive = np.ones(series.micros.size, dtype=np.bool_)
        for rep in windows:
            passive &= ~_span(series.micros, rep.start, rep.end + wait)
        n_passive = int(passive.sum())
        outcomes: dict[str, _Outcome] = {"rest_resistance_ohm": (float(series.resistance[passive].mean()), n_passive) if n_passive else Reason.NO_DATA}
        elongation = _elongation(series, calibration)
        if isinstance(elongation, Reason):
            return {**outcomes, **dict.fromkeys(_CALIBRATED, elongation)}
        if n_passive:
            mean = float(elongation.mm[passive].mean())
            outcomes["elongation_mm"] = (mean, n_passive)
            outcomes["elongation_pct"] = (100.0 * mean / elongation.rest_length_mm, n_passive)
        else:
            outcomes["elongation_mm"] = outcomes["elongation_pct"] = Reason.NO_DATA
        reps = [(rep, values) for rep in windows if (values := elongation.mm[_span(series.micros, rep.start, rep.end)]).size]
        if len(reps) < MIN_REPETITIONS:
            reason = Reason.NO_DATA if windows and not reps else Reason.TOO_FEW_REPETITIONS
            return {**outcomes, **dict.fromkeys(_PER_REP, reason)}
        n = len(reps)
        outcomes["peak_elongation_mm"] = (float(np.mean([v.max() for _, v in reps])), n)
        outcomes["range_mm"] = (float(np.mean([v.max() - v.min() for _, v in reps])), n)
        times = [t for rep, v in reps if (t := _recovery_s(series.micros, elongation.mm, rep, float(v.max()), wait, self._recovery_fraction)) is not None]
        outcomes["recovery_time_s"] = (float(np.mean(times)), len(times)) if times else Reason.NO_DATA
        return outcomes

    def _recovery_wait_s(self, context: ExtractionContext, stream_id: str) -> float:
        """Recovery wait: that of the stream's calibration (even if expired) or, without it, the default one."""
        calibration = context.calibration(stream_id).calibration
        if isinstance(calibration, StretchCalibration) and calibration.recovery_wait_s is not None:
            return calibration.recovery_wait_s
        return self._default_recovery_wait_s
