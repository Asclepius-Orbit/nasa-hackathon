"""Device status, clock anchor and background session_id of the real gateway.

Pure logic: no I/O and no clock (``now``/``at`` are supplied by the caller). Payloads come out validated with the
contract models and ready for JSON (``spine_contracts.dump``).

States of a stream (enum ``state`` of ``telemetry/device-status``: ``active``/``stalled``/``disabled``):

- ``disabled``: does not participate (EMG outside a session that includes it) or has not yet sent any batch and
  ``stall_timeout_s`` has not elapsed since the boot was known (the firmware emits each kind every 100 ms: this is the boot margin).
- ``active``: a batch arrived less than ``stall_timeout_s`` ago.
- ``stalled``: participates and has gone ``stall_timeout_s`` without batches (or never sent any once the margin passed; e.g. the firmware does not
  emit ``I`` frames if the MPU does not respond).

Global state (``status`` enum: ``ok``/``degraded``/``offline``): ``offline`` with the serial link down or with no
hello/tick since the port was opened; ``degraded`` if any stream is ``stalled``; otherwise ``ok``.
"""

import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from spine_contracts import SCHEMA_VERSION, ClockSync, DeviceStatus, dump

from spine_gateway.settings import GatewaySettings

BACKGROUND_NAMESPACE = uuid.UUID("5b0d7a0e-3c1f-5e8e-9a4c-2f6d1b7e8a90")
"""Namespace UUID of the background session_id; fixed so that the one for a given day does not change between gateway versions."""
FIRMWARE_VERSION_MAX = 32
"""``maxLength`` of ``firmware_version`` in ``telemetry/device-status``."""
_LINK = {"type": "usb_serial"}


class StreamState(StrEnum):
    ACTIVE = "active"
    STALLED = "stalled"
    DISABLED = "disabled"


class Health(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    OFFLINE = "offline"


@dataclass(frozen=True, slots=True)
class StreamReport:
    """An entry of the DeviceStatus ``streams``; ``None`` omits the field."""

    stream_id: str
    stream_kind: str
    state: StreamState
    sample_rate_hz: float | None
    last_seq: int | None


def stream_state(*, expected: bool, last_frame_at: datetime | None, since: datetime, now: datetime, stall_timeout_s: float) -> StreamState:
    """``since``: when the current boot became known (start of the margin for a stream without data)."""
    if not expected:
        return StreamState.DISABLED
    timeout = timedelta(seconds=stall_timeout_s)
    if last_frame_at is None:
        return StreamState.DISABLED if now - since < timeout else StreamState.STALLED
    return StreamState.ACTIVE if now - last_frame_at < timeout else StreamState.STALLED


def stall_deadline(*, expected: bool, last_frame_at: datetime | None, since: datetime, now: datetime, stall_timeout_s: float) -> datetime | None:
    """Instant at which ``stream_state`` would change without more batches, or ``None`` if it would no longer change by itself."""
    if not expected:
        return None
    deadline = (since if last_frame_at is None else last_frame_at) + timedelta(seconds=stall_timeout_s)
    return deadline if deadline > now else None


def health(*, device_known: bool, states: Iterable[StreamState]) -> Health:
    """``device_known``: the link is up and the device has sent a hello or tick since the port was opened."""
    if not device_known:
        return Health.OFFLINE
    return Health.DEGRADED if StreamState.STALLED in set(states) else Health.OK


def _header(settings: GatewaySettings) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "astronaut_id": settings.astronaut_id, "device_id": settings.device_id}


def device_status(
    settings: GatewaySettings,
    *,
    at: datetime,
    health: Health,
    boot_id: int,
    uptime_ms: int,
    firmware_version: str | None,
    streams: Sequence[StreamReport],
) -> dict[str, Any]:
    """Full DeviceStatus of a known device. No battery or temperature: the Arduino does not measure them."""
    body: dict[str, Any] = {
        **_header(settings),
        "timestamp": at,
        "status": health.value,
        "source": "device",
        "boot_id": boot_id,
        "uptime_ms": uptime_ms,
    }
    if firmware_version is not None:
        body["firmware_version"] = firmware_version[:FIRMWARE_VERSION_MAX]
    body["link"] = _LINK
    body["streams"] = [_stream(s) for s in streams]
    return dump(DeviceStatus.model_validate(body))


def _stream(report: StreamReport) -> dict[str, Any]:
    entry: dict[str, Any] = {"stream_id": report.stream_id, "stream_kind": report.stream_kind, "state": report.state.value}
    if report.sample_rate_hz is not None:
        entry["sample_rate_hz"] = report.sample_rate_hz
    if report.last_seq is not None:
        entry["last_seq"] = report.last_seq
    return entry


def offline_status(settings: GatewaySettings, *, at: datetime) -> dict[str, Any]:
    """``offline`` DeviceStatus (Last Will, serial link down, stop): asserts nothing about the device."""
    return dump(DeviceStatus.model_validate({**_header(settings), "timestamp": at, "status": Health.OFFLINE.value, "source": "device", "link": _LINK}))


def clock_sync(settings: GatewaySettings, *, boot_id: int, device_ms: int, utc: datetime) -> dict[str, Any]:
    """``gateway_receive_time`` anchor: the ``millis`` of a tick and the time at which it reached the gateway."""
    body = {
        **_header(settings),
        "boot_id": boot_id,
        "device_ms": device_ms,
        "utc": utc,
        "uncertainty_ms": settings.clock_uncertainty_ms,
        "method": "gateway_receive_time",
    }
    return dump(ClockSync.model_validate(body))


def background_session_id(settings: GatewaySettings, at: datetime) -> str:
    """Background session_id: deterministic uuid5 of the device and the UTC day of ``at`` (changes at UTC midnight)."""
    day = at.astimezone(UTC).date().isoformat()
    return str(uuid.uuid5(BACKGROUND_NAMESPACE, f"{settings.astronaut_id}/{settings.device_id}/background/{day}"))


def next_after(at: datetime, step: timedelta, now: datetime) -> datetime:
    """First ``at + n·step`` (n >= 0) after ``now``."""
    if at > now:
        return at
    return at + (math.floor((now - at) / step) + 1) * step
