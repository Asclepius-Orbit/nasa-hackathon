"""Samples of a segment with their UTC instant, trimmable to a window (#42, #44).

Each chunk contributes ``n`` samples: the ``i``-th falls at the device's ``t0_device_ms + i·1000/sample_rate_hz`` and its UTC
comes from the clock fit of the ``boot_id`` (rounded device milliseconds). Samples of two
``boot_id`` are never joined: the caller asks for one segment at a time. Never raises: what has no UTC (boot without an anchor, unrepresentable
value) does not appear in the result.
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
    """Samples of a segment: ``times[i]`` and ``ends[i]`` (UTC of the start and end of the sample) and its value in each channel."""

    times: tuple[datetime, ...]
    ends: tuple[datetime, ...]
    channels: dict[str, NDArray[np.float64]]


def segment_samples(
    clock: DeviceClock,
    segment: Segment,
    channels: Sequence[str],
    window: tuple[datetime, datetime] | None = None,
) -> SegmentSamples:
    """Samples of the segment in ``seq`` order for the requested ``channels``, with ``start <= t < end`` if there is a window.

    A chunk missing any requested channel is skipped entirely; in the others the smallest number of samples
    among channels is used. With an empty window or one outside the segment, or without UTC, returns empty tuples and arrays for all channels.
    """
    times: list[datetime] = []
    ends: list[datetime] = []
    values: dict[str, list[float]] = {name: [] for name in channels}
    for chunk in segment.chunks:
        found = [getattr(chunk.data, name, None) for name in channels]
        # the IMU channels are RootModel (``.root``); the contract's ``emg`` is already a list
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
