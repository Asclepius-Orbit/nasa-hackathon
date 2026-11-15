"""``imu`` family (#44): range of motion and peak angular velocity per plane, and accelerometer RMS.

Based on the gyroscope, which does not depend on gravity (comparable between Earth and microgravity). For each IMU
stream declared in the session, with its ``placement.spine_level`` (never fixed here):

- Plane -> sensor axis with ``calibration.imu.mounting`` (simulator body frame: sagittal = rotation about
  the left-right axis, frontal = about the anterior one, transverse = about the cranial one). The sign follows the axis
  (``left``, ``anterior``, ``cranial`` positive). No sensor axis for a plane: that plane is ``no_calibration``.
- ``gyro_bias_dps`` of the calibration is subtracted from each sensor axis.
- Per repetition (active window, ``start <= t < end``) and plane: ``rom_deg`` = excursion (max - min) of the
  integrated angle (each sample with the period of its chunk) from
  0 before the first sample; ``peak_angular_velocity_dps`` = maximum of ``|ω|``.
- ``accel_rms_g`` = RMS of the accelerometer magnitude after removing each axis's mean over the repetition (the
  static component: gravity and ``accel_bias_g``, which that subtraction already cancels).
- Published value = mean over the repetitions with samples; ``n`` = how many. Each repetition reads the segment
  (``boot_id``) with the most samples in its window: two boots are never joined.

``skipped`` reasons in order: ``stream_missing``, the calibration (``no_calibration``/``calibration_expired``),
``no_data`` (no segments with UTC or no samples in any repetition), ``too_few_repetitions`` (none). The
sampling rate does not discard any feature of this family. Never raises.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray
from spine_contracts import AnatomicalPlane, ImuCalibration, StreamKind
from spine_contracts.catalog import expand_template
from spine_contracts.models.domain.calibration_schema import BodyAxis, Mounting
from spine_contracts.models.domain.feature_set_schema import Reason
from spine_contracts.models.domain.session_schema import StreamDeclaration

from spine_engine.features.context import ExtractionContext
from spine_engine.features.ordering import Segment
from spine_engine.features.registry import ComputedFeature, FamilyResult, SkippedFeature
from spine_engine.features.repetitions import RepetitionService, RepWindow
from spine_engine.features.signals import segment_samples

TEMPLATES = (
    "imu.{spine_level}.{anatomical_plane}.rom_deg",
    "imu.{spine_level}.{anatomical_plane}.peak_angular_velocity_dps",
    "imu.{spine_level}.accel_rms_g",
)

_GYRO = ("gyro_x", "gyro_y", "gyro_z")
_ACCEL = ("accel_x", "accel_y", "accel_z")
# Body axis (positive, negative) of each plane: the same frame as the simulator.
_PLANE_AXES = {
    AnatomicalPlane.SAGITTAL: (BodyAxis.LEFT, BodyAxis.RIGHT),
    AnatomicalPlane.FRONTAL: (BodyAxis.ANTERIOR, BodyAxis.POSTERIOR),
    AnatomicalPlane.TRANSVERSE: (BodyAxis.CRANIAL, BodyAxis.CAUDAL),
}

type Signal = NDArray[np.float64]


def plane_channel(mounting: Mounting, plane: AnatomicalPlane) -> tuple[str, float] | None:
    """Gyroscope channel that measures the rotation of the plane and its sign; ``None`` if the mounting does not cover it."""
    positive, negative = _PLANE_AXES[plane]
    for channel, axis in (("gyro_x", mounting.sensor_x), ("gyro_y", mounting.sensor_y), ("gyro_z", mounting.sensor_z)):
        if axis is positive:
            return channel, 1.0
        if axis is negative:
            return channel, -1.0
    return None


def range_of_motion_deg(omega_dps: Signal, rate_hz: float | Signal) -> float:
    """Excursion of the integrated angle (rectangles of ``1/rate_hz``), with angle 0 before the first sample.

    ``rate_hz`` may be a per-sample rate: each one is integrated with the period of its chunk.
    """
    angle = np.concatenate(([0.0], np.cumsum(omega_dps / rate_hz)))
    return float(angle.max() - angle.min())


def peak_angular_velocity_dps(omega_dps: Signal) -> float:
    """Maximum of ``|ω|``."""
    return float(np.abs(omega_dps).max())


def accel_rms_g(ax: Signal, ay: Signal, az: Signal) -> float:
    """RMS of the dynamic acceleration magnitude (each axis minus its mean)."""
    dynamic = sum((axis - axis.mean()) ** 2 for axis in (ax, ay, az))
    return float(np.sqrt(np.mean(dynamic)))


@dataclass(frozen=True, slots=True)
class _RepSamples:
    sample_rates_hz: Signal  # the chunk rate of each sample: the one used to integrate the ROM
    channels: dict[str, Signal]


class ImuFamily:
    """IMU features per stream, plane and repetition, from the windows of the injected service."""

    family = "imu"
    feature_ids: Sequence[str] = tuple(feature_id for template in TEMPLATES for feature_id in expand_template(template))

    def __init__(self, repetitions: RepetitionService) -> None:
        self.repetitions = repetitions

    def extract(self, context: ExtractionContext) -> FamilyResult:
        computed: list[ComputedFeature] = []
        skipped: list[SkippedFeature] = []
        for stream in context.session.streams:
            if stream.stream_kind is StreamKind.IMU and stream.placement.spine_level is not None:
                result = self._stream(context, stream, stream.placement.spine_level.value)
                computed += result.computed
                skipped += result.skipped
        return FamilyResult(tuple(computed), tuple(skipped))

    def _stream(self, context: ExtractionContext, stream: StreamDeclaration, level: str) -> FamilyResult:
        ids = _ids(level)
        all_ids = [i for plane_ids in ids.values() for i in plane_ids] + [f"imu.{level}.accel_rms_g"]
        stream_id = stream.stream_id
        if context.stream_missing(stream_id):
            return _skip(all_ids, Reason.STREAM_MISSING)
        resolution = context.calibration(stream_id)
        calibration = resolution.calibration
        if resolution.reason is not None or not isinstance(calibration, ImuCalibration):
            return _skip(all_ids, resolution.reason or Reason.NO_CALIBRATION)
        segments = context.segments(stream_id)
        if not segments:
            return _skip(all_ids, Reason.NO_DATA)
        windows = self.repetitions.windows(context).windows
        reps = [found for window in windows if (found := _rep_samples(context, segments, window)) is not None]
        if not reps:
            reason = Reason.NO_DATA if windows else Reason.TOO_FEW_REPETITIONS
            return _skip(all_ids, reason)
        return _compute(stream_id, calibration, ids, reps, level)


def _ids(level: str) -> dict[AnatomicalPlane, tuple[str, str]]:
    return {plane: (f"imu.{level}.{plane.value}.rom_deg", f"imu.{level}.{plane.value}.peak_angular_velocity_dps") for plane in AnatomicalPlane}


def _skip(feature_ids: Sequence[str], reason: Reason) -> FamilyResult:
    return FamilyResult(skipped=tuple(SkippedFeature(feature_id, reason) for feature_id in feature_ids))


def _rep_samples(context: ExtractionContext, segments: Sequence[Segment], window: RepWindow) -> _RepSamples | None:
    best: _RepSamples | None = None
    for segment in segments:
        found = _segment_rep(context, segment, window)
        if len(found.sample_rates_hz) > (0 if best is None else len(best.sample_rates_hz)):
            best = found
    return best


def _segment_rep(context: ExtractionContext, segment: Segment, window: RepWindow) -> _RepSamples:
    """Samples of the window in a segment, chunk by chunk to know the rate of each one."""
    rates: list[float] = []
    parts: dict[str, list[Signal]] = {name: [] for name in _GYRO + _ACCEL}
    for chunk in segment.chunks:
        samples = segment_samples(context.clock, replace(segment, chunks=(chunk,)), _GYRO + _ACCEL, window.active)
        rates += [chunk.sample_rate_hz] * len(samples.times)
        for name, values in samples.channels.items():
            parts[name].append(values)
    channels = {name: np.concatenate(values) for name, values in parts.items()}
    return _RepSamples(np.asarray(rates), channels)


def _compute(
    stream_id: str,
    calibration: ImuCalibration,
    ids: dict[AnatomicalPlane, tuple[str, str]],
    reps: Sequence[_RepSamples],
    level: str,
) -> FamilyResult:
    bias = calibration.gyro_bias_dps
    offsets = {"gyro_x": bias.x, "gyro_y": bias.y, "gyro_z": bias.z}
    n = len(reps)
    computed: list[ComputedFeature] = []
    skipped: list[SkippedFeature] = []

    def add(feature_id: str, per_rep: Sequence[float]) -> None:
        computed.append(ComputedFeature(feature_id, float(np.mean(per_rep)), (stream_id,), n=n))

    for plane, (rom_id, peak_id) in ids.items():
        found = plane_channel(calibration.mounting, plane)
        if found is None:
            skipped += [SkippedFeature(i, Reason.NO_CALIBRATION) for i in (rom_id, peak_id)]
            continue
        channel, sign = found
        omegas = [(sign * (r.channels[channel] - offsets[channel]), r) for r in reps]
        add(rom_id, [range_of_motion_deg(omega, r.sample_rates_hz) for omega, r in omegas])
        add(peak_id, [peak_angular_velocity_dps(omega) for omega, _ in omegas])
    add(f"imu.{level}.accel_rms_g", [accel_rms_g(*(r.channels[name] for name in _ACCEL)) for r in reps])
    return FamilyResult(tuple(computed), tuple(skipped))
