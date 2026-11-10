"""Opening a database: the schema is versioned and an incompatible file is refused with a clear error."""

import sqlite3
from datetime import UTC, datetime

import pytest
from monitor.db import STORE_SCHEMA_VERSION, IncompatibleDatabaseError, Store

# Tables of an earlier backend prototype found in a real Docker volume: `data` instead of `payload`.
PROTOTYPE = """
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, astronaut_id TEXT NOT NULL, started_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE feature_sets (session_id TEXT PRIMARY KEY, astronaut_id TEXT NOT NULL, session_started_at TEXT,
                           computed_at TEXT NOT NULL, data TEXT NOT NULL);
"""


def user_version(path) -> int:
    with sqlite3.connect(path) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def test_a_new_database_is_stamped_with_the_schema_version(tmp_path):
    Store(tmp_path / "app.db").close()

    assert user_version(tmp_path / "app.db") == STORE_SCHEMA_VERSION


def test_a_database_of_this_schema_without_a_version_is_adopted(tmp_path):
    path = tmp_path / "app.db"
    Store(path).close()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 0")

    Store(path).close()

    assert user_version(path) == STORE_SCHEMA_VERSION


def test_a_prototype_database_is_refused_with_a_clear_error(tmp_path):
    path = tmp_path / "app.db"
    with sqlite3.connect(path) as db:
        db.executescript(PROTOTYPE)

    with pytest.raises(IncompatibleDatabaseError, match="feature_sets") as caught:
        Store(path)

    assert str(path) in str(caught.value)
    assert user_version(path) == 0  # nothing was changed


def test_a_newer_schema_is_refused(tmp_path):
    path = tmp_path / "app.db"
    Store(path).close()
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {STORE_SCHEMA_VERSION + 1}")

    with pytest.raises(IncompatibleDatabaseError, match="newer"):
        Store(path)


def test_the_read_only_store_also_refuses_it(tmp_path):
    path = tmp_path / "app.db"
    with sqlite3.connect(path) as db:
        db.executescript(PROTOTYPE)

    with pytest.raises(IncompatibleDatabaseError):
        Store(path, read_only=True)


def test_a_file_without_the_processing_attempts_table_gets_it_on_open(tmp_path):
    path = tmp_path / "app.db"
    Store(path).close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE processing_attempts")

    store = Store(path)

    assert store.record_processing_failure("s1", "boom", datetime(2026, 11, 10, tzinfo=UTC)) == 1
    store.close()
    assert user_version(path) == STORE_SCHEMA_VERSION


def test_in_memory_databases_still_work():
    store = Store(":memory:")

    assert store.rejected_count() == 0
    store.close()
