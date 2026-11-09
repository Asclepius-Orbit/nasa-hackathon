"""Familia ``emg_spectral`` (#43): frecuencia mediana del EMG por repetición y su pendiente entre repeticiones.

Por cada stream EMG declarado con ``placement.muscle`` y ``side``:

- ``emg.{muscle}.{side}.median_freq_hz``: media, sobre las repeticiones válidas, de la frecuencia mediana del espectro
  de potencia (PSD) de las muestras de la fase activa de cada repetición. La mediana es la frecuencia que parte la
  potencia total en dos mitades: se interpola linealmente sobre la PSD acumulada. Un espectro sin potencia (señal
  nula) da 0 Hz. Unidad: Hz.
- ``emg.{muscle}.{side}.median_freq_slope_hz_per_rep``: pendiente del ajuste lineal por mínimos cuadrados de la
  mediana contra el índice de repetición (``RepWindow.index``, el de la repetición, no su posición entre las
  válidas; así una repetición descartada deja un hueco, igual que la verdad de terreno del simulador). Unidad: Hz/rep.
  Una pendiente negativa es fatiga: la mediana baja.

PSD: Welch con segmentos de 1 s (``round(rate_hz)`` muestras, resolución de 1 Hz) o la repetición entera si es más
corta, ventana de Hann, solape del 50 % y resta de la media de cada segmento. Es numpy puro (``scipy`` no es
dependencia del engine); la escala de la PSD no influye en la mediana.

Una repetición es válida si su fase activa aporta al menos ``MIN_ACTIVE_SAMPLES`` muestras: con menos, la ventana de
Hann y la resolución espectral (``rate_hz`` / muestras) no dejan una mediana con sentido. Si la fase activa cae en
varios segmentos del stream, se usa el trozo más largo (unir trozos de arranques distintos inventaría un espectro).
``n`` es el número de repeticiones válidas. Hacen falta al menos 2 repeticiones válidas (D-60).

Solo vale el EMG crudo: la rectificación y la envolvente destruyen el espectro. Si algún chunk del stream no es
``raw``, todo sale ``signal_not_raw``. Nunca lanza (D-51).

Motivos de ``skipped``, por orden de precedencia: stream declarado sin chunks ``stream_missing``; algún chunk no
crudo ``signal_not_raw``; sin repeticiones o menos de 2 válidas ``too_few_repetitions``; repeticiones pero sin
muestras en ninguna ``no_data``.
"""

from collections.abc import Sequence
from datetime import datetime

import numpy as np
from numpy.typing import NDArray
from spine_contracts import EmgChunk
from spine_contracts.catalog import expand_template
from spine_contracts.models.domain.feature_set_schema import Reason

from spine_engine.features.context import ExtractionContext
from spine_engine.features.emg import _emg_streams, _EmgStream, _Piece, _pieces, _read
from spine_engine.features.registry import ComputedFeature, FamilyResult, SkippedFeature
from spine_engine.features.repetitions import RepetitionService

MEDIAN_FREQ_HZ = "emg.{muscle}.{side}.median_freq_hz"
MEDIAN_FREQ_SLOPE = "emg.{muscle}.{side}.median_freq_slope_hz_per_rep"

# Mínimo de muestras activas de una repetición válida: 64 muestras son 64 ms a 1 kHz, resolución de ~16 Hz,
# lo mínimo para que la ventana de Hann deje más de un puñado de bins con potencia.
MIN_ACTIVE_SAMPLES = 64
_SEGMENT_SECONDS = 1.0  # duración del segmento de Welch
_OVERLAP = 0.5  # solape entre segmentos de Welch
_RAW = "raw"

type _Spans = list[tuple[int, tuple[datetime, datetime]]]  # (índice de repetición, fase activa)


def _median_frequency_hz(values: NDArray[np.float64], rate_hz: float) -> float:
    """Frecuencia mediana de la PSD de Welch (Hann, solape 50 %, sin media), 0 si no hay potencia."""
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
    """El trozo más largo de cada repetición (índice de ventana)."""
    best: dict[int, _Piece] = {}
    for piece in pieces:
        if piece.window not in best or piece.values.size > best[piece.window].values.size:
            best[piece.window] = piece
    return best


class EmgSpectralFamily:
    """Frecuencia mediana del EMG a partir de las ventanas del servicio de repeticiones inyectado.

    ``feature_ids`` son todos los ids concretos del catálogo; en cada petición solo se emiten los de los streams
    EMG declarados en la sesión.
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
        """Índices y medianas de las repeticiones válidas del stream, o el motivo por el que no hay."""
        if context.stream_missing(stream.stream_id):
            return Reason.STREAM_MISSING
        kinds = {c.signal_kind.value for s in context.segments(stream.stream_id) for c in s.chunks if isinstance(c, EmgChunk)}
        if kinds != {_RAW}:
            return Reason.SIGNAL_NOT_RAW
        if not spans:
            return Reason.TOO_FEW_REPETITIONS
        pieces = [p for p in _pieces(_read(context, stream.stream_id), [span for _, span in spans]) if np.isfinite(p.values).all()]
        if not pieces:
            return Reason.NO_DATA
        valid = sorted((spans[window][0], piece) for window, piece in _longest(pieces).items() if piece.values.size >= MIN_ACTIVE_SAMPLES)
        if len(valid) < 2:
            return Reason.TOO_FEW_REPETITIONS
        return [index for index, _ in valid], [_median_frequency_hz(p.values, p.rate_hz) for _, p in valid]
