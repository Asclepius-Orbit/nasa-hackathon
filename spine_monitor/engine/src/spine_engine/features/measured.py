"""Five-second, calibrated measurements for the report's device limits.

The latest five seconds are measured. IMU integration requires continuous samples
from its absolute reference. Ambiguous streams and missing references are skipped.
"""
from collections.abc import Sequence
from datetime import timedelta

import numpy as np
from spine_contracts import EmgMvcCalibration, ImuCalibration, StreamKind, StretchCalibration
from spine_contracts.models.domain.calibration_schema import BodyAxis
from spine_contracts.models.domain.feature_set_schema import Reason, Window

from spine_engine.features.context import ExtractionContext
from spine_engine.features.emg import _emg_streams, _mvc, _pieces, _rms
from spine_engine.features.emg import _read as read_emg
from spine_engine.features.registry import ComputedFeature, FamilyResult, SkippedFeature
from spine_engine.features.signals import segment_samples
from spine_engine.features.stretch import _calibration, _span
from spine_engine.features.stretch import _read as read_stretch
from spine_engine.measurements import EMG, IMU, STRETCH


class MeasuredFamily:
    family = "measured"
    feature_ids: Sequence[str] = (STRETCH, IMU, EMG)

    def extract(self, context: ExtractionContext) -> FamilyResult:
        window = context.window
        if window is None or window.end - window.start < timedelta(seconds=5):
            return FamilyResult()
        window = Window(start=window.end - timedelta(seconds=5), end=window.end)
        computed = []
        skipped = []
        for key, kind in ((STRETCH, StreamKind.STRETCH), (IMU, StreamKind.IMU), (EMG, StreamKind.EMG)):
            streams = [s for s in context.session.streams if s.stream_kind is kind and (
                key != STRETCH or getattr(getattr(s.placement, "spine_region", None), "value", None) == "full_spine") and (
                key != IMU or getattr(getattr(s.placement, "spine_level", None), "value", None) in {"l1", "l2", "l3", "l4", "l5"})]
            if len(streams) != 1:
                skipped.append(SkippedFeature(key, Reason.STREAM_MISSING if not streams else Reason.NO_DATA))
                continue
            stream = streams[0]
            reference_calibration = None
            if key == STRETCH:
                reference_calibration = _calibration(context, stream.stream_id)
                if not isinstance(reference_calibration, StretchCalibration) or reference_calibration.reference_posture != "straight_back":
                    skipped.append(SkippedFeature(key, Reason.NO_CALIBRATION))
                    continue
            segments = context.segments(stream.stream_id)
            quality = context.assess([stream.stream_id])
            if len(segments) != 1 or quality.coverage < 1 or quality.flags:
                skipped.append(SkippedFeature(key, Reason.LOW_QUALITY))
                continue
            expected = round(5 * stream.sample_rate_hz)
            channel = {STRETCH: "adc_counts", IMU: "gyro_x", EMG: "emg"}[key]
            timed = segment_samples(context.clock, segments[0], (channel,), (window.start, window.end))
            cadence_ok = len(timed.times) == expected and all(
                abs((instant - window.start).total_seconds() - index / stream.sample_rate_hz) <= 1e-6
                for index, instant in enumerate(timed.times)
            )
            if not cadence_ok or any(c.sample_rate_hz != stream.sample_rate_hz for c in segments[0].chunks):
                skipped.append(SkippedFeature(key, Reason.LOW_QUALITY))
                continue
            if key == STRETCH:
                series = read_stretch(context, stream.stream_id)
                values = series.resistance[_span(series.micros, window.start, window.end)]
                value = None
                if isinstance(reference_calibration, StretchCalibration) and len(values) == expected:
                    value = 100 * (float(np.mean(values)) - reference_calibration.rest_resistance_ohm) / reference_calibration.rest_resistance_ohm
            elif key == EMG:
                events = context.session.events or []
                markers = sorted((e.timestamp, e.event_type.value) for e in events if e.event_type.value in {"reference_effort_start", "reference_effort_end"})
                before = [m for m in markers if m[0] <= window.start]
                after = [m for m in markers if m[0] >= window.end]
                during = [m for m in markers if window.start < m[0] < window.end]
                reference_effort = bool(before and after and before[-1][1] == "reference_effort_start" and after[0][1] == "reference_effort_end" and not during)
                info = next((s for s in _emg_streams(context) if s.stream_id == stream.stream_id), None)
                calibration = _mvc(context, info) if info else Reason.NO_CALIBRATION
                pieces = _pieces(read_emg(context, stream.stream_id), [(window.start, window.end)])
                count = sum(len(p.values) for p in pieces)
                value = None
                if reference_effort and isinstance(calibration, EmgMvcCalibration) and count == expected:
                    value = 1023 * _rms(pieces) / calibration.mvc_rms_mv
            else:
                resolution = context.calibration(stream.stream_id)
                calibration = resolution.calibration
                samples = segment_samples(context.clock, segments[0], ("gyro_x",))
                reference = calibration.created_at if isinstance(calibration, ImuCalibration) else window.end
                reference_times = [t for t in samples.times if reference <= t < window.end]
                duration = (window.end - reference).total_seconds()
                continuous = duration >= 5 and len(reference_times) == round(duration * stream.sample_rate_hz) and all(
                    abs((t - reference).total_seconds() - i / stream.sample_rate_hz) <= 1e-6
                    for i, t in enumerate(reference_times)
                )
                if (resolution.reason is None and isinstance(calibration, ImuCalibration)
                    and calibration.reference_orientation_x_deg is not None and continuous
                    and calibration.spine_level == stream.placement.spine_level and calibration.mounting.sensor_x is BodyAxis.LEFT
                ):
                    mask = np.asarray([reference <= t < window.end for t in samples.times], dtype=np.bool_)
                    angles = calibration.reference_orientation_x_deg + np.cumsum(
                        (samples.channels["gyro_x"][mask] - calibration.gyro_bias_dps.x) / stream.sample_rate_hz
                    )
                    value = float(np.max(angles[-expected:]))
                else:
                    value = None
            if value is None or not np.isfinite(value) or (key == IMU and not 25 <= value <= 180) or (key == EMG and not 0 <= value <= 1023):
                skipped.append(SkippedFeature(key, Reason.NO_DATA))
            else:
                computed.append(ComputedFeature(key, value, (stream.stream_id,), expected, window=(window.start, window.end)))
        return FamilyResult(tuple(computed), tuple(skipped))

