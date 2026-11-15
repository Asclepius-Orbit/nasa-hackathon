"""Measured device limits from the device report, evaluated independently of deviations.

Inputs must be calibrated five-second feature sets. EMG is only valid when explicitly
produced by a reference-effort test, as specified by its feature identifier.
"""

import math
from datetime import datetime, timedelta

from spine_contracts import FeatureSet, Level

STRETCH = "stretch.full_spine.resistance_change_pct"
IMU = "imu.lumbar.sensor_x.orientation_deg"
EMG = "emg.reference.normalized_amplitude"
UNITS = {STRETCH: "%", IMU: "deg", EMG: "counts"}
SOURCE = "Device report"


def measurements(feature_sets: list[FeatureSet], as_of: datetime) -> dict[str, float]:
    """Select the newest usable five-second measurement for each sensor."""
    found: dict[str, tuple[datetime, float]] = {}
    skipped_at: dict[str, datetime] = {}
    observed_at: dict[str, datetime] = {}
    for fs in feature_sets:
        if fs.window is not None and as_of - timedelta(seconds=5) <= fs.window.end <= as_of:
            for omitted in fs.skipped or []:
                if omitted.feature_id in UNITS:
                    skipped_at[omitted.feature_id] = max(fs.window.end, skipped_at.get(omitted.feature_id, fs.window.end))
        for f in fs.features:
            window = f.window or fs.window
            if window is None:
                continue
            if not as_of - timedelta(seconds=5) <= window.end <= as_of:
                continue
            if f.feature_id not in UNITS:
                continue
            observed_at[f.feature_id] = max(window.end, observed_at.get(f.feature_id, window.end))
            valid = (window.end - window.start == timedelta(seconds=5) and f.unit == UNITS[f.feature_id]
                     and math.isfinite(f.value) and f.quality.score > 0 and not f.quality.flags
                     and (f.feature_id != STRETCH or f.value > -100)
                     and (f.feature_id != IMU or 25 <= f.value <= 180)
                     and (f.feature_id != EMG or 0 <= f.value <= 1023))
            if not valid:
                skipped_at[f.feature_id] = max(window.end, skipped_at.get(f.feature_id, window.end))
                continue
            previous = found.get(f.feature_id)
            # With equal timestamps use the more conservative reading, independent of input order.
            if (previous is None or window.end > previous[0] or
                    (window.end == previous[0] and (f.value < previous[1] if f.feature_id == EMG else f.value > previous[1]))):
                found[f.feature_id] = (window.end, f.value)
    return {key: value for key, (end, value) in found.items()
            if end == observed_at[key] and (key not in skipped_at or skipped_at[key] < end)}


def classify(key: str, value: float) -> tuple[Level, str, str, float]:
    """Return level, category, comparison and the governing bound."""
    if key == STRETCH:
        if value >= 7.5:
            return Level.RED, "DANGER", ">=", 7.5
        if value >= 4:
            return Level.AMBER, "CAUTION", ">=", 4
        return Level.GREEN, "NORMAL", "<", 4
    if key == IMU:
        if value >= 42:
            return Level.RED, "DANGER", ">=", 42
        if value >= 35:
            return Level.AMBER, "CAUTION", ">=", 35
        return Level.GREEN, "NORMAL", ">=", 25
    if key == EMG:
        if value < 614:
            return Level.RED, "CRITICAL", "<", 614
        if value < 717:
            return Level.RED, "HIGH", "<", 717
        if value < 819:
            return Level.AMBER, "MODERATE", "<", 819
        if value < 922:
            return Level.AMBER, "MILD", "<", 922
        return Level.GREEN, "NORMAL", ">=", 922
    raise ValueError(key)
