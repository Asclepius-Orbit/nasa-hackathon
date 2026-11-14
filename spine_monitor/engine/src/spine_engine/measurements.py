"""Measured device limits from Polfer and Aguado, evaluated independently of deviations.

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
SOURCE = "Polfer and Aguado device report"


def measurements(feature_sets: list[FeatureSet], as_of: datetime) -> dict[str, float]:
    """Select the newest usable five-second measurement for each sensor."""
    found: dict[str, tuple[datetime, float]] = {}
    for fs in feature_sets:
        window = fs.window
        if window is None or window.end - window.start != timedelta(seconds=5):
            continue
        if not as_of - timedelta(seconds=5) <= window.end <= as_of:
            continue
        for f in fs.features:
            if f.feature_id not in UNITS or f.unit != UNITS[f.feature_id] or not math.isfinite(f.value) or f.quality.score <= 0:
                continue
            if f.feature_id == IMU and f.value < 25:
                continue
            if f.feature_id == EMG and not 0 <= f.value <= 1023:
                continue
            previous = found.get(f.feature_id)
            # With equal timestamps use the more conservative reading, independent of input order.
            if (previous is None or window.end > previous[0] or
                    (window.end == previous[0] and (f.value < previous[1] if f.feature_id == EMG else f.value > previous[1]))):
                found[f.feature_id] = (window.end, f.value)
    return {key: value for key, (_, value) in found.items()}


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
