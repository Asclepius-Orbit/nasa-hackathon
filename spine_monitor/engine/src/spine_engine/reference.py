"""Which baseline entry corresponds to a feature."""

from collections.abc import Iterable

from spine_contracts import BaselineEntry, GravityEnvironment

EntryKey = tuple[str, str | None, GravityEnvironment]


def index_entries(entries: Iterable[BaselineEntry]) -> dict[EntryKey, BaselineEntry]:
    return {(e.feature_id, e.protocol_id, e.gravity_environment): e for e in entries}


def reference_entry(
    index: dict[EntryKey, BaselineEntry],
    feature_id: str,
    protocol_id: str | None,
    gravity: GravityEnvironment | None,
) -> BaselineEntry | None:
    """Entry of the same protocol and gravity environment; if it does not exist, the Earth one.

    This way a microgravity session is compared with its own drift and, if there is no baseline of that
    environment yet, with Earth (environment effect). Per-posture resistances always require Earth.
    Without a declared gravity there is no reference.
    """
    if gravity is None:
        return None
    parts = feature_id.split(".")
    if len(parts) == 3 and parts[0] == "stretch" and parts[2] in {"straight_resistance_ohm", "max_flexion_resistance_ohm"}:
        return index.get((feature_id, protocol_id, GravityEnvironment.EARTH_1G))
    for candidate in (gravity, GravityEnvironment.EARTH_1G):
        entry = index.get((feature_id, protocol_id, candidate))
        if entry is not None:
            return entry
    return None
