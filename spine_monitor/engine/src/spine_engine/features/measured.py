"""Five-second, calibrated measurements for the report's device limits.

A request must contain exactly the five-second interval; ambiguous streams, missing
reference effort markers, partial data and an IMU without an initial angle are skipped.
"""
from datetime import timedelta

import numpy as np
from spine_contracts import EmgMvcCalibration, ImuCalibration, StreamKind, StretchCalibration
from spine_contracts.models.domain.calibration_schema import BodyAxis
from spine_contracts.models.domain.feature_set_schema import Reason

from spine_engine.features.context import ExtractionContext
from spine_engine.features.emg import _emg_streams, _mvc, _pieces, _read as read_emg, _rms
from spine_engine.features.registry import ComputedFeature, FamilyResult, SkippedFeature
from spine_engine.features.signals import segment_samples
from spine_engine.features.stretch import _calibration, _read as read_stretch
from spine_engine.measurements import EMG, IMU, STRETCH


class MeasuredFamily:
    family = "measured"
    feature_ids = (STRETCH, IMU, EMG)

    def extract(self, context: ExtractionContext) -> FamilyResult:
        window = context.window
        if window is None or window.end - window.start != timedelta(seconds=5):
            return FamilyResult(skipped=tuple(SkippedFeature(i, Reason.NO_DATA) for i in self.feature_ids))
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
            segments = context.segments(stream.stream_id)
            quality = context.assess([stream.stream_id])
            if len(segments) != 1 or quality.coverage < 1 or quality.flags:
                skipped.append(SkippedFeature(key, Reason.LOW_QUALITY))
                continue
            expected = round(5 * stream.sample_rate_hz)
            if key == STRETCH:
                calibration = _calibration(context, stream.stream_id)
                values = read_stretch(context, stream.stream_id).resistance
                value = 100 * (float(np.mean(values)) - calibration.rest_resistance_ohm) / calibration.rest_resistance_ohm if isinstance(calibration, StretchCalibration) and getattr(calibration, "reference_posture", None) == "straight_back" and len(values) == expected else None
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
                value = 1023 * _rms(pieces) / calibration.mvc_rms_mv if reference_effort and isinstance(calibration, EmgMvcCalibration) and count == expected else None
            else:
                resolution = context.calibration(stream.stream_id)
                calibration = resolution.calibration
                samples = segment_samples(context.clock, segments[0], ("gyro_x",), (window.start, window.end))
                if (resolution.reason is None and isinstance(calibration, ImuCalibration)
                    and calibration.reference_orientation_x_deg is not None and calibration.created_at == window.start
                    and calibration.spine_level == stream.placement.spine_level and calibration.mounting.sensor_x is BodyAxis.LEFT
                    and len(samples.times) == expected):
                    angles = calibration.reference_orientation_x_deg + np.cumsum((samples.channels["gyro_x"] - calibration.gyro_bias_dps.x) / stream.sample_rate_hz)
                    value = float(np.max(angles))
                else:
                    value = None
            if value is None or not np.isfinite(value) or (key == IMU and value < 25) or (key == EMG and not 0 <= value <= 1023):
                skipped.append(SkippedFeature(key, Reason.NO_DATA))
            else:
                computed.append(ComputedFeature(key, value, (stream.stream_id,), expected))
        return FamilyResult(tuple(computed), tuple(skipped))

