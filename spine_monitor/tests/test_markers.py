from pathlib import Path

import pytest

KINDS = {"unit", "integration", "contract", "property", "golden"}
CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"


def test_every_test_ends_with_exactly_one_kind_marker(pytester):
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    pytester.makepyfile(
        test_plain="""
        def test_a(): pass
        """,
        test_explicit="""
        import pytest
        @pytest.mark.property
        def test_b(): pass
        """,
        test_broker="""
        import pytest
        @pytest.mark.mosquitto
        def test_c(): pass
        """,
    )
    items = pytester.inline_run("--collect-only", "-q").getcalls("pytest_itemcollected")
    kinds = {c.item.name: {m.name for m in c.item.iter_markers()} & KINDS for c in items}
    assert kinds == {"test_a": {"unit"}, "test_b": {"property"}, "test_c": {"integration"}}


@pytest.mark.parametrize("marker", sorted(KINDS | {"mosquitto"}))
def test_markers_are_registered(pytestconfig, marker):
    assert any(line.startswith(f"{marker}:") for line in pytestconfig.getini("markers"))
