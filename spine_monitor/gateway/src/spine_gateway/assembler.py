"""Assembly of the Arduino ``D`` batches into MQTT v1 chunks (``EmgChunk``/``ImuChunk``/``StretchChunk``).

Pure synchronous logic: no I/O, no clock (``received_at`` and ``session_id`` are supplied by the caller) and no randomness.

Units (contract ``telemetry/stream-chunk``):

- EMG: the signal type is configurable (``emg_signal_kind``): the module (Muscle Sensor V3 clone) probably
  delivers the envelope (default), but it is not confirmed with the board (issue #13). No ``filter`` block. mV at the
  ADC input: ``(counts - offset) / (2^bits - 1) · vref · 1000 / gain``, with ``offset = 0`` for ``rectified`` and
  ``envelope`` and ``offset = (2^bits - 1) / 2`` for ``raw`` (bipolar signal). ASSUMPTION NOT VERIFIED with hardware:
  that the raw signal arrives biased at Vref/2 (``emg_offset_counts``).
- IMU: nominal sensitivities from the MPU-6050 datasheet (``ACCEL_LSB_PER_G``, ``GYRO_LSB_PER_DPS``).
- Cord: ``adc_counts`` always. ``resistance_ohm`` via the divider (Rs between 5 V and A1, cord between A1 and GND):
  ``Rc = Rs · counts / (max - counts)``. At 0 (short) or at the maximum (open circuit) it is not computable and, since
  ``number_array`` does not allow ``null`` and all columns carry ``n_samples`` entries, that chunk does not publish
  ``resistance_ohm`` (nor ``length_mm``, which comes from it). ``length_mm`` only with a ``stretch`` calibration: piecewise
  linear curve clamped to its ends when out of range (flag ``out_of_calibration``); ``calibration_id`` goes
  only when there is ``length_mm``.

Quality (``assessed_by: gateway``): ``score = samples without problems / samples``; ``clipping`` if a sample saturates:
EMG at the ADC maximum (``raw``, centred: also at 0, both extremes are saturation; ``rectified`` and
``envelope``: 0 is the resting muscle, a healthy value), cord at 0 (short) or at the
maximum (also ``lead_off``: open circuit) and IMU at -32768 or 32767 on any sensor axis.
After lost batches, ``packet_gap`` and the score stays at most at ``PACKET_GAP_SCORE``.

``seq`` runs per (boot_id, stream) from 0 and is shared by session and background. A lost batch consumes seq (``1 + gap``) and
is declared in ``dropped_samples``; a duplicate or regression is discarded without touching anything (``Discarded``).

Background (outside a session, IMU and cord only): ``BackgroundAccumulator`` gathers 10 contiguous batches and publishes a
1 s chunk at 10 Hz with the mean of each batch. A gap drops the partial window (time would not be uniform), consumes one seq
for each 1 s window touched by the dropped part and by what was lost (``ceil((partial + gap) / 10)``) and the new window
starts at the next received batch. ``reset`` (session start or end) drops the partial window without consuming seq.

Known limit: ``t0_ms`` is the Arduino u32 ``millis()`` and wraps after ~49.7 days of continuous
operation; ``t0_device_ms`` would start over without changing ``boot_id``. Accepted for the demo mission.
"""

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from fractions import Fraction
from functools import reduce
from types import MappingProxyType
from typing import Any

from spine_contracts import SCHEMA_VERSION, EmgChunk, ImuChunk, QualityFlag, StretchCalibration, StretchChunk
from spine_contracts.models.telemetry.stream_chunk_schema import SignalKind

from spine_gateway.protocol import I16_MAX, I16_MIN, Frame, FrameKind, ImuSample, frame_gap
from spine_gateway.settings import GatewaySettings

BATCH_MS = 100
"""Duration of a firmware batch."""
SAMPLES_PER_BATCH: Mapping[FrameKind, int] = MappingProxyType({FrameKind.EMG: 100, FrameKind.IMU: 10, FrameKind.CORD: 2})
SAMPLE_RATE_HZ: Mapping[FrameKind, float] = MappingProxyType({kind: n * 1000 / BATCH_MS for kind, n in SAMPLES_PER_BATCH.items()})
"""EMG 1000 Hz, IMU 100 Hz, cord 20 Hz."""
BACKGROUND_FRAMES = 10
"""Contiguous batches per background chunk (1 s)."""
BACKGROUND_SAMPLE_RATE_HZ = 1000 / BATCH_MS
"""One sample (the mean) per batch: 10 Hz."""
PACKET_GAP_SCORE = 0.7
"""Maximum score of the chunk that follows lost batches (same value as the simulator mission example)."""

# Nominal MPU-6050 sensitivity per configured range (datasheet, tables 6.1 and 6.2).
ACCEL_LSB_PER_G: Mapping[int, float] = MappingProxyType({2: 16384.0, 4: 8192.0, 8: 4096.0, 16: 2048.0})
GYRO_LSB_PER_DPS: Mapping[int, float] = MappingProxyType({250: 131.0, 500: 65.5, 1000: 32.8, 2000: 16.4})

_IMU_COLUMNS = ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z")
_ACCEL_AXES = slice(0, 3)
_GYRO_AXES = slice(3, 6)

type Chunk = EmgChunk | ImuChunk | StretchChunk
type Samples = tuple[int, ...] | tuple[ImuSample, ...]


# --- conversions ---


def max_counts(adc_bits: int) -> int:
    """Maximum ADC reading: ``2^bits - 1``."""
    return (1 << adc_bits) - 1


def counts_to_mv(counts: float, *, adc_bits: int, vref_v: float, gain: float) -> float:
    """mV at the ADC input divided by the module gain. Accepts means (float)."""
    return counts / max_counts(adc_bits) * vref_v * 1000.0 / gain


def emg_offset_counts(settings: GatewaySettings) -> float:
    """Counts subtracted from the EMG before converting to mV: mid-scale if ``raw``, 0 otherwise.

    ASSUMPTION NOT VERIFIED with hardware: the raw signal arrives biased at Vref/2, so after subtracting the mid
    scale it is bipolar. An envelope or a rectified signal is unipolar and carries no offset.
    """
    return max_counts(settings.adc_bits) / 2 if settings.emg_signal_kind is SignalKind.RAW else 0.0


def _lsb(table: Mapping[int, float], sensor: str, value: int) -> float:
    lsb = table.get(value)
    if lsb is None:
        raise ValueError(f"unsupported {sensor} range: {value} (MPU-6050 supports {', '.join(map(str, table))})")
    return lsb


def accel_to_g(raw: int, range_g: int) -> float:
    """Raw accelerometer count to g with the nominal sensitivity of the range; ``ValueError`` if it is not 2/4/8/16."""
    return raw / _lsb(ACCEL_LSB_PER_G, "accel", range_g)


def gyro_to_dps(raw: int, range_dps: int) -> float:
    """Raw gyroscope count to °/s; ``ValueError`` if the range is not 250/500/1000/2000."""
    return raw / _lsb(GYRO_LSB_PER_DPS, "gyro", range_dps)


def counts_to_resistance(counts: float, *, adc_bits: int, series_resistor_ohm: float) -> float | None:
    """Cord resistance via the divider; ``None`` at 0 or from the maximum (short or open circuit)."""
    full = max_counts(adc_bits)
    if not 0 < counts < full:
        return None
    return series_resistor_ohm * counts / (full - counts)


def resistance_to_length(resistance: float, points: Sequence[tuple[float, float]]) -> tuple[float, bool]:
    """Length by piecewise linear interpolation over points (resistance_ohm, length_mm) and whether it falls inside.

    The points are sorted by resistance. Outside the curve range it is clamped to the length of the nearest
    end and ``False`` is returned (the caller flags it ``out_of_calibration``); it is not extrapolated because the response
    of the cord is not linear. The result of each segment is bounded to its ends so that it is monotonic in float.
    """
    curve = sorted(points)
    if resistance < curve[0][0]:
        return curve[0][1], False
    if resistance > curve[-1][0]:
        return curve[-1][1], False
    i = bisect_right([r for r, _ in curve], resistance)
    if i == len(curve):
        return curve[-1][1], True
    (r0, l0), (r1, l1) = curve[i - 1], curve[i]
    value = l0 + (resistance - r0) / (r1 - r0) * (l1 - l0)
    return min(max(value, min(l0, l1)), max(l0, l1)), True


# --- quality ---


@dataclass(frozen=True, slots=True)
class _Quality:
    score: float = 1.0
    flags: frozenset[QualityFlag] = frozenset()

    def worst(self, other: _Quality) -> _Quality:
        return _Quality(min(self.score, other.score), self.flags | other.flags)

    def contract(self) -> dict[str, Any]:
        # Order of the flags: that of the contract enum, so the output is deterministic.
        return {"score": self.score, "flags": [f.value for f in QualityFlag if f in self.flags], "assessed_by": "gateway"}


_GAP = _Quality(PACKET_GAP_SCORE, frozenset({QualityFlag.PACKET_GAP}))


def _fraction(bad: Iterable[frozenset[QualityFlag]], total: int) -> _Quality:
    """Quality of a channel from the flags of each problematic sample (clean ones are not passed)."""
    problems = list(bad)
    if not problems:
        return _Quality()
    return _Quality((total - len(problems)) / total, frozenset().union(*problems))


_CLIPPING = frozenset({QualityFlag.CLIPPING})
_OPEN = frozenset({QualityFlag.CLIPPING, QualityFlag.LEAD_OFF})
_OUT = frozenset({QualityFlag.OUT_OF_CALIBRATION})


def _emg_quality(counts: tuple[int, ...], full: int, kind: SignalKind) -> dict[str, _Quality]:
    # Raw (centred): 0 and the maximum are saturation. Rectified or envelope: near 0 is the resting muscle and
    # is not a signal problem; it only saturates at the maximum.
    low = 0 if kind is SignalKind.RAW else -1
    return {"emg": _fraction((_CLIPPING for c in counts if c <= low or c >= full), len(counts))}


def _cord_quality(counts: tuple[int, ...], full: int) -> dict[str, _Quality]:
    return {"adc_counts": _fraction((_OPEN if c >= full else _CLIPPING for c in counts if c <= 0 or c >= full), len(counts))}


def _imu_quality(samples: tuple[ImuSample, ...]) -> dict[str, _Quality]:
    def clipped(axes: slice) -> _Quality:
        return _fraction((_CLIPPING for s in samples if any(v in (I16_MIN, I16_MAX) for v in s[axes])), len(samples))

    return {"accel": clipped(_ACCEL_AXES), "gyro": clipped(_GYRO_AXES)}


# --- shape of the samples ---


def scalar_samples(kind: FrameKind, samples: Samples) -> tuple[int, ...]:
    """Samples of an EMG or cord batch as integers; ``TypeError`` if they are IMU tuples (programming error)."""
    values = tuple(s for s in samples if isinstance(s, int))
    if len(values) != len(samples):
        raise TypeError(f"{kind.name} frame with samples of the wrong shape")
    return values


def _imu_samples(samples: Samples) -> tuple[ImuSample, ...]:
    values = tuple(s for s in samples if isinstance(s, tuple))
    if len(values) != len(samples):
        raise TypeError(f"{FrameKind.IMU.name} frame with samples of the wrong shape")
    return values


def _mean(values: Iterable[int]) -> int:
    """Integer mean of integers, exact round-half-to-even on ties (``round`` of ``Fraction``): 2.5 -> 2, 3.5 -> 4."""
    items = list(values)
    return round(Fraction(sum(items), len(items)))


# --- results ---


class DiscardReason(StrEnum):
    """Why a batch does not produce a chunk."""

    NO_BOOT = "no_boot"
    """Arrived before any hello: there is no ``boot_id`` to publish it with."""
    UNEXPECTED_SAMPLE_COUNT = "unexpected_sample_count"
    """``n`` is not that of the 100 ms batch of the kind: the sampling rate would not be the declared one. Counts as lost."""
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    NOT_BACKGROUND_STREAM = "not_background_stream"
    """EMG outside a session: not published in the background (its ``frame_seq`` is still tracked)."""


@dataclass(frozen=True, slots=True)
class Discarded:
    kind: FrameKind
    frame_seq: int
    reason: DiscardReason


@dataclass(frozen=True, slots=True)
class Pending:
    """Batch absorbed by the background window; the chunk goes out when 10 batches complete."""

    kind: FrameKind
    frame_seq: int


# --- background ---


@dataclass(frozen=True, slots=True)
class BackgroundWindow:
    """10 contiguous batches reduced to their mean: ``samples`` has one integer sample per batch."""

    t0_ms: int
    samples: Samples
    frames: tuple[Frame, ...]
    after_gap: bool
    """There were lost batches between the previous window (or the last ``reset``) and this one."""


@dataclass(frozen=True, slots=True)
class BackgroundStep:
    lost_windows: int
    """Seq that the gap of this batch consumes (lost 1 s windows); 0 if there was no gap."""
    window: BackgroundWindow | None


class BackgroundAccumulator:
    """Background windows of an IMU or cord stream. ``add`` receives the gap already computed with ``frame_gap``."""

    def __init__(self, kind: FrameKind) -> None:
        if kind is FrameKind.EMG:
            raise ValueError("no background for EMG: it is only published during sessions")
        self._kind = kind
        self._frames: list[Frame] = []
        self._after_gap = False

    def reset(self) -> None:
        """Drops the partial window and the pending gap notice (session start or end, new boot)."""
        self._frames.clear()
        self._after_gap = False

    def add(self, frame: Frame, gap: int) -> BackgroundStep:
        lost = 0
        if gap > 0:
            lost = -(-(len(self._frames) + gap) // BACKGROUND_FRAMES)
            self._frames.clear()
            self._after_gap = True
        self._frames.append(frame)
        if len(self._frames) < BACKGROUND_FRAMES:
            return BackgroundStep(lost, None)
        frames = tuple(self._frames)
        window = BackgroundWindow(frames[0].t0_ms, self._average(frames), frames, self._after_gap)
        self.reset()
        return BackgroundStep(lost, window)

    def _average(self, frames: tuple[Frame, ...]) -> Samples:
        if self._kind is FrameKind.IMU:
            means: list[ImuSample] = []
            for f in frames:
                samples = _imu_samples(f.samples)
                ax, ay, az, gx, gy, gz = (_mean(s[axis] for s in samples) for axis in range(6))
                means.append((ax, ay, az, gx, gy, gz))
            return tuple(means)
        return tuple(_mean(scalar_samples(self._kind, f.samples)) for f in frames)


# --- assembler ---


@dataclass(slots=True)
class _Stream:
    last_frame_seq: int | None = None
    next_seq: int = 0


@dataclass(slots=True)
class _Streams:
    by_kind: dict[FrameKind, _Stream] = field(default_factory=lambda: {kind: _Stream() for kind in FrameKind})


class ChunkAssembler:
    """Per-stream state of the gateway: ``seq``, last ``frame_seq`` and background windows, per device boot."""

    def __init__(self, settings: GatewaySettings) -> None:
        # Validates the IMU ranges on construction: a configuration error must not wait for the first batch.
        _lsb(ACCEL_LSB_PER_G, "accel", settings.imu_accel_range_g)
        _lsb(GYRO_LSB_PER_DPS, "gyro", settings.imu_gyro_range_dps)
        self._settings = settings
        self._full = max_counts(settings.adc_bits)
        self._boot_id: int | None = None
        self._streams = _Streams()
        self._background = {kind: BackgroundAccumulator(kind) for kind in (FrameKind.IMU, FrameKind.CORD)}

    @property
    def boot_id(self) -> int | None:
        return self._boot_id

    def on_new_boot(self, boot_id: int) -> bool:
        """Firmware hello. With a new ``boot_id`` it resets seq, ``frame_seq`` and background and returns ``True``."""
        if boot_id == self._boot_id:
            return False
        self._boot_id = boot_id
        self._streams = _Streams()
        self.reset_background()
        return True

    def reset_background(self) -> None:
        """Session start or end: drops the partial background windows (does not consume seq)."""
        for accumulator in self._background.values():
            accumulator.reset()

    def next_seq(self, kind: FrameKind) -> int:
        """Seq that the next chunk of the ``kind`` stream will carry."""
        return self._streams.by_kind[kind].next_seq

    def session_chunk(
        self, frame: Frame, *, session_id: str, received_at: datetime, stretch_calibration: StretchCalibration | None = None
    ) -> Chunk | Discarded:
        """One chunk per batch, with the batch frequency. ``stretch_calibration``: the current one for the cord, if any."""
        gap = self._admit(frame)
        if isinstance(gap, Discarded):
            return gap
        stream = self._streams.by_kind[frame.kind]
        seq = stream.next_seq + gap
        stream.next_seq = seq + 1
        quality = self._frame_quality(frame.kind, frame.samples)
        if gap:
            quality = {name: q.worst(_GAP) for name, q in quality.items()}
        return self._build(
            frame.kind,
            frame.samples,
            quality,
            base={
                "session_id": session_id,
                "received_at": received_at,
                "seq": seq,
                "t0_device_ms": frame.t0_ms,
                "sample_rate_hz": SAMPLE_RATE_HZ[frame.kind],
                **({"dropped_samples": gap * len(frame.samples)} if gap else {}),
            },
            calibration=stretch_calibration,
        )

    def background_chunk(
        self, frame: Frame, *, session_id: str, received_at: datetime, stretch_calibration: StretchCalibration | None = None
    ) -> Chunk | Pending | Discarded:
        """Background: a 1 s chunk at 10 Hz every 10 contiguous batches; meanwhile ``Pending``. EMG: discarded."""
        gap = self._admit(frame)
        if isinstance(gap, Discarded):
            return gap
        if frame.kind is FrameKind.EMG:
            return Discarded(frame.kind, frame.frame_seq, DiscardReason.NOT_BACKGROUND_STREAM)
        stream = self._streams.by_kind[frame.kind]
        step = self._background[frame.kind].add(frame, gap)
        stream.next_seq += step.lost_windows
        window = step.window
        if window is None:
            return Pending(frame.kind, frame.frame_seq)
        # The quality of the window is the worst of its batches, channel by channel (the raw batches, not the means).
        per_frame = [self._frame_quality(f.kind, f.samples) for f in window.frames]
        quality = {name: reduce(_Quality.worst, (q[name] for q in per_frame)) for name in per_frame[0]}
        if window.after_gap:
            quality = {name: q.worst(_GAP) for name, q in quality.items()}
        seq = stream.next_seq
        stream.next_seq += 1
        return self._build(
            frame.kind,
            window.samples,
            quality,
            base={
                "session_id": session_id,
                "received_at": received_at,
                "seq": seq,
                "t0_device_ms": window.t0_ms,
                "sample_rate_hz": BACKGROUND_SAMPLE_RATE_HZ,
            },
            calibration=stretch_calibration,
        )

    # --- internals ---

    def _admit(self, frame: Frame) -> int | Discarded:
        """Gap relative to the previous batch of the kind, or the discard. Only an admitted batch moves the last ``frame_seq``."""
        if self._boot_id is None:
            return Discarded(frame.kind, frame.frame_seq, DiscardReason.NO_BOOT)
        if len(frame.samples) != SAMPLES_PER_BATCH[frame.kind]:
            return Discarded(frame.kind, frame.frame_seq, DiscardReason.UNEXPECTED_SAMPLE_COUNT)
        stream = self._streams.by_kind[frame.kind]
        gap = frame_gap(stream.last_frame_seq, frame.frame_seq)
        if gap == -1:
            return Discarded(frame.kind, frame.frame_seq, DiscardReason.DUPLICATE)
        if gap < 0:
            return Discarded(frame.kind, frame.frame_seq, DiscardReason.OUT_OF_ORDER)
        stream.last_frame_seq = frame.frame_seq
        return gap

    def _frame_quality(self, kind: FrameKind, samples: Samples) -> dict[str, _Quality]:
        if kind is FrameKind.EMG:
            return _emg_quality(scalar_samples(kind, samples), self._full, self._settings.emg_signal_kind)
        if kind is FrameKind.IMU:
            return _imu_quality(_imu_samples(samples))
        return _cord_quality(scalar_samples(kind, samples), self._full)

    def _build(self, kind: FrameKind, samples: Samples, quality: dict[str, _Quality], *, base: dict[str, Any], calibration: StretchCalibration | None) -> Chunk:
        s = self._settings
        header = {
            "schema_version": SCHEMA_VERSION,
            "astronaut_id": s.astronaut_id,
            "device_id": s.device_id,
            "source": "device",
            "boot_id": self._boot_id,
            "n_samples": len(samples),
            **base,
        }
        if kind is FrameKind.EMG:
            counts = scalar_samples(kind, samples)
            offset = emg_offset_counts(s)
            return EmgChunk.model_validate(
                {
                    **header,
                    "stream_id": s.emg_stream_id,
                    "stream_kind": "emg",
                    "signal_kind": s.emg_signal_kind.value,
                    "units": {"emg": "mV"},
                    "data": {"emg": [counts_to_mv(c - offset, adc_bits=s.adc_bits, vref_v=s.vref_v, gain=s.emg_gain) for c in counts]},
                    "quality": {"emg": quality["emg"].contract()},
                    "acquisition": {"adc_bits": s.adc_bits, "vref_v": s.vref_v, "gain": s.emg_gain, "input_pin": s.emg_input_pin},
                }
            )
        if kind is FrameKind.IMU:
            imu = _imu_samples(samples)
            columns = {
                name: [accel_to_g(v[axis], s.imu_accel_range_g) if axis < 3 else gyro_to_dps(v[axis], s.imu_gyro_range_dps) for v in imu]
                for axis, name in enumerate(_IMU_COLUMNS)
            }
            return ImuChunk.model_validate(
                {
                    **header,
                    "stream_id": s.imu_stream_id,
                    "stream_kind": "imu",
                    "units": {name: "g" if name.startswith("accel") else "deg/s" for name in _IMU_COLUMNS},
                    "data": columns,
                    "quality": {"accel": quality["accel"].contract(), "gyro": quality["gyro"].contract()},
                    "config": {"accel_range_g": s.imu_accel_range_g, "gyro_range_dps": s.imu_gyro_range_dps, "dlpf_hz": s.imu_dlpf_hz},
                }
            )
        return self._build_stretch(scalar_samples(kind, samples), quality["adc_counts"], header, calibration)

    def _build_stretch(self, counts: tuple[int, ...], adc_quality: _Quality, header: dict[str, Any], calibration: StretchCalibration | None) -> StretchChunk:
        s = self._settings
        units: dict[str, str] = {"adc_counts": "count"}
        data: dict[str, Any] = {"adc_counts": list(counts)}
        quality: dict[str, Any] = {"adc_counts": adc_quality.contract()}
        extra: dict[str, Any] = {}
        resistances = [counts_to_resistance(c, adc_bits=s.adc_bits, series_resistor_ohm=s.stretch_series_resistor_ohm) for c in counts]
        valid = [r for r in resistances if r is not None]
        if len(valid) == len(resistances):
            units["resistance_ohm"] = "ohm"
            data["resistance_ohm"] = valid
            if calibration is not None:
                points = [(p.resistance_ohm, p.length_mm) for p in calibration.curve.points]
                lengths = [resistance_to_length(r, points) for r in valid]
                units["length_mm"] = "mm"
                data["length_mm"] = [length for length, _ in lengths]
                outside = _fraction((_OUT for _, inside in lengths if not inside), len(lengths))
                quality["length_mm"] = adc_quality.worst(outside).contract()
                extra["calibration_id"] = calibration.calibration_id
        return StretchChunk.model_validate(
            {
                **header,
                "stream_id": s.stretch_stream_id,
                "stream_kind": "stretch",
                "units": units,
                "data": data,
                "quality": quality,
                **extra,
                "circuit": {
                    "adc_bits": s.adc_bits,
                    "vref_v": s.vref_v,
                    "series_resistor_ohm": s.stretch_series_resistor_ohm,
                    "input_pin": s.stretch_input_pin,
                },
            }
        )
