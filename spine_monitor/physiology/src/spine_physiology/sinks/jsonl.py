"""Sink JSONL: una carpeta por astronauta y día.

Dueño: D1. Estructura:
    out/manifest.json                       config (model_dump JSON), semilla, versión del paquete y del contrato
    out/AST-01/profile.json                 el AstronautProfile (último recibido)
    out/AST-01/day-012/<fichero>.jsonl      un fichero por tipo (ver `FILES`), `day` con 3 dígitos
Cada línea es `{"topic": ..., "payload": ...}` para los tipos con topic y `{"payload": ...}` para el
resto (JSON compacto, claves en el orden del payload, UTF-8, `\n`). `close()` escribe el manifest.
"""

from collections.abc import Iterable
from pathlib import Path
from types import TracebackType

from spine_physiology.config import MissionConfig
from spine_physiology.envelope import Envelope, EnvelopeKind

# Fichero de cada tipo dentro de la carpeta del día (el perfil va aparte, en profile.json).
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


class JsonlSink:
    def __init__(self, out_dir: Path, *, config: MissionConfig, seed: int) -> None:
        raise NotImplementedError

    def write(self, envelope: Envelope) -> None:
        raise NotImplementedError

    def close(self) -> None:
        """Cierra los ficheros abiertos y escribe el manifest. Idempotente."""
        raise NotImplementedError

    def __enter__(self) -> JsonlSink:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        self.close()


def write_jsonl(messages: Iterable[Envelope], out_dir: Path, *, config: MissionConfig, seed: int) -> int:
    """Escribe todos los mensajes; devuelve cuántos."""
    raise NotImplementedError
