"""Lookup of the fixed facts of a protocol (`contracts/catalog/protocol-catalog.json`) by protocol_id."""

import json
from collections.abc import Mapping
from functools import cache
from typing import Any, cast

from spine_contracts.catalog import CATALOG_DIR


@cache
def _entries() -> Mapping[str, Mapping[str, Any]]:
    """The catalog indexed by protocol_id; read once, shared (do not mutate)."""
    path = CATALOG_DIR / "protocol-catalog.json"
    catalog = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    return {entry["protocol_id"]: entry for entry in catalog["entries"]}


def protocol_entry(protocol_id: str) -> Mapping[str, Any] | None:
    """Catalog entry of the protocol, or None when it is not in the catalog."""
    return _entries().get(protocol_id)


def total_reps_for(protocol_id: str) -> int | None:
    """Planned repetitions of the protocol; None for protocols without them or not in the catalog."""
    entry = protocol_entry(protocol_id)
    return None if entry is None else entry["total_reps"]
