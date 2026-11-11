"""SQLite persistence. The app owns all the state: the engine keeps nothing.

Each entity is stored as its JSON Schema defines it (`payload` column, JSON) next to the columns
needed to look it up. Instants are indexed with `utc_key`: one UTC format for all of them, so text
order is time order even when messages arrive with another offset.

A single connection, used from the event loop (every endpoint is `async`), so there are no
concurrent writes. WAL so that an external reader (e.g. the read-only API) does not block.
"""

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    astronaut_id TEXT NOT NULL, device_id TEXT NOT NULL, stream_id TEXT NOT NULL,
    boot_id INTEGER NOT NULL, seq INTEGER NOT NULL,
    session_id TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (astronaut_id, device_id, stream_id, boot_id, seq)
);
CREATE INDEX IF NOT EXISTS chunks_by_session ON chunks (session_id);

CREATE TABLE IF NOT EXISTS device_status (
    astronaut_id TEXT NOT NULL, device_id TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (astronaut_id, device_id)
);

CREATE TABLE IF NOT EXISTS clock_syncs (
    astronaut_id TEXT NOT NULL, device_id TEXT NOT NULL, boot_id INTEGER NOT NULL, device_ms INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (astronaut_id, device_id, boot_id, device_ms)
);

CREATE TABLE IF NOT EXISTS calibrations (
    calibration_id TEXT PRIMARY KEY, astronaut_id TEXT NOT NULL, device_id TEXT NOT NULL,
    stream_id TEXT NOT NULL, session_id TEXT, created_at TEXT NOT NULL, payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY, astronaut_id TEXT NOT NULL, device_id TEXT NOT NULL,
    started_at TEXT NOT NULL, phase TEXT NOT NULL, state TEXT NOT NULL, error TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_astronaut ON sessions (astronaut_id, started_at);

CREATE TABLE IF NOT EXISTS commands (
    command_id TEXT PRIMARY KEY, astronaut_id TEXT NOT NULL, device_id TEXT NOT NULL,
    type TEXT NOT NULL, session_id TEXT, issued_at TEXT NOT NULL, state TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS commands_by_session ON commands (session_id);

CREATE TABLE IF NOT EXISTS command_acks (
    command_id TEXT NOT NULL, status TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (command_id, status)
);

CREATE TABLE IF NOT EXISTS feature_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, astronaut_id TEXT NOT NULL, session_id TEXT UNIQUE,
    phase TEXT, anchor_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS feature_sets_by_astronaut ON feature_sets (astronaut_id, anchor_at);

CREATE TABLE IF NOT EXISTS baselines (
    baseline_id TEXT PRIMARY KEY, astronaut_id TEXT NOT NULL, version INTEGER NOT NULL,
    created_at TEXT NOT NULL, payload TEXT NOT NULL,
    UNIQUE (astronaut_id, version)
);

CREATE TABLE IF NOT EXISTS assessments (
    assessment_id TEXT PRIMARY KEY, astronaut_id TEXT NOT NULL, as_of TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS assessments_by_astronaut ON assessments (astronaut_id, as_of);

CREATE TABLE IF NOT EXISTS profiles (
    astronaut_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS self_reports (
    report_id TEXT PRIMARY KEY, astronaut_id TEXT NOT NULL, kind TEXT NOT NULL,
    timestamp TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS self_reports_by_astronaut ON self_reports (astronaut_id, timestamp);

CREATE TABLE IF NOT EXISTS daily_context (
    astronaut_id TEXT NOT NULL, date TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (astronaut_id, date)
);

CREATE TABLE IF NOT EXISTS rejected_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, received_at TEXT NOT NULL,
    error TEXT NOT NULL, payload TEXT
);

CREATE TABLE IF NOT EXISTS ground_outbox (
    astronaut_id TEXT NOT NULL, date TEXT NOT NULL, revision INTEGER NOT NULL, mission_day INTEGER,
    state TEXT NOT NULL CHECK (state IN ('queued', 'sent')), payload TEXT NOT NULL, enqueued_at TEXT NOT NULL,
    received_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, next_attempt_at TEXT,
    PRIMARY KEY (astronaut_id, date, revision)
);

CREATE TABLE IF NOT EXISTS processing_attempts (
    session_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL, last_error TEXT, next_at TEXT
);
"""

# Stored in PRAGMA user_version. Bump it with any change to SCHEMA that an existing file would not get
# from CREATE ... IF NOT EXISTS (new columns, changed tables).
STORE_SCHEMA_VERSION = 1


class IncompatibleDatabaseError(RuntimeError):
    """The database file was written with another schema; it is refused before anything is changed."""


def _expected_columns() -> dict[str, list[str]]:
    scratch = sqlite3.connect(":memory:")
    try:
        scratch.executescript(SCHEMA)
        return _columns(scratch)
    finally:
        scratch.close()


def _columns(db: sqlite3.Connection) -> dict[str, list[str]]:
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {table: [row[1] for row in db.execute(f"PRAGMA table_info({table})")] for table in tables}


# At most this many invalid messages are kept, to diagnose without filling the disk.
REJECTED_KEEP = 1000
_REJECTED_PAYLOAD_MAX = 4096


def utc_key(value: str | datetime) -> str:
    """RFC 3339 instant (with offset) -> fixed-width UTC text that sorts as text."""
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    if moment.tzinfo is None:
        raise ValueError(f"naive timestamp: {value!r}")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def day_span(first: date, last: date) -> tuple[datetime, datetime]:
    """UTC instants [start of `first`, last instant of `last`], the inclusive window the queries take."""
    return datetime.combine(first, time.min, UTC), datetime.combine(last, time.min, UTC) + timedelta(days=1, microseconds=-1)


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class SessionRow:
    payload: dict[str, Any]
    state: str
    error: str | None


@dataclass(frozen=True)
class CommandRow:
    payload: dict[str, Any]
    state: str
    acks: list[dict[str, Any]]

    @property
    def type(self) -> str:
        return self.payload["type"]


@dataclass(frozen=True)
class ProcessingAttempt:
    """Failed analyses of a session. `next_at` is `utc_key` text: when the next retry is due."""

    session_id: str
    attempts: int
    last_error: str | None
    next_at: str | None


@dataclass(frozen=True)
class GroundKey:
    """Identity of a ground summary: one revision of the summary of one UTC day of one astronaut."""

    astronaut_id: str
    day: date
    revision: int


@dataclass(frozen=True)
class GroundOutboxItem:
    """A row of the ground outbox. Instants are `utc_key` text (valid RFC 3339); `received_at` is the ground station's receipt."""

    key: GroundKey
    mission_day: int | None
    state: str
    payload: dict[str, Any]
    enqueued_at: str
    received_at: str | None
    attempts: int
    last_error: str | None
    next_attempt_at: str | None


def _ground_item(row: sqlite3.Row) -> GroundOutboxItem:
    return GroundOutboxItem(
        GroundKey(row["astronaut_id"], date.fromisoformat(row["date"]), row["revision"]),
        row["mission_day"], row["state"], json.loads(row["payload"]), row["enqueued_at"], row["received_at"],
        row["attempts"], row["last_error"], row["next_attempt_at"],
    )  # fmt: skip


# Sessions that really ran. `starting` never got an ack and `rejected` never started: they are not served
# by the read-only API.
SERVED_SESSION_STATES = ("running", "ended", "processing", "processed", "processing_failed")


class Store:
    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        """`read_only`: the connection refuses every write (`PRAGMA query_only`), for the read-only API."""
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            self._check_schema(path)
        except IncompatibleDatabaseError:
            self._db.close()
            raise
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        self._db.execute(f"PRAGMA user_version = {STORE_SCHEMA_VERSION}")
        if read_only:
            self._db.execute("PRAGMA query_only=ON")

    def _check_schema(self, path: Path | str) -> None:
        """A file of this schema (or empty) is accepted; anything else is refused untouched."""
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > STORE_SCHEMA_VERSION:
            raise IncompatibleDatabaseError(f"{path}: schema version {version} is newer than this app ({STORE_SCHEMA_VERSION})")
        found = _columns(self._db)
        if version == STORE_SCHEMA_VERSION or not found:
            return
        # Version 0 with tables: adopted only when every table is one of ours, with our columns.
        expected = _expected_columns()
        wrong = sorted(table for table, columns in found.items() if expected.get(table) != columns)
        if wrong:
            raise IncompatibleDatabaseError(
                f"{path}: written by another version of the app (tables with another schema: {', '.join(wrong)}). "
                "Move the file away or point SPINE_APP_DB_PATH to another one."
            )

    def close(self) -> None:
        self._db.close()

    def _window(self, table: str, column: str, where: str, args: tuple, start: datetime, end: datetime, limit: int) -> tuple[list[dict[str, Any]], bool]:
        """Items with `column` in [start, end], oldest first; when more than `limit`, the most recent are kept."""
        rows = self._db.execute(
            f"SELECT payload FROM {table} WHERE {where} AND {column} >= ? AND {column} <= ? ORDER BY {column} DESC LIMIT ?",
            (*args, utc_key(start), utc_key(end), limit + 1),
        ).fetchall()
        items = [json.loads(row["payload"]) for row in rows[:limit]]
        items.reverse()
        return items, len(rows) > limit

    def astronaut_known(self, astronaut_id: str) -> bool:
        """True when anything at all was recorded for the astronaut."""
        tables = ("profiles", "daily_context", "sessions", "self_reports", "assessments", "baselines", "device_status", "feature_sets")
        query = " UNION ALL ".join(f"SELECT 1 FROM {t} WHERE astronaut_id = ?" for t in tables)
        return self._db.execute(f"SELECT EXISTS ({query})", (astronaut_id,) * len(tables)).fetchone()[0] == 1

    def _one(self, sql: str, *args: object) -> dict[str, Any] | None:
        row = self._db.execute(sql, args).fetchone()
        return None if row is None else json.loads(row["payload"])

    def _all(self, sql: str, *args: object) -> list[dict[str, Any]]:
        return [json.loads(row["payload"]) for row in self._db.execute(sql, args)]

    # --- rejected messages -------------------------------------------------------------------

    def record_rejected(self, topic: str, error: str, payload: bytes) -> None:
        text = payload[:_REJECTED_PAYLOAD_MAX].decode("utf-8", errors="replace")
        self._db.execute(
            "INSERT INTO rejected_messages (topic, received_at, error, payload) VALUES (?, ?, ?, ?)",
            (topic, utc_key(datetime.now(UTC)), error[:1000], text),
        )
        self._db.execute(
            "DELETE FROM rejected_messages WHERE id <= (SELECT MAX(id) FROM rejected_messages) - ?", (REJECTED_KEEP,)
        )

    def rejected_count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM rejected_messages").fetchone()[0]

    # --- chunks ----------------------------------------------------------------------------------

    def add_chunk(self, chunk: dict[str, Any]) -> bool:
        """False if it was already stored (QoS 1 redelivery): the key is stream + boot_id + seq."""
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chunk["astronaut_id"], chunk["device_id"], chunk["stream_id"], chunk["boot_id"], chunk["seq"],
                chunk["session_id"], utc_key(chunk["received_at"]), _dumps(chunk),
            ),
        )  # fmt: skip
        return cursor.rowcount == 1

    def session_chunks(self, astronaut_id: str, session_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT payload FROM chunks WHERE astronaut_id = ? AND session_id = ? ORDER BY stream_id, boot_id, seq",
            astronaut_id,
            session_id,
        )

    def stream_chunks(self, astronaut_id: str, device_id: str, session_id: str, stream_id: str) -> Iterator[dict[str, Any]]:
        """One stream of one session in the order it happened, read lazily (a long session has thousands of chunks).

        boot_id is random and seq only means something inside a boot: boots go by the instant their first chunk reached the gateway.
        """
        cursor = self._db.execute(
            "SELECT payload FROM chunks WHERE session_id = ? AND astronaut_id = ? AND device_id = ? AND stream_id = ? "
            "ORDER BY MIN(received_at) OVER (PARTITION BY boot_id), boot_id, seq",
            (session_id, astronaut_id, device_id, stream_id),
        )
        for row in cursor:
            yield json.loads(row["payload"])

    # --- device status and clock anchors ---------------------------------------------

    def put_device_status(self, status: dict[str, Any]) -> bool:
        """Keeps the latest status per device; False when one older than the stored one arrives."""
        key = utc_key(status["timestamp"])
        cursor = self._db.execute(
            "INSERT INTO device_status VALUES (?, ?, ?, ?) "
            "ON CONFLICT (astronaut_id, device_id) DO UPDATE SET timestamp = excluded.timestamp, payload = excluded.payload "
            "WHERE excluded.timestamp >= device_status.timestamp",
            (status["astronaut_id"], status["device_id"], key, _dumps(status)),
        )
        return cursor.rowcount == 1

    def device_status(self, astronaut_id: str, device_id: str) -> dict[str, Any] | None:
        return self._one("SELECT payload FROM device_status WHERE astronaut_id = ? AND device_id = ?", astronaut_id, device_id)

    def device_statuses(self, astronaut_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT payload FROM device_status WHERE astronaut_id = ? ORDER BY device_id", astronaut_id)

    def add_clock_sync(self, anchor: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO clock_syncs VALUES (?, ?, ?, ?, ?)",
            (anchor["astronaut_id"], anchor["device_id"], anchor["boot_id"], anchor["device_ms"], _dumps(anchor)),
        )

    def clock_syncs(self, astronaut_id: str, device_id: str, boot_ids: set[int]) -> list[dict[str, Any]]:
        if not boot_ids:
            return []
        marks = ",".join("?" * len(boot_ids))
        return self._all(
            f"SELECT payload FROM clock_syncs WHERE astronaut_id = ? AND device_id = ? AND boot_id IN ({marks}) "
            "ORDER BY boot_id, device_ms",
            astronaut_id,
            device_id,
            *sorted(boot_ids),
        )

    # --- calibrations ---------------------------------------------------------------------------

    def put_calibration(self, calibration: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO calibrations VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                calibration["calibration_id"], calibration["astronaut_id"], calibration["device_id"],
                calibration["stream_id"], calibration.get("session_id"), utc_key(calibration["created_at"]),
                _dumps(calibration),
            ),
        )  # fmt: skip

    def calibrations(self, calibration_ids: set[str]) -> list[dict[str, Any]]:
        if not calibration_ids:
            return []
        marks = ",".join("?" * len(calibration_ids))
        return self._all(
            f"SELECT payload FROM calibrations WHERE calibration_id IN ({marks}) ORDER BY created_at",
            *sorted(calibration_ids),
        )

    # --- sessions --------------------------------------------------------------------------------

    def insert_session(self, session: dict[str, Any], state: str) -> None:
        self._db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
            (
                session["session_id"], session["astronaut_id"], session["device_id"], utc_key(session["started_at"]),
                session["mission_context"]["phase"], state, _dumps(session),
            ),
        )  # fmt: skip

    def session(self, session_id: str) -> SessionRow | None:
        row = self._db.execute("SELECT payload, state, error FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return None if row is None else SessionRow(json.loads(row["payload"]), row["state"], row["error"])

    def update_session(self, session_id: str, payload: dict[str, Any]) -> None:
        self._db.execute("UPDATE sessions SET payload = ? WHERE session_id = ?", (_dumps(payload), session_id))

    def transition_session(self, session_id: str, from_states: tuple[str, ...], to_state: str, error: str | None = None) -> bool:
        """Changes the state only if it is one of `from_states`, so two acks never process twice."""
        marks = ",".join("?" * len(from_states))
        cursor = self._db.execute(
            f"UPDATE sessions SET state = ?, error = ? WHERE session_id = ? AND state IN ({marks})",
            (to_state, error, session_id, *from_states),
        )
        return cursor.rowcount == 1

    def served_session(self, session_id: str) -> dict[str, Any] | None:
        marks = ",".join("?" * len(SERVED_SESSION_STATES))
        return self._one(f"SELECT payload FROM sessions WHERE session_id = ? AND state IN ({marks})", session_id, *SERVED_SESSION_STATES)

    def latest_served_session(self, astronaut_id: str) -> dict[str, Any] | None:
        marks = ",".join("?" * len(SERVED_SESSION_STATES))
        return self._one(
            f"SELECT payload FROM sessions WHERE astronaut_id = ? AND state IN ({marks}) ORDER BY started_at DESC LIMIT 1",
            astronaut_id,
            *SERVED_SESSION_STATES,
        )

    def sessions_window(self, astronaut_id: str, start: datetime, end: datetime, limit: int) -> tuple[list[dict[str, Any]], bool]:
        marks = ",".join("?" * len(SERVED_SESSION_STATES))
        return self._window("sessions", "started_at", f"astronaut_id = ? AND state IN ({marks})", (astronaut_id, *SERVED_SESSION_STATES), start, end, limit)

    def session_rows(self, astronaut_id: str, *, phase: str | None = None, start: datetime | None = None, end: datetime | None = None) -> list[SessionRow]:
        """Sessions of the astronaut in ANY state, oldest first, optionally of one mission phase and with `started_at` in [start, end]."""
        where, args = ["astronaut_id = ?"], [astronaut_id]
        if phase is not None:
            where.append("phase = ?")
            args.append(phase)
        if start is not None:
            where.append("started_at >= ?")
            args.append(utc_key(start))
        if end is not None:
            where.append("started_at <= ?")
            args.append(utc_key(end))
        rows = self._db.execute(f"SELECT payload, state, error FROM sessions WHERE {' AND '.join(where)} ORDER BY started_at", args).fetchall()
        return [SessionRow(json.loads(row["payload"]), row["state"], row["error"]) for row in rows]

    def sessions_in_states(self, states: Iterable[str]) -> list[SessionRow]:
        """Sessions of any astronaut in one of `states`, oldest first."""
        wanted = list(states)
        marks = ",".join("?" * len(wanted))
        rows = self._db.execute(
            f"SELECT payload, state, error FROM sessions WHERE state IN ({marks}) ORDER BY started_at, session_id", wanted
        ).fetchall()
        return [SessionRow(json.loads(row["payload"]), row["state"], row["error"]) for row in rows]

    def latest_session(self, astronaut_id: str) -> SessionRow | None:
        """The most recently started session in any state."""
        row = self._db.execute("SELECT payload, state, error FROM sessions WHERE astronaut_id = ? ORDER BY started_at DESC LIMIT 1", (astronaut_id,)).fetchone()
        return None if row is None else SessionRow(json.loads(row["payload"]), row["state"], row["error"])

    def active_session(self, astronaut_id: str, device_id: str) -> SessionRow | None:
        row = self._db.execute(
            "SELECT payload, state, error FROM sessions WHERE astronaut_id = ? AND device_id = ? "
            "AND state IN ('starting', 'running') ORDER BY started_at DESC LIMIT 1",
            (astronaut_id, device_id),
        ).fetchone()
        return None if row is None else SessionRow(json.loads(row["payload"]), row["state"], row["error"])

    # --- commands and acks -------------------------------------------------------------------------

    def insert_command(self, command: dict[str, Any], state: str) -> None:
        self._db.execute(
            "INSERT INTO commands VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                command["command_id"], command["astronaut_id"], command["device_id"], command["type"],
                command.get("session_id"), utc_key(command["issued_at"]), state, _dumps(command),
            ),
        )  # fmt: skip

    def command(self, command_id: str) -> CommandRow | None:
        row = self._db.execute("SELECT payload, state FROM commands WHERE command_id = ?", (command_id,)).fetchone()
        if row is None:
            return None
        acks = self._all("SELECT payload FROM command_acks WHERE command_id = ? ORDER BY timestamp", command_id)
        return CommandRow(json.loads(row["payload"]), row["state"], acks)

    def commands_in_state(self, state: str) -> list[CommandRow]:
        """Commands in `state` (acks included), oldest issued first."""
        rows = self._db.execute("SELECT command_id FROM commands WHERE state = ? ORDER BY issued_at, command_id", (state,)).fetchall()
        return [command for row in rows if (command := self.command(row["command_id"])) is not None]

    def set_command_state(self, command_id: str, state: str, *, only_if: tuple[str, ...] | None = None) -> bool:
        if only_if is None:
            cursor = self._db.execute("UPDATE commands SET state = ? WHERE command_id = ?", (state, command_id))
        else:
            marks = ",".join("?" * len(only_if))
            cursor = self._db.execute(
                f"UPDATE commands SET state = ? WHERE command_id = ? AND state IN ({marks})", (state, command_id, *only_if)
            )
        return cursor.rowcount == 1

    def add_ack(self, ack: dict[str, Any]) -> bool:
        """False if that status was already received for the command (redelivery)."""
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO command_acks VALUES (?, ?, ?, ?)",
            (ack["command_id"], ack["status"], utc_key(ack["timestamp"]), _dumps(ack)),
        )
        return cursor.rowcount == 1

    def session_commands(self, session_id: str, command_type: str) -> list[CommandRow]:
        rows = self._db.execute(
            "SELECT command_id FROM commands WHERE session_id = ? AND type = ? ORDER BY issued_at",
            (session_id, command_type),
        ).fetchall()
        return [command for row in rows if (command := self.command(row["command_id"])) is not None]

    # --- processing retries ----------------------------------------------------------------------

    def processing_attempt(self, session_id: str) -> ProcessingAttempt | None:
        row = self._db.execute("SELECT * FROM processing_attempts WHERE session_id = ?", (session_id,)).fetchone()
        return None if row is None else ProcessingAttempt(row["session_id"], row["attempts"], row["last_error"], row["next_at"])

    def record_processing_failure(self, session_id: str, error: str, next_at: str | datetime) -> int:
        """Counts one more failed analysis of the session and schedules the next retry; returns the new count."""
        row = self._db.execute(
            "INSERT INTO processing_attempts VALUES (?, 1, ?, ?) ON CONFLICT (session_id) DO UPDATE SET "
            "attempts = attempts + 1, last_error = excluded.last_error, next_at = excluded.next_at RETURNING attempts",
            (session_id, error, utc_key(next_at)),
        ).fetchone()
        return row["attempts"]

    def reset_processing_attempts(self, session_id: str) -> None:
        self._db.execute("DELETE FROM processing_attempts WHERE session_id = ?", (session_id,))

    def due_processing_retries(self, now: str | datetime, max_attempts: int) -> list[str]:
        """Failed sessions with retries left whose `next_at` has come, earliest first."""
        rows = self._db.execute(
            "SELECT p.session_id FROM processing_attempts p JOIN sessions s ON s.session_id = p.session_id "
            "WHERE s.state = 'processing_failed' AND p.attempts < ? AND p.next_at <= ? ORDER BY p.next_at, p.session_id",
            (max_attempts, utc_key(now)),
        ).fetchall()
        return [row["session_id"] for row in rows]

    # --- feature sets, baselines, assessments ---------------------------------------------------

    def put_feature_set(self, feature_set: dict[str, Any], *, phase: str | None, anchor_at: str) -> None:
        """`anchor_at`: start of the session (or of the window for session-less features), to filter by date."""
        session_id = feature_set["session_id"]
        if session_id is not None:
            self._db.execute("DELETE FROM feature_sets WHERE session_id = ?", (session_id,))
        self._db.execute(
            "INSERT INTO feature_sets (astronaut_id, session_id, phase, anchor_at, payload) VALUES (?, ?, ?, ?, ?)",
            (feature_set["astronaut_id"], session_id, phase, utc_key(anchor_at), _dumps(feature_set)),
        )

    def feature_set(self, session_id: str) -> dict[str, Any] | None:
        return self._one("SELECT payload FROM feature_sets WHERE session_id = ?", session_id)

    def feature_sets_of_phase(self, astronaut_id: str, phase: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT payload FROM feature_sets WHERE astronaut_id = ? AND phase = ? AND session_id IS NOT NULL ORDER BY anchor_at",
            astronaut_id,
            phase,
        )

    def feature_sets_between(self, astronaut_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self._all(
            "SELECT payload FROM feature_sets WHERE astronaut_id = ? AND anchor_at >= ? AND anchor_at <= ? ORDER BY anchor_at",
            astronaut_id,
            utc_key(start),
            utc_key(end),
        )

    def latest_feature_set_with(self, astronaut_id: str, feature_id: str) -> dict[str, Any] | None:
        """The most recent feature set (by `anchor_at`) that has a non-null value for `feature_id`; one row at most."""
        return self._one(
            "SELECT payload FROM feature_sets WHERE astronaut_id = ? AND EXISTS ("
            "SELECT 1 FROM json_each(payload, '$.features') WHERE json_extract(value, '$.feature_id') = ? AND json_extract(value, '$.value') IS NOT NULL"
            ") ORDER BY anchor_at DESC LIMIT 1",
            astronaut_id,
            feature_id,
        )

    def feature_sets_window(self, astronaut_id: str, start: datetime, end: datetime, limit: int) -> tuple[list[dict[str, Any]], bool]:
        return self._window("feature_sets", "anchor_at", "astronaut_id = ?", (astronaut_id,), start, end, limit)

    def put_baseline(self, baseline: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT INTO baselines VALUES (?, ?, ?, ?, ?)",
            (baseline["baseline_id"], baseline["astronaut_id"], baseline["version"], utc_key(baseline["created_at"]), _dumps(baseline)),
        )

    def baseline(self, baseline_id: str) -> dict[str, Any] | None:
        return self._one("SELECT payload FROM baselines WHERE baseline_id = ?", baseline_id)

    def latest_baseline(self, astronaut_id: str) -> dict[str, Any] | None:
        return self._one("SELECT payload FROM baselines WHERE astronaut_id = ? ORDER BY version DESC LIMIT 1", astronaut_id)

    def put_assessment(self, assessment: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT INTO assessments VALUES (?, ?, ?, ?)",
            (assessment["assessment_id"], assessment["astronaut_id"], utc_key(assessment["as_of"]), _dumps(assessment)),
        )

    def assessment(self, assessment_id: str) -> dict[str, Any] | None:
        return self._one("SELECT payload FROM assessments WHERE assessment_id = ?", assessment_id)

    def assessments_window(self, astronaut_id: str, start: datetime, end: datetime, limit: int) -> tuple[list[dict[str, Any]], bool]:
        return self._window("assessments", "as_of", "astronaut_id = ?", (astronaut_id,), start, end, limit)

    def assessments_between(self, astronaut_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Assessments with `as_of` in [start, end], oldest first, without a row limit."""
        return self._all(
            "SELECT payload FROM assessments WHERE astronaut_id = ? AND as_of >= ? AND as_of <= ? ORDER BY as_of",
            astronaut_id,
            utc_key(start),
            utc_key(end),
        )

    def latest_assessment(self, astronaut_id: str) -> dict[str, Any] | None:
        return self._one("SELECT payload FROM assessments WHERE astronaut_id = ? ORDER BY as_of DESC LIMIT 1", astronaut_id)

    # --- profile, self-reports and daily context --------------------------------------------------

    def put_profile(self, profile: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO profiles VALUES (?, ?, ?)",
            (profile["astronaut_id"], utc_key(profile["updated_at"]), _dumps(profile)),
        )

    def profile(self, astronaut_id: str) -> dict[str, Any] | None:
        return self._one("SELECT payload FROM profiles WHERE astronaut_id = ?", astronaut_id)

    def profile_astronaut_ids(self) -> list[str]:
        """Every astronaut that has a profile, in id order."""
        return [row["astronaut_id"] for row in self._db.execute("SELECT astronaut_id FROM profiles ORDER BY astronaut_id")]

    def put_self_report(self, report: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT INTO self_reports VALUES (?, ?, ?, ?, ?)",
            (report["report_id"], report["astronaut_id"], report["kind"], utc_key(report["timestamp"]), _dumps(report)),
        )

    def self_report(self, report_id: str) -> dict[str, Any] | None:
        return self._one("SELECT payload FROM self_reports WHERE report_id = ?", report_id)

    def self_reports_between(self, astronaut_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self._all(
            "SELECT payload FROM self_reports WHERE astronaut_id = ? AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            astronaut_id,
            utc_key(start),
            utc_key(end),
        )

    def self_reports_window(self, astronaut_id: str, start: datetime, end: datetime, limit: int) -> tuple[list[dict[str, Any]], bool]:
        return self._window("self_reports", "timestamp", "astronaut_id = ?", (astronaut_id,), start, end, limit)

    def latest_self_report_of_any_kind(self, astronaut_id: str) -> dict[str, Any] | None:
        return self._one("SELECT payload FROM self_reports WHERE astronaut_id = ? ORDER BY timestamp DESC LIMIT 1", astronaut_id)

    def latest_self_report(self, astronaut_id: str, kind: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT payload FROM self_reports WHERE astronaut_id = ? AND kind = ? ORDER BY timestamp DESC LIMIT 1",
            astronaut_id,
            kind,
        )

    def put_daily_context(self, context: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO daily_context VALUES (?, ?, ?)",
            (context["astronaut_id"], context["date"], _dumps(context)),
        )

    def daily_context(self, astronaut_id: str, date: str) -> dict[str, Any] | None:
        return self._one("SELECT payload FROM daily_context WHERE astronaut_id = ? AND date = ?", astronaut_id, date)

    def daily_contexts_between(self, astronaut_id: str, start: str, end: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT payload FROM daily_context WHERE astronaut_id = ? AND date >= ? AND date <= ? ORDER BY date",
            astronaut_id,
            start,
            end,
        )

    # --- ground outbox -------------------------------------------------------------------------------

    def _immediate[T](self, work: Callable[[], T]) -> T:
        """Runs `work` in one write transaction (the connection is in autocommit): all of it or none of it."""
        self._db.execute("BEGIN IMMEDIATE")
        try:
            result = work()
            self._db.execute("COMMIT")
        except BaseException:
            # A COMMIT that fails may leave the transaction open: without the rollback the next BEGIN would fail forever.
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
        return result

    def upsert_ground_summary(self, astronaut_id: str, day: date, mission_day: int | None, build: Callable[[int], dict[str, Any]], now: datetime) -> int:
        """Queues the summary of a day and returns its revision; `build(revision)` makes the payload (inside the transaction).

        If the latest revision of that day is still queued (not confirmed by the ground) its payload and `enqueued_at` are
        replaced and revision, attempts and backoff are kept; if it was sent, or there is none, the next revision is inserted.
        """

        def work() -> int:
            last = self._db.execute(
                "SELECT revision, state FROM ground_outbox WHERE astronaut_id = ? AND date = ? ORDER BY revision DESC LIMIT 1",
                (astronaut_id, day.isoformat()),
            ).fetchone()
            if last is not None and last["state"] == "queued":
                revision = last["revision"]
                self._db.execute(
                    "UPDATE ground_outbox SET mission_day = ?, payload = ?, enqueued_at = ? WHERE astronaut_id = ? AND date = ? AND revision = ?",
                    (mission_day, _dumps(build(revision)), utc_key(now), astronaut_id, day.isoformat(), revision),
                )
                return revision
            revision = 1 if last is None else last["revision"] + 1
            self._db.execute(
                "INSERT INTO ground_outbox (astronaut_id, date, revision, mission_day, state, payload, enqueued_at) VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                (astronaut_id, day.isoformat(), revision, mission_day, _dumps(build(revision)), utc_key(now)),
            )
            return revision

        return self._immediate(work)

    def next_ground_item(self, now: datetime) -> GroundOutboxItem | None:
        """The next entry to send. Per astronaut only its oldest queued entry (by date, revision) counts, so a newer one never
        overtakes it, even while it waits for its `next_attempt_at`; among the due ones, the oldest (date, revision, astronaut)."""
        row = self._db.execute(
            "SELECT * FROM ground_outbox o WHERE state = 'queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
            "AND NOT EXISTS (SELECT 1 FROM ground_outbox p WHERE p.astronaut_id = o.astronaut_id AND p.state = 'queued' "
            "AND (p.date < o.date OR (p.date = o.date AND p.revision < o.revision))) "
            "ORDER BY date, revision, astronaut_id LIMIT 1",
            (utc_key(now),),
        ).fetchone()
        return None if row is None else _ground_item(row)

    def mark_ground_sent(self, key: GroundKey, received_at: datetime, sent_payload: dict[str, Any]) -> bool:
        """Records the ground's receipt of the entry whose payload was `sent_payload`. True when the entry is sent afterwards.

        Idempotent: an entry already sent keeps its first receipt. If the entry was replaced while it was being sent (its
        payload is no longer `sent_payload`) it stays queued, with the same key, to be sent again: False. Also False if unknown.
        """

        def work() -> bool:
            row = self._db.execute(
                "SELECT state, payload FROM ground_outbox WHERE astronaut_id = ? AND date = ? AND revision = ?",
                (key.astronaut_id, key.day.isoformat(), key.revision),
            ).fetchone()
            if row is None:
                return False
            if row["state"] == "sent":
                return True
            if json.loads(row["payload"]) != sent_payload:
                return False
            self._db.execute(
                "UPDATE ground_outbox SET state = 'sent', received_at = ?, next_attempt_at = NULL WHERE astronaut_id = ? AND date = ? AND revision = ?",
                (utc_key(received_at), key.astronaut_id, key.day.isoformat(), key.revision),
            )
            return True

        return self._immediate(work)

    def mark_ground_failed(self, key: GroundKey, error_code: str, next_attempt_at: datetime) -> bool:
        """A failed send: one more attempt, the error code and when to retry. The state does not change; a sent entry is left alone (False)."""
        cursor = self._db.execute(
            "UPDATE ground_outbox SET attempts = attempts + 1, last_error = ?, next_attempt_at = ? "
            "WHERE astronaut_id = ? AND date = ? AND revision = ? AND state = 'queued'",
            (error_code, utc_key(next_attempt_at), key.astronaut_id, key.day.isoformat(), key.revision),
        )
        return cursor.rowcount == 1

    def ground_outbox_items(self, astronaut_id: str, limit: int) -> list[GroundOutboxItem]:
        """Most recent first (by date, then revision)."""
        rows = self._db.execute("SELECT * FROM ground_outbox WHERE astronaut_id = ? ORDER BY date DESC, revision DESC LIMIT ?", (astronaut_id, limit))
        return [_ground_item(row) for row in rows]

    def latest_ground_summary(self, astronaut_id: str) -> dict[str, Any] | None:
        """Payload of the most recent entry, queued or sent."""
        return self._one("SELECT payload FROM ground_outbox WHERE astronaut_id = ? ORDER BY date DESC, revision DESC LIMIT 1", astronaut_id)

    def last_ground_contact(self, astronaut_id: str | None = None) -> str | None:
        """Latest receipt confirmed by the ground station (derived, not stored twice), of one astronaut or of all."""
        if astronaut_id is None:
            row = self._db.execute("SELECT MAX(received_at) FROM ground_outbox").fetchone()
        else:
            row = self._db.execute("SELECT MAX(received_at) FROM ground_outbox WHERE astronaut_id = ?", (astronaut_id,)).fetchone()
        return row[0]
