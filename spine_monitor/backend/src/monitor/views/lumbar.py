"""Lumbar length helpers shared by the UI views (history, today, strip).

Pure functions over the payloads of `domain/baseline` and `domain/feature-set`. Absence is `None`, never
a 0: a missing measure or a missing baseline entry gives a null delta.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, NamedTuple

ELONGATION_FEATURE = "stretch.lumbar.peak_elongation_mm"
EARTH_GRAVITY = "earth_1g"

Entry = Mapping[str, Any]


def instant(timestamp: str) -> datetime:
    """RFC 3339 timestamp with offset -> aware UTC datetime."""
    return datetime.fromisoformat(timestamp).astimezone(UTC)


class LumbarDelta(NamedTuple):
    """The lumbar change shown on Today: delta against the baseline mean and the instant of the measure behind it."""

    delta_mm: float
    measured_at: datetime


def baseline_entry(baseline: Mapping[str, Any] | None, protocol_id: str | None, gravity: str | None, feature_id: str = ELONGATION_FEATURE) -> Entry | None:
    """The baseline entry of `feature_id` for that protocol and gravity, else the Earth one of that protocol.

    Same rule as the engine's `reference_entry`: a microgravity measure is compared with its own baseline and, if
    there is none yet, with Earth's. Without a declared protocol or gravity there is no reference; entries that lack
    either never match. None without baseline or match.
    """
    if baseline is None or protocol_id is None or gravity is None:
        return None
    entries = baseline["entries"]
    for wanted in dict.fromkeys((gravity, EARTH_GRAVITY)):
        for candidate in entries:
            if (candidate["feature_id"], candidate.get("protocol_id"), candidate.get("gravity_environment")) == (feature_id, protocol_id, wanted):
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


def _latest_elongation(feature_sets: Sequence[Entry]) -> tuple[float, Entry] | None:
    """(value, set) of the most recent set with the lumbar elongation; the last one wins a tie."""
    measures = [(measured_at(fs), i, value, fs) for i, fs in enumerate(feature_sets) if (value := elongation_value(fs)) is not None]
    if not measures:
        return None
    _, _, value, latest = max(measures, key=lambda m: m[:2])
    return value, latest


def latest_elongation(feature_sets: Sequence[Entry]) -> tuple[float, str | None, str | None] | None:
    """(value_mm, protocol_id, gravity) of the most recent set with the lumbar elongation; the last one wins a tie.

    Protocol and gravity are optional in a feature set: absent they are None (and have no baseline reference).
    """
    latest = _latest_elongation(feature_sets)
    if latest is None:
        return None
    value, feature_set = latest
    return value, feature_set.get("protocol_id"), feature_set.get("gravity_environment")


def latest_lumbar_delta(feature_sets: Sequence[Entry], baseline: Mapping[str, Any] | None) -> LumbarDelta | None:
    """Delta of the most recent elongation against its baseline entry, with the instant of that measure.

    None when there is no measure or no baseline entry (or mean) for it: the delta and its instant are null together.
    """
    latest = _latest_elongation(feature_sets)
    if latest is None:
        return None
    value, feature_set = latest
    delta = lumbar_delta_mm(value, baseline_entry(baseline, feature_set.get("protocol_id"), feature_set.get("gravity_environment")))
    return None if delta is None else LumbarDelta(delta, measured_at(feature_set))
