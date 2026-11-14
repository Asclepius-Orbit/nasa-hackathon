from datetime import UTC, datetime, timedelta

import pytest
from monitor.db import Store


def test_windows_are_independent_deduplicated_and_ordered():
    store = Store(":memory:")
    try:
        start = datetime(2026, 11, 13, tzinfo=UTC)
        first = {
            "astronaut_id": "AST-01", "session_id": "session",
            "window": {"start": start.isoformat(), "end": (start + timedelta(seconds=5)).isoformat()}, "features": [],
        }
        second = {**first, "window": {"start": (start + timedelta(seconds=5)).isoformat(), "end": (start + timedelta(seconds=10)).isoformat()}}
        assert store.put_measured_window(second, boot_id=1)
        assert store.put_measured_window(first, boot_id=1)
        assert not store.put_measured_window({**first, "features": ["changed"]}, boot_id=1)
        assert store.measured_windows("AST-01", start, start + timedelta(seconds=10)) == [first, second]
        assert store.measured_windows("AST-02", start, start + timedelta(seconds=10)) == []
        assert store.astronaut_known("AST-01")
        assert store.export_astronaut("AST-01")["measured_windows"] == [first, second]
        assert store.put_measured_window(first, boot_id=2)
    finally:
        store.close()


def test_window_store_rejects_ambiguous_or_partial_windows():
    store = Store(":memory:")
    try:
        with pytest.raises(ValueError):
            store.put_measured_window({"session_id": None}, boot_id=1)
        with pytest.raises(ValueError):
            store.put_measured_window({"session_id": "s", "window": {"start": "2026-11-13T00:00:00", "end": "2026-11-13T00:00:05"}}, boot_id=1)
        with pytest.raises(ValueError):
            store.put_measured_window({"session_id": "s", "window": {"start": "2026-11-13T00:00:00Z", "end": "2026-11-13T00:00:04Z"}}, boot_id=1)
    finally:
        store.close()
