"""Familia ``emg`` de amplitud (#42): RMS activo y de reposo, %MVC, pico en ventana y simetría entre lados.

Por cada stream EMG declarado con ``placement.muscle`` y ``side``:

- ``emg.{muscle}.{side}.rms_mv``: RMS de las muestras de la fase activa de todas las repeticiones.
- ``emg.{muscle}.{side}.rest_rms_mv``: RMS de las muestras de los reposos de las repeticiones.
- ``emg.{muscle}.{side}.rms_pct_mvc``: ``rms_mv`` como % de ``mvc_rms_mv`` de ``calibration.emg-mvc``.
- ``emg.{muscle}.{side}.peak_pct_mvc``: el RMS en ventana más alto (``window_ms`` de la calibración) como % de la MVC.
  Las ventanas no cruzan de una repetición a otra ni de un ``boot_id`` a otro; si una repetición tiene menos muestras
  que la ventana, todas sus muestras forman una sola ventana.

Y por músculo, ``emg.{muscle}.symmetry_index_pct`` = 100·|L-R| / ((L+R)/2) sobre los ``rms_pct_mvc`` de los dos lados.

La amplitud no necesita señal cruda: el cálculo es el mismo sobre cruda, rectificada o envolvente; el valor coincide
para cruda y rectificada, no para la envolvente. Por eso la MVC solo se usa si es del mismo músculo y lado que el
stream y se midió en un tipo de señal compatible con el de todos sus chunks (cruda y rectificada entre sí; envolvente
solo con envolvente); si no, lo que lleva %MVC sale ``no_calibration`` y ``rms_mv``/``rest_rms_mv`` se calculan igual.
Dos streams declarados con el mismo músculo y lado (caso de borde no tratado): cada uno emite sus features con el mismo
id (el compuesto se queda con la primera calculada) y la simetría usa siempre el primero. Las muestras de
cada ventana salen de ``segment_samples`` (ventana UTC semiabierta ``inicio <= t < fin``). ``n`` es el número de
repeticiones que aportaron muestras. Nunca lanza.

Motivos de ``skipped``: sin calibración ``no_calibration`` y caducada ``calibration_expired`` (solo afectan a lo que
lleva %MVC); sin repeticiones ``too_few_repetitions``; sin muestras en las ventanas ``no_data``; stream declarado sin
chunks ``stream_missing``. La simetría hace falta con la MVC de ambos lados: si falta alguna, ``no_calibration``
(aunque la otra esté caducada); un músculo con un solo lado declarado, ``stream_missing``; ``(L+R)/2`` nulo, ``no_data``.
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
# Tipos de señal con el mismo RMS: cruda y rectificada (el cuadrado borra el signo); la envolvente va aparte.
_SIGNAL_GROUP = {"raw": "rms", "rectified": "rms", "envelope": "envelope"}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)
_TEMPLATES = {"rms_mv": RMS_MV, "rest_rms_mv": REST_RMS_MV, "rms_pct_mvc": RMS_PCT_MVC, "peak_pct_mvc": PEAK_PCT_MVC}


@dataclass(frozen=True, slots=True)
class _Piece:
    """Muestras de una repetición (``window``) dentro de un segmento, con su frecuencia de muestreo."""

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
    """``rms_pct_mvc`` de un lado y las repeticiones que lo respaldan."""

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
    """Muestras de un segmento leídas una sola vez: instante UTC en microsegundos enteros y valor."""

    micros: NDArray[np.int64]
    values: NDArray[np.float64]
    rate_hz: float


def _micros(instant: datetime) -> int:
    """Microsegundos UTC desde la época, enteros: compara igual que los ``datetime`` (sin redondeo de float)."""
    return (instant - _EPOCH) // _MICROSECOND


def _read(context: ExtractionContext, stream_id: str) -> tuple[_Read, ...]:
    """Lee cada segmento del stream una vez: el coste es lineal en las muestras, no en muestras por repeticiones."""
    reads: list[_Read] = []
    for segment in context.segments(stream_id):
        samples = segment_samples(context.clock, segment, (_CHANNEL,))
        micros = np.fromiter((_micros(t) for t in samples.times), dtype=np.int64, count=len(samples.times))
        reads.append(_Read(micros, samples.channels[_CHANNEL], segment.chunks[0].sample_rate_hz))
    return tuple(reads)


def _pieces(reads: Sequence[_Read], spans: Sequence[tuple[datetime, datetime]]) -> tuple[_Piece, ...]:
    """Muestras de cada tramo UTC (``inicio <= t < fin``), un trozo por (tramo, segmento) que tenga muestras."""
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
    """RMS en ventana más alto de todos los trozos; la ventana nunca cruza de un trozo a otro."""
    best = 0.0
    for piece in pieces:
        length = min(piece.values.size, max(1, round(window_ms / 1000.0 * piece.rate_hz)))
        windowed = np.convolve(piece.values**2, np.full(length, 1.0 / length), mode="valid")
        best = max(best, float(np.sqrt(windowed.max())))
    return best


def _n(pieces: Sequence[_Piece]) -> int:
    return len({p.window for p in pieces})


def _mvc(context: ExtractionContext, stream: _EmgStream) -> EmgMvcCalibration | Reason:
    """MVC utilizable del stream: resuelta, del mismo músculo y lado, y medida en un tipo de señal compatible."""
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
    """Features de amplitud del EMG a partir de las ventanas del servicio de repeticiones inyectado.

    ``feature_ids`` son todos los ids concretos del catálogo (plantillas expandidas con músculos y lados); en cada
    petición solo se emiten los de los streams EMG declarados en la sesión.
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
        """Calcula las cuatro features de un stream y devuelve su ``rms_pct_mvc`` (o por qué no hay)."""
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
