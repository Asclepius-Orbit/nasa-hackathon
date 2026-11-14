"""Lectura de los comandos de ``.../cmd`` y construcción de sus acks (``telemetry/command-ack``).

Lógica pura, el mismo esqueleto que el gateway falso del simulador:

- Por encima de ``PAYLOAD_MAX_BYTES`` ni se parsea: el ``command_id`` se busca con una expresión regular y, si aparece,
  se rechaza con ``invalid_command``.
- Sin ``command_id`` UUID legible (JSON roto, anidado hasta ``RecursionError``, no objeto...) no hay a quién responder:
  se ignora (``None``).
- Se valida contra el JSON Schema (``validation``, que aplica también los ``if/then``) y con ``command_adapter``; los ids
  del payload deben ser los del topic y las duraciones no pueden pasar de ``DURATION_MAX_S``.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from spine_contracts import SCHEMA_VERSION, CalibrateCommand, Command, CommandAck, StartSessionCommand, command_adapter, dump, validation

from spine_gateway.settings import GatewaySettings

PAYLOAD_MAX_BYTES = 16 * 1024
"""Los comandos del contrato ocupan cientos de bytes: por encima de esto ni se parsea (un JSON enorme no debe costar tiempo)."""
MESSAGE_MAX = 300
"""``maxLength`` de ``error.message`` en ``command-ack.schema.json``."""
DURATION_MAX_S = 30 * 86400
"""Tope de ``max_duration_s``/``duration_s``: el esquema no lo acota y un valor enorme desbordaría las esperas."""

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_COMMAND_ID = re.compile(rb'"command_id"\s*:\s*"([0-9a-f-]{36})"')
_COMMAND_SCHEMA = "telemetry/command"
# Constante de módulo: mutmut no muta el módulo, y "ascii" -> "ASCII" sería un mutante equivalente (como en protocol.py).
_ASCII = "ascii"


class AckStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"


class AckCode(StrEnum):
    """``error.code`` del contrato (enum cerrado)."""

    BUSY = "busy"
    UNKNOWN_STREAM = "unknown_stream"
    NO_SESSION = "no_session"
    DEVICE_OFFLINE = "device_offline"
    CALIBRATION_FAILED = "calibration_failed"
    INVALID_COMMAND = "invalid_command"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class Rejection:
    code: AckCode
    message: str


@dataclass(frozen=True, slots=True)
class Incoming:
    """Un comando con ``command_id`` legible: ``command`` si es válido, si no ``rejection``."""

    command_id: str
    command: Command | None
    rejection: Rejection | None


def read_command(payload: bytes, *, astronaut_id: str, device_id: str) -> Incoming | None:
    """Interpreta un payload de ``topics.cmd(astronaut_id, device_id)``; ``None`` si no hay ``command_id`` que responder."""
    if len(payload) > PAYLOAD_MAX_BYTES:
        found = _COMMAND_ID.search(payload[:PAYLOAD_MAX_BYTES])
        command_id = found.group(1).decode(_ASCII) if found else None
        if command_id is None or not _UUID.fullmatch(command_id):
            return None
        message = f"command payload of {len(payload)} bytes exceeds the {PAYLOAD_MAX_BYTES} byte limit"
        return Incoming(command_id, None, Rejection(AckCode.INVALID_COMMAND, message))
    try:
        data: Any = json.loads(payload)
    except ValueError, RecursionError:  # ValueError cubre UnicodeDecodeError y JSONDecodeError
        return None
    command_id = data.get("command_id") if isinstance(data, dict) else None
    if not isinstance(command_id, str) or not _UUID.fullmatch(command_id):
        return None
    rejection = _problems(data, astronaut_id=astronaut_id, device_id=device_id)
    if rejection is not None:
        return Incoming(command_id, None, rejection)
    return Incoming(command_id, command_adapter.validate_python(data), None)


def _problems(data: dict[str, Any], *, astronaut_id: str, device_id: str) -> Rejection | None:
    problems = validation.errors(_COMMAND_SCHEMA, data)
    if problems:
        return Rejection(AckCode.INVALID_COMMAND, "; ".join(problems))
    command = command_adapter.validate_python(data)
    if (command.astronaut_id, command.device_id) != (astronaut_id, device_id):
        return Rejection(AckCode.INVALID_COMMAND, f"command for {command.astronaut_id}/{command.device_id} received on the topic of {astronaut_id}/{device_id}")
    duration = command.max_duration_s if isinstance(command, StartSessionCommand) else command.duration_s if isinstance(command, CalibrateCommand) else None
    # `not <= tope` rechaza también inf (json.loads acepta 1e999).
    if duration is not None and not duration <= DURATION_MAX_S:
        return Rejection(AckCode.INVALID_COMMAND, f"duration exceeds the {DURATION_MAX_S} s limit of this gateway")
    return None


def ack(
    settings: GatewaySettings,
    command_id: str,
    status: AckStatus,
    at: datetime,
    *,
    code: AckCode | None = None,
    message: str | None = None,
    calibration_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "command_id": command_id, "astronaut_id": settings.astronaut_id,
        "device_id": settings.device_id, "timestamp": at, "status": status.value,
    }  # fmt: skip
    if code is not None:
        body["error"] = {"code": code.value} if message is None else {"code": code.value, "message": message[:MESSAGE_MAX]}
    if calibration_id is not None:
        body["result"] = {"calibration_id": calibration_id}
    return dump(CommandAck.model_validate(body))
