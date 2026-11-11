"""Schema migrations with a backup first, the read-only connection next to the writer, retention of raw chunks and the
periodic backup (#66)."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from monitor import db as db_module
from monitor.db import STORE_SCHEMA_VERSION, IncompatibleDatabaseError, Store
from monitor.maintenance import Maintenance

NOW = datetime(2027, 3, 1, tzinfo=UTC)
EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"


def user_version(path) -> int:
    with sqlite3.connect(path) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def session_columns(path) -> list[str]:
    with sqlite3.connect(path) as db:
        return [row[1] for row in db.execute("PRAGMA table_info(sessions)")]


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def stored_session(store: Store, started_at: str, state: str = "processed", n: int = 1) -> str:
    session = example("domain/session.exercise") | {"started_at": started_at, "session_id": f"0b0b0b0b-0000-4000-8000-00000000000{n}"}
    store.insert_session(session, state)
    chunk = example("telemetry/stream-chunk.imu")
    store.add_chunk(chunk | {"session_id": session["session_id"], "seq": chunk["seq"] + 1000 * n})
    return session["session_id"]


def as_version_1(path) -> None:
    """The file as the version-1 app left it: without the column that version 2 added."""
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE sessions DROP COLUMN chunks_purged_at")
        db.execute("PRAGMA user_version = 1")


def test_a_version_1_file_is_backed_up_and_migrated_keeping_its_data(tmp_path):
    path = tmp_path / "app.db"
    store = Store(path)
    session_id = stored_session(store, "2027-02-01T10:00:00Z")
    store.close()
    as_version_1(path)

    migrated = Store(path)

    assert user_version(path) == STORE_SCHEMA_VERSION
    assert "chunks_purged_at" in session_columns(path)
    assert migrated.session(session_id) is not None
    [backup] = (tmp_path / "backups").glob("app.v1.*.db")
    assert user_version(backup) == 1
    migrated.close()


def test_a_failed_migration_leaves_the_file_as_it_was(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    Store(path).close()
    as_version_1(path)

    def broken(db):
        db.execute("ALTER TABLE sessions ADD COLUMN chunks_purged_at TEXT")
        raise RuntimeError("migration bug")

    monkeypatch.setitem(db_module.MIGRATIONS, 1, broken)
    with pytest.raises(RuntimeError):
        Store(path)

    assert user_version(path) == 1
    assert "chunks_purged_at" not in session_columns(path)


def test_the_read_only_connection_refuses_a_file_not_migrated_yet(tmp_path):
    path = tmp_path / "app.db"
    Store(path).close()
    as_version_1(path)

    with pytest.raises(IncompatibleDatabaseError, match="must migrate"):
        Store(path, read_only=True)
    assert user_version(path) == 1


def test_the_read_only_connection_sees_new_writes_and_never_writes(tmp_path):
    path = tmp_path / "app.db"
    writer = Store(path)
    reader = Store(path, read_only=True)

    session_id = stored_session(writer, "2027-02-01T10:00:00Z")

    assert reader.session(session_id) is not None
    with pytest.raises(sqlite3.OperationalError):
        reader.register_crew("AST-09", NOW)
    writer.register_crew("AST-09", NOW)  # the open reader does not lock the writer out
    reader.close()
    writer.close()


def test_raw_chunks_of_old_processed_sessions_are_purged_and_the_rest_kept(tmp_path):
    store = Store(tmp_path / "app.db")
    old = stored_session(store, "2027-01-01T10:00:00Z", n=1)
    recent = stored_session(store, "2027-02-27T10:00:00Z", n=2)
    running = stored_session(store, "2026-12-01T10:00:00Z", "running", n=3)

    assert store.purge_chunks(NOW - timedelta(days=30), NOW) == [old]

    astronaut = store.session(old).payload["astronaut_id"]
    assert store.session_chunks(astronaut, old) == []
    assert store.session(old) is not None
    assert [store.chunks_purged_at(s) for s in (recent, running)] == [None, None]
    assert len(store.session_chunks(astronaut, recent)) == 1
    store.close()


def test_maintenance_keeps_the_newest_copies(tmp_path):
    store = Store(tmp_path / "app.db")
    times = iter([NOW, NOW + timedelta(days=1), NOW + timedelta(days=2)])
    maintenance = Maintenance(store, tmp_path / "backups", interval_s=1, keep=2, chunk_retention_days=None, clock=lambda: next(times))

    copies = [maintenance.run_once() for _ in range(3)]

    assert sorted((tmp_path / "backups").glob("backup-*.db")) == copies[1:]
    store.close()
