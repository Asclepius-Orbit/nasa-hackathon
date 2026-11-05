"""SQLite persistence. The app owns all the state: the engine keeps nothing.

Each entity is stored as its JSON Schema defines it (`payload` column, JSON) next to the columns
needed to look it up. Instants are indexed with `utc_key`: one UTC format for all of them, so text
order is time order even when messages arrive with another offset.

A single connection, used from the event loop (every endpoint is `async`), so there are no
concurrent writes. WAL so that an external reader (e.g. the read-only API) does not block.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
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
"""

# At most this many invalid messages are kept, to diagnose without filling the disk.
REJECTED_KEEP = 1000
_REJECTED_PAYLOAD_MAX = 4096


def utc_key(value: str | datetime) -> str:
    """RFC 3339 instant (with offset) -> fixed-width UTC text that sorts as text."""
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    if moment.tzinfo is None:
        raise ValueError(f"naive timestamp: {value!r}")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        if read_only:
            self._db.execute("PRAGMA query_only=ON")

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
