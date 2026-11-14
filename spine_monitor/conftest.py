"""Asigna a cada test exactamente un marker de tipo (unit, integration, contract, property, golden)."""

from pathlib import PurePath

import pytest
from hypothesis import settings

pytest_plugins = ["pytester"]  # lo necesita tests/test_markers.py; debe declararse en el conftest raíz

# Sin deadline: en runners lentos un ejemplo lento no debe ser un fallo intermitente. No se deriva la semilla.
settings.register_profile("spine", deadline=None)
settings.load_profile("spine")

KINDS = ("unit", "integration", "contract", "property", "golden")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        declared = sorted({m.name for m in item.iter_markers() if m.name in KINDS})
        if len(declared) > 1:
            raise pytest.UsageError(f"{item.nodeid} has more than one kind marker: {', '.join(declared)}; use exactly one")
        if declared:
            continue
        parts = PurePath(str(item.path)).parts
        if item.get_closest_marker("mosquitto"):
            kind = "integration"
        elif "contracts" in parts and "tests" in parts:
            kind = "contract"
        else:
            kind = "unit"
        item.add_marker(getattr(pytest.mark, kind))
