"""Familia ``stretch`` (#45): elongación del cordón, pico y rango por repetición, recuperación y resistencia de reposo.

Se trabaja siempre desde las cuentas ADC. Por chunk: con ``circuit`` completo (``adc_bits`` y ``series_resistor_ohm``)
la resistencia sale de ``adc_counts`` (``stretch_curve.adc_to_resistance``); si no, de ``data.resistance_ohm``; un
chunk sin ninguna de las dos no aporta muestras. Las resistencias no finitas (ADC a 0 o saturado, NaN) se descartan.
La elongación por muestra sale de la curva y la longitud de reposo de ``calibration.stretch``. La espera de
recuperación es ``recovery_wait_s`` de esa calibración (aunque esté caducada) o, si no la trae, la
``default_recovery_wait_s`` del constructor (provisional hasta #46). Por stream stretch con ``placement.spine_region``:

- Muestras pasivas: las que no caen en ``[inicio de activa, fin de activa + espera)`` de ninguna repetición (sin
  repeticiones, p. ej. ``spine_check``, todas). ``elongation_mm`` = media de su elongación, ``elongation_pct`` =
  ``100·elongation_mm / rest_length_mm`` y ``rest_resistance_ohm`` = media de su resistencia (no necesita
  calibración, como dice el catálogo). ``n`` = número de muestras pasivas.
- Por repetición (ventana activa ``inicio <= t < fin``): ``peak_elongation_mm`` = máxima elongación, ``range_mm`` =
  máx − mín. Publicado = media sobre las repeticiones con muestras; ``n`` las cuenta.
- ``recovery_time_s``: por repetición, segundos desde el fin de la activa hasta la primera muestra con elongación
  ``<= recovery_fraction · pico de esa repetición`` (PROVISIONAL hasta #46), buscada en ``[fin, fin + espera)``
  recortado al fin del reposo de la ``RepWindow`` si lo tiene. Una repetición que no recupera ahí se excluye;
  publicado = media de las que recuperan, ``n`` las cuenta; si ninguna recupera, ``no_data``.

Motivos de ``skipped`` en orden: stream declarado sin chunks ``stream_missing`` (todas); para todo menos
``rest_resistance_ohm``, sin calibración ``no_calibration``, caducada ``calibration_expired`` y una curva
inutilizable (``StretchCurveError``) o una calibración que no es de cordón ``no_calibration``; sin muestras
(pasivas) ``no_data`` (incluye no tener ``circuit`` ni ``resistance_ohm``, o no tener UTC); para las de repetición,
menos de ``MIN_REPETITIONS`` (2) repeticiones con muestras ``too_few_repetitions``, salvo que haya repeticiones y
ninguna tenga muestras (``no_data``). Nunca lanza.
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
_PER_REP = ("peak_elongation_mm", "range_mm", "recovery_time_s")
_CALIBRATED = ("elongation_mm", "elongation_pct", *_PER_REP)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class _Series:
    """Muestras válidas del stream (resistencia finita): instante UTC en µs enteros y resistencia (Ω)."""

    micros: NDArray[np.int64]
    resistance: NDArray[np.float64]


def _micros(instant: datetime) -> int:
    return (instant - _EPOCH) // _MICROSECOND


def _read(context: ExtractionContext, stream_id: str) -> _Series:
    """Resistencia por muestra, chunk a chunk: de ``adc_counts`` con el ``circuit`` del chunk o, sin él, ``resistance_ohm``."""
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
    """Elongación (mm) por muestra de la serie y la longitud de reposo de la calibración."""

    mm: NDArray[np.float64]
    rest_length_mm: float


def _elongation(series: _Series, calibration: StretchCalibration | Reason) -> _Elongation | Reason:
    """Elongación con la curva de la calibración; una curva inutilizable es ``no_calibration``."""
    if isinstance(calibration, Reason):
        return calibration
    rest = calibration.rest_length_mm
    try:
        return _Elongation(elongation_mm(resistance_to_length(series.resistance, calibration.curve.points), rest), rest)
    except StretchCurveError:
        return Reason.NO_CALIBRATION


def _span(micros: NDArray[np.int64], start: datetime, end: datetime) -> NDArray[np.bool_]:
    """Máscara de las muestras en ``start <= t < end``."""
    return (micros >= _micros(start)) & (micros < _micros(end))


def _recovery_s(micros: NDArray[np.int64], elongation: NDArray[np.float64], rep: RepWindow, peak: float, wait: timedelta, fraction: float) -> float | None:
    """Segundos desde el fin de la activa hasta la primera muestra con elongación ``<= recovery_fraction · peak``.

    Se busca en ``[fin, fin + wait)`` recortado al fin del reposo si lo hay; ``None`` si no recupera ahí.
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
    """Features del cordón por stream stretch declarado con ``placement.spine_region``."""

    family = "stretch"
    feature_ids: Sequence[str] = tuple(i for name in _NAMES for i in expand_template(f"stretch.{{spine_region}}.{name}"))

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
                for name in _NAMES:
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
        """Espera de recuperación: la de la calibración del stream (aunque esté caducada) o, sin ella, la de por defecto."""
        calibration = context.calibration(stream_id).calibration
        if isinstance(calibration, StretchCalibration) and calibration.recovery_wait_s is not None:
            return calibration.recovery_wait_s
        return self._default_recovery_wait_s
