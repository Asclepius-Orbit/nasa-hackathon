"""JSONL sink: one folder per astronaut and day.

Owner: D1. Layout:
    out/manifest.json                       config (model_dump JSON), seed, package and contract version, `complete`
    out/AST-01/profile.json                 the AstronautProfile (last one received)
    out/AST-01/day-012/<file>.jsonl         one file per type (see `FILES`), `day` with 3 digits
Each line is `{"topic", "retained", "at", "payload"}` for the types with a topic (chunks, status, clock,
acks, calibrations) and `{"at", "payload"}` for domain objects (compact JSON, keys in payload order,
UTF-8, `\n`, no wall clocks: same bytes with the same seed). `close()` writes the manifest.
Files are opened lazily with a cap on handles: when exceeded, the least recently used one is closed
and reopened (in append mode) when needed again.
"""

import json
from collections import OrderedDict
from collections.abc import Iterable
from importlib import metadata
from pathlib import Path
from types import TracebackType
from typing import IO, Any

from spine_physiology.config import MissionConfig
from spine_physiology.envelope import Envelope, EnvelopeKind

# File for each type inside the day folder (the profile goes separately, in profile.json).
FILES: dict[EnvelopeKind, str] = {
    EnvelopeKind.STREAM_CHUNK: "chunks.jsonl",
    EnvelopeKind.SESSION: "sessions.jsonl",
    EnvelopeKind.CALIBRATION: "calibrations.jsonl",
    EnvelopeKind.SELF_REPORT: "self_reports.jsonl",
    EnvelopeKind.DAILY_CONTEXT: "daily_context.jsonl",
    EnvelopeKind.DEVICE_STATUS: "device_status.jsonl",
    EnvelopeKind.CLOCK_SYNC: "clock_sync.jsonl",
    EnvelopeKind.COMMAND_ACK: "command_acks.jsonl",
    EnvelopeKind.GROUND_TRUTH: "ground_truth.jsonl",
}
PROFILE_FILE = "profile.json"
MANIFEST_FILE = "manifest.json"


def day_dir(out_dir: Path, astronaut_id: str, day: int) -> Path:
    return out_dir / astronaut_id / f"day-{day:03d}"


def _line(envelope: Envelope) -> str:
    record: dict[str, Any] = {}
    if envelope.topic is not None:
        record["topic"] = envelope.topic
        record["retained"] = envelope.retained
    record["at"] = envelope.at.isoformat()
    record["payload"] = envelope.payload
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "unknown"


class JsonlSink:
    def __init__(self, out_dir: Path, *, config: MissionConfig, seed: int, max_open_files: int = 64) -> None:
        # The handle cap is an operating system limit, not a simulation parameter.
        if max_open_files < 1:
            raise ValueError("max_open_files must be >= 1")
        self._out_dir = out_dir
        self._config = config
        self._seed = seed
        self._max_open = max_open_files
        self._open: OrderedDict[Path, IO[str]] = OrderedDict()
        # Files already created in this run: the first open truncates, reopens append.
        self._seen: set[Path] = set()
        self._closed = False

    def _handle(self, path: Path) -> IO[str]:
        handle = self._open.get(path)
        if handle is not None:
            self._open.move_to_end(path)
            return handle
        while len(self._open) >= self._max_open:
            _, oldest = self._open.popitem(last=False)
            oldest.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if path in self._seen else "w"
        handle = path.open(mode, encoding="utf-8", newline="\n")
        self._seen.add(path)
        self._open[path] = handle
        return handle

    def write(self, envelope: Envelope) -> None:
        if self._closed:
            raise ValueError("JsonlSink is closed")
        if envelope.kind is EnvelopeKind.PROFILE:
            path = self._out_dir / envelope.astronaut_id / PROFILE_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(envelope.payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
            return
        if envelope.day is None:
            raise ValueError(f"{envelope.kind} envelope without day")
        path = day_dir(self._out_dir, envelope.astronaut_id, envelope.day) / FILES[envelope.kind]
        self._handle(path).write(_line(envelope))

    def close(self, *, complete: bool = True) -> None:
        """Closes the open files and writes the manifest (`complete` False if the run was interrupted). Idempotent."""
        if self._closed:
            return
        self._closed = True
        for handle in self._open.values():
            handle.close()
        self._open.clear()
        self._out_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "config": self._config.model_dump(mode="json"),
            "seed": self._seed,
            "package_version": _version("spine-physiology"),
            "contracts_version": _version("spine-contracts"),
            "complete": complete,
        }
        (self._out_dir / MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")

    def __enter__(self) -> JsonlSink:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        self.close(complete=exc_type is None)


def write_jsonl(messages: Iterable[Envelope], out_dir: Path, *, config: MissionConfig, seed: int) -> int:
    """Writes all the messages; returns how many."""
    count = 0
    with JsonlSink(out_dir, config=config, seed=seed) as sink:
        for envelope in messages:
            sink.write(envelope)
            count += 1
    return count
