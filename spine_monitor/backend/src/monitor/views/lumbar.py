"""Lumbar length helpers shared by the UI views (history, today, strip).

Pure functions over the payloads of `domain/baseline` and `domain/feature-set`. Absence is `None`, never
a 0: a missing measure or a missing baseline entry gives a null delta.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

ELONGATION_FEATURE = "stretch.lumbar.peak_elongation_mm"

Entry = Mapping[str, Any]


def instant(timestamp: str) -> datetime:
    """RFC 3339 timestamp with offset -> aware UTC datetime."""
    return datetime.fromisoformat(timestamp).astimezone(UTC)


def baseline_entry(baseline: Mapping[str, Any] | None, protocol_id: str, gravity: str, feature_id: str = ELONGATION_FEATURE) -> Entry | None:
    """The baseline entry of `feature_id` for that protocol and gravity; None without baseline or match."""
    if baseline is None:
        return None
    for candidate in baseline["entries"]:
        if (candidate["feature_id"], candidate["protocol_id"], candidate["gravity_environment"]) == (feature_id, protocol_id, gravity):
            return candidate
    return None


def first_lumbar_entry(baseline: Mapping[str, Any] | None) -> Entry | None:
    """First lumbar elongation entry of the baseline, whatever its protocol and gravity."""
    if baseline is None:
        return None
    return next((e for e in baseline["entries"] if e["feature_id"] == ELONGATION_FEATURE), None)


def lumbar_delta_mm(value_mm: float | None, entry: Entry | None) -> float | None:
    """value - baseline mean, in mm; None when the value, the entry or its mean is missing."""
    if value_mm is None or entry is None or "mean" not in entry:
        return None
    return value_mm - entry["mean"]


def preflight_range_mm(entry: Entry | None) -> dict[str, float] | None:
    """Own pre-flight range relative to the mean: {lo: min - mean, hi: max - mean}; None if mean, min or max is missing."""
    if entry is None or not {"mean", "min", "max"} <= entry.keys():
        return None
    return {"lo": entry["min"] - entry["mean"], "hi": entry["max"] - entry["mean"]}


def measured_at(feature_set: Entry) -> datetime:
    """When the features were measured: start of their window, or `computed_at` for a set without window."""
    window = feature_set.get("window")
    return instant(window["start"] if window else feature_set["computed_at"])


def elongation_value(feature_set: Entry) -> float | None:
    return next((f["value"] for f in feature_set["features"] if f["feature_id"] == ELONGATION_FEATURE), None)


def latest_elongation(feature_sets: Sequence[Entry]) -> tuple[float, str, str] | None:
    """(value_mm, protocol_id, gravity) of the most recent set with the lumbar elongation; the last one wins a tie."""
    measures = [(measured_at(fs), i, value, fs) for i, fs in enumerate(feature_sets) if (value := elongation_value(fs)) is not None]
    if not measures:
        return None
    _, _, value, latest = max(measures, key=lambda m: m[:2])
    return value, latest["protocol_id"], latest["gravity_environment"]
