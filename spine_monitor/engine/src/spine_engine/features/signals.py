"""Muestras de un segmento con su instante UTC, recortables a una ventana (#42, #44).

Cada chunk aporta ``n`` muestras: la ``i`` cae en ``t0_device_ms + i·1000/sample_rate_hz`` del dispositivo y su UTC
sale del ajuste de reloj del ``boot_id`` (milisegundos del dispositivo redondeados). Nunca se unen muestras de dos
``boot_id``: quien llama pide un segmento cada vez. Sin lanzar: lo que no tiene UTC (arranque sin ancla, valor no
representable) no aparece en el resultado.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from spine_engine.features.clock import DeviceClock
from spine_engine.features.ordering import Segment


@dataclass(frozen=True, slots=True)
class SegmentSamples:
    """Muestras de un segmento: ``times[i]`` y ``ends[i]`` (UTC del inicio y del fin de la muestra) y su valor en cada canal."""

    times: tuple[datetime, ...]
    ends: tuple[datetime, ...]
    channels: dict[str, NDArray[np.float64]]


def segment_samples(
    clock: DeviceClock,
    segment: Segment,
    channels: Sequence[str],
    window: tuple[datetime, datetime] | None = None,
) -> SegmentSamples:
    """Muestras del segmento en orden de ``seq`` para los ``channels`` pedidos, con ``start <= t < end`` si hay ventana.

    Un chunk al que le falta algún canal pedido se salta entero; en los demás se usa el menor número de muestras
    entre canales. Con ventana vacía o fuera del segmento, o sin UTC, devuelve tuplas y arrays vacíos con todos los canales.
    """
    times: list[datetime] = []
    ends: list[datetime] = []
    values: dict[str, list[float]] = {name: [] for name in channels}
    for chunk in segment.chunks:
        found = [getattr(chunk.data, name, None) for name in channels]
        # los canales del IMU son RootModel (``.root``); el ``emg`` del contrato es ya una lista
        columns = [getattr(channel, "root", channel) for channel in found if channel is not None]
        if len(columns) < len(found):
            continue
        n = min(len(column) for column in columns)
        step_ms = 1000.0 / chunk.sample_rate_hz
        for i in range(n):
            device_ms = chunk.t0_device_ms + i * step_ms
            start = clock.to_utc(segment.boot_id, round(device_ms))
            end = clock.to_utc(segment.boot_id, round(device_ms + step_ms))
            if start is None or end is None or (window is not None and not window[0] <= start < window[1]):
                continue
            times.append(start)
            ends.append(end)
            for name, column in zip(channels, columns, strict=True):
                values[name].append(float(column[i]))
    return SegmentSamples(tuple(times), tuple(ends), {name: np.asarray(v, dtype=np.float64) for name, v in values.items()})
