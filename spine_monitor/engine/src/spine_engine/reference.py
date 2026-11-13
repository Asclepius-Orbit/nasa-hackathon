"""Qué entrada de la baseline corresponde a una feature."""

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
    """Entrada del mismo protocolo y entorno de gravedad; si no existe, la de la Tierra.

    Así una sesión en microgravedad se compara con su propia deriva y, si aún no hay baseline de ese
    entorno, con la Tierra (efecto del entorno). Las resistencias por postura requieren siempre Tierra.
    Sin gravedad declarada no hay referencia.
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
