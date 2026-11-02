"""Asigna a cada test exactamente un marker de tipo (unit, integration, contract, property, golden)."""

from pathlib import PurePath

import pytest

pytest_plugins = ["pytester"]  # lo necesita tests/test_markers.py; debe declararse en el conftest raíz

KINDS = ("unit", "integration", "contract", "property", "golden")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if any(item.get_closest_marker(kind) for kind in KINDS):
            continue
        parts = PurePath(str(item.path)).parts
        if item.get_closest_marker("mosquitto"):
            kind = "integration"
        elif "contracts" in parts and "tests" in parts:
            kind = "contract"
        else:
            kind = "unit"
        item.add_marker(getattr(pytest.mark, kind))
