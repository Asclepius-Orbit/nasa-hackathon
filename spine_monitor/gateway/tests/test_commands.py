"""Lectura de comandos (tamaño, command_id, esquema, ids) y construcción de acks: lógica pura."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from spine_contracts import CalibrateCommand, StartSessionCommand, validation
from spine_gateway import commands
from spine_gateway.commands import AckCode, AckStatus, Rejection
from spine_gateway.settings import GatewaySettings

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
CID = "c0ffee00-0000-4000-8000-000000000001"
SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"


def start(**fields: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0", "command_id": CID, "astronaut_id": "AST-01", "device_id": "wb-01",
        "issued_at": "2026-11-02T09:00:00Z", "type": "start_session", "session_id": SESSION, "session_type": "exercise",
        "protocol_id": "lat_pulldown_standing_v1", **fields,
    }  # fmt: skip


def calibrate(**fields: Any) -> dict[str, Any]:
    body = {**start(), "type": "calibrate", "calibration_type": "stretch", "stream_ids": ["stretch_lumbar"], **fields}
    del body["session_type"], body["protocol_id"]
    return body


def read(payload: dict[str, Any] | bytes) -> commands.Incoming | None:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return commands.read_command(raw, astronaut_id="AST-01", device_id="wb-01")


def test_a_valid_command_is_parsed() -> None:
    incoming = read(start(stream_ids=["imu_l3"], max_duration_s=60))
    assert incoming is not None and incoming.rejection is None
    assert incoming.command_id == CID
    assert isinstance(incoming.command, StartSessionCommand) and incoming.command.stream_ids == ["imu_l3"]


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff not json",
        b"[1, 2, 3]",
        b"[" * 10_000 + b"]" * 10_000,  # RecursionError en json.loads
        json.dumps({"type": "start_session"}).encode(),
        json.dumps(start(command_id="not-a-uuid")).encode(),
        json.dumps(start(command_id=12)).encode(),
        json.dumps(start(command_id="C0FFEE00-0000-4000-8000-000000000001")).encode(),
        b'{"padding": "' + b"x" * 20_000 + b'"}',
    ],
)
def test_without_a_readable_command_id_the_command_is_ignored(payload: bytes) -> None:
    assert read(payload) is None


def test_the_size_limit_is_inclusive_and_an_oversized_command_is_rejected_by_its_command_id() -> None:
    limit = commands.PAYLOAD_MAX_BYTES
    assert limit == 16 * 1024
    base = json.dumps(start()).encode()
    at_limit = read(base + b" " * (limit - len(base)))
    assert at_limit is not None and at_limit.rejection is None
    over = read(base + b" " * (limit + 1 - len(base)))
    assert over is not None and over.command is None and over.command_id == CID
    assert over.rejection == Rejection(AckCode.INVALID_COMMAND, f"command payload of {limit + 1} bytes exceeds the {limit} byte limit")


def test_an_oversized_command_id_is_found_with_spaces_around_the_colon() -> None:
    raw = b'{"command_id" :  "' + CID.encode() + b'", "padding": "' + b"x" * 20_000 + b'"}'
    incoming = read(raw)
    assert incoming is not None and incoming.command_id == CID and incoming.rejection is not None
    # Solo se busca en los primeros PAYLOAD_MAX_BYTES: un command_id más allá no cuenta.
    assert read(b'{"padding": "' + b"x" * 20_000 + b'", "command_id": "' + CID.encode() + b'"}') is None


def test_a_schema_violation_is_rejected_with_the_problems() -> None:
    missing = start()
    del missing["protocol_id"]
    incoming = read(missing)
    assert incoming is not None and incoming.command is None and incoming.rejection is not None
    assert incoming.rejection.code is AckCode.INVALID_COMMAND
    assert incoming.rejection.message == "; ".join(validation.errors("telemetry/command", missing)) != ""


def test_a_command_for_another_device_is_rejected() -> None:
    incoming = read(start(astronaut_id="AST-02", device_id="wb-02"))
    assert incoming is not None and incoming.rejection == Rejection(AckCode.INVALID_COMMAND, "command for AST-02/wb-02 received on the topic of AST-01/wb-01")
    other_device = read(start(device_id="wb-02"))
    assert other_device is not None and other_device.rejection is not None
    other_astronaut = read(start(astronaut_id="AST-02"))
    assert other_astronaut is not None and other_astronaut.rejection is not None


@pytest.mark.parametrize("payload", [start(max_duration_s=30 * 86400 + 1), calibrate(duration_s=1e300)])
def test_huge_durations_are_rejected(payload: dict[str, Any]) -> None:
    incoming = read(payload)
    assert incoming is not None and incoming.rejection == Rejection(AckCode.INVALID_COMMAND, "duration exceeds the 2592000 s limit of this gateway")


def test_an_infinite_duration_is_rejected() -> None:
    raw = json.dumps(calibrate()).encode()[:-1] + b', "duration_s": 1e999}'
    incoming = read(raw)
    assert incoming is not None and incoming.rejection is not None and "duration exceeds" in incoming.rejection.message


def test_durations_at_the_limit_are_accepted() -> None:
    incoming = read(start(max_duration_s=30 * 86400))
    assert incoming is not None and incoming.rejection is None
    parsed = read(calibrate(duration_s=2.5))
    assert parsed is not None and isinstance(parsed.command, CalibrateCommand) and parsed.rejection is None


# --- acks ---


def test_ack_without_error_or_result(settings: GatewaySettings) -> None:
    payload = commands.ack(settings, CID, AckStatus.ACCEPTED, T0)
    assert payload == {
        "schema_version": "1.0.0", "command_id": CID, "astronaut_id": "AST-01", "device_id": "wb-01",
        "timestamp": "2026-11-02T09:00:00Z", "status": "accepted",
    }  # fmt: skip
    assert validation.errors("telemetry/command-ack", payload) == []


def test_ack_with_an_error_truncates_the_message(settings: GatewaySettings) -> None:
    payload = commands.ack(settings, CID, AckStatus.REJECTED, T0, code=AckCode.BUSY, message="x" * 400)
    assert payload["error"] == {"code": "busy", "message": "x" * 300}
    assert validation.errors("telemetry/command-ack", payload) == []
    bare = commands.ack(settings, CID, AckStatus.FAILED, T0, code=AckCode.INTERNAL)
    assert bare["error"] == {"code": "internal"}


def test_ack_with_a_calibration_result(settings: GatewaySettings) -> None:
    calibration_id = "11111111-2222-4333-8444-555555555555"
    payload = commands.ack(settings, CID, AckStatus.COMPLETED, T0, calibration_id=calibration_id)
    assert payload["result"] == {"calibration_id": calibration_id} and "error" not in payload
    assert validation.errors("telemetry/command-ack", payload) == []


def test_ack_codes_are_the_contract_enum() -> None:
    schema = json.loads((validation.SCHEMAS_DIR / "telemetry" / "command-ack.schema.json").read_text(encoding="utf-8"))
    assert [c.value for c in AckCode] == schema["properties"]["error"]["properties"]["code"]["enum"]
    assert [s.value for s in AckStatus] == schema["properties"]["status"]["enum"]
