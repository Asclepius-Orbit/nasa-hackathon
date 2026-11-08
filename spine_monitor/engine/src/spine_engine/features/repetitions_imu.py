"""Respaldo IMU de las repeticiones (#41): ventanas desde el giróscopo cuando no hay eventos válidos.

Solo giróscopo (``gyro_x/y/z`` en deg/s): en microgravedad el acelerómetro no ve la gravedad y no sirve
para ver la flexión. Cada repetición es una flexión de ida y vuelta, así que en el eje dominante deja dos
lóbulos de velocidad angular de signo contrario (ida, vuelta). El algoritmo, por segmento (``boot_id``):

1. Muestras del segmento en orden de ``seq``; el instante de cada una es ``t0_device_ms + i/sample_rate_hz``
   de su chunk. Las no finitas se sustituyen por la mediana del eje (sin movimiento).
2. Se resta a cada eje su mediana (sesgo del giróscopo; el reposo domina) y se elige el eje con más
   energía (empate: x, luego y, luego z).
3. Media móvil centrada de ``SMOOTH_S`` y ruido estimado con la MAD de las primeras diferencias (robusto
   frente al propio movimiento, que es suave).
4. Lóbulos: tramos contiguos de mismo signo con ``|ω|`` sobre el umbral bajo y pico sobre el alto. Umbrales
   relativos al máximo del segmento (``PEAK_FRACTION`` y ``EDGE_FRACTION``) con suelo de ruido
   (``NOISE_K_PEAK`` y ``NOISE_K_EDGE`` sigmas). Lóbulos del mismo signo con picos a menos de
   ``MIN_PEAK_SEPARATION_S`` se funden (un lóbulo partido por el ruido).
5. Emparejado ida→vuelta: lóbulo de signo ``s`` seguido inmediatamente de uno de signo ``-s``. Se prueban
   ``s = +1`` y ``s = -1`` y gana el que más pares da (empate: menor hueco total dentro de los pares; luego
   ``+1``). Un lóbulo sin pareja (p. ej. un único pico) se descarta.

Varios arranques: cada segmento se detecta por separado (nunca se unen muestras de dos ``boot_id``) y las
ventanas se ordenan por tiempo con índices consecutivos; el reposo va de cada fin al inicio siguiente.
Varios streams IMU: se usa el primero por ``stream_id`` que dé alguna ventana. Determinista y sin lanzar:
lo incoherente (sin IMU, sin ancla de reloj, sin señal) da ``()``.
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

# Media móvil: atenúa el ruido sin desdibujar lóbulos de ~1 s o más.
SMOOTH_S = 0.1
# Un lóbulo cuenta si su pico llega a esta fracción del máximo del segmento...
PEAK_FRACTION = 0.25
# ...y sus bordes llegan hasta donde |ω| cae bajo esta fracción (cuanto más baja, menos se recorta la rep).
EDGE_FRACTION = 0.03
# Suelos de ruido, en sigmas del ruido suavizado: ni el pico ni el borde se confunden con ruido.
NOISE_K_PEAK = 8.0
NOISE_K_EDGE = 3.0
# Dos picos del mismo signo más cercanos que esto son el mismo lóbulo.
MIN_PEAK_SEPARATION_S = 0.25
# MAD -> sigma para ruido gaussiano; las diferencias de ruido blanco tienen sigma·√2.
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
    """Ventanas de repetición desde el giróscopo del primer stream IMU (por ``stream_id``) que las dé."""
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
    return tuple(
        RepWindow(
            index=i + 1,
            start=start,
            end=end,
            rest_start=end if i < last else None,
            rest_end=spans[i + 1][0] if i < last else None,
        )
        for i, (start, end) in enumerate(spans)
    )


def _segment_spans(context: ExtractionContext, segment: Segment) -> list[tuple[datetime, datetime]]:
    chunks = [c for c in segment.chunks if isinstance(c, ImuChunk)]
    if not chunks:
        return []
    times: list[float] = []
    axes: list[list[float]] = [[], [], []]
    for chunk in chunks:
        values = [getattr(chunk.data, name).root for name in _GYRO_AXES]
        n = min(len(v) for v in values)
        step_ms = 1000.0 / chunk.sample_rate_hz
        times.extend(chunk.t0_device_ms + i * step_ms for i in range(n))
        for axis, channel in zip(axes, values, strict=True):
            axis.extend(float(x) for x in channel[:n])
    rate = chunks[0].sample_rate_hz
    signal = _dominant_axis(axes)
    if signal is None:
        return []
    spans: list[tuple[datetime, datetime]] = []
    for out, back in _pairs(_lobes(signal, rate)):
        start = context.clock.to_utc(segment.boot_id, round(times[out.first]))
        end = context.clock.to_utc(segment.boot_id, round(times[back.last] + 1000.0 / rate))
        if start is not None and end is not None and end > start:
            spans.append((start, end))
    return spans


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
    """Alarga el borde mientras |ω| siga bajando sin cambiar de signo: recupera la cola casi quieta del lóbulo."""
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
