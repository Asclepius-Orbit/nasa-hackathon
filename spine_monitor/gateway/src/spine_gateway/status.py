"""Estado del dispositivo, ancla de reloj y session_id de fondo del gateway real.

Lógica pura: sin I/O y sin reloj (``now``/``at`` los pone quien llama). Los payloads salen validados con los modelos del
contrato y listos para JSON (``spine_contracts.dump``).

Estados de un stream (enum ``state`` de ``telemetry/device-status``: ``active``/``stalled``/``disabled``):

- ``disabled``: no participa (el EMG fuera de una sesión que lo incluya) o aún no ha mandado ningún lote y no ha pasado
  ``stall_timeout_s`` desde que se conoció el arranque (el firmware emite cada kind cada 100 ms: es el margen de arranque).
- ``active``: llegó un lote hace menos de ``stall_timeout_s``.
- ``stalled``: participa y lleva ``stall_timeout_s`` sin lotes (o nunca los mandó pasado el margen; p. ej. el firmware no
  emite tramas ``I`` si el MPU no responde).

Estado global (enum ``status``: ``ok``/``degraded``/``offline``): ``offline`` con el enlace serie caído o sin ningún
hello/tick desde que se abrió el puerto; ``degraded`` si algún stream está ``stalled``; si no, ``ok``.
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
"""Namespace UUID del session_id de fondo; fijo para que el de un día no cambie entre versiones del gateway."""
FIRMWARE_VERSION_MAX = 32
"""``maxLength`` de ``firmware_version`` en ``telemetry/device-status``."""
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
    """Una entrada de ``streams`` del DeviceStatus; ``None`` omite el campo."""

    stream_id: str
    stream_kind: str
    state: StreamState
    sample_rate_hz: float | None
    last_seq: int | None


def stream_state(*, expected: bool, last_frame_at: datetime | None, since: datetime, now: datetime, stall_timeout_s: float) -> StreamState:
    """``since``: desde cuándo se conoce el arranque en curso (inicio del margen de un stream sin datos)."""
    if not expected:
        return StreamState.DISABLED
    timeout = timedelta(seconds=stall_timeout_s)
    if last_frame_at is None:
        return StreamState.DISABLED if now - since < timeout else StreamState.STALLED
    return StreamState.ACTIVE if now - last_frame_at < timeout else StreamState.STALLED


def stall_deadline(*, expected: bool, last_frame_at: datetime | None, since: datetime, now: datetime, stall_timeout_s: float) -> datetime | None:
    """Instante en que ``stream_state`` cambiaría sin más lotes, o ``None`` si ya no cambiaría solo."""
    if not expected:
        return None
    deadline = (since if last_frame_at is None else last_frame_at) + timedelta(seconds=stall_timeout_s)
    return deadline if deadline > now else None


def health(*, device_known: bool, states: Iterable[StreamState]) -> Health:
    """``device_known``: el enlace está arriba y el dispositivo ha mandado un hello o tick desde que se abrió el puerto."""
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
    """DeviceStatus completo de un dispositivo conocido. Sin batería ni temperatura: el Arduino no las mide."""
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
    """DeviceStatus ``offline`` (Last Will, enlace serie caído, parada): no afirma nada del dispositivo."""
    return dump(DeviceStatus.model_validate({**_header(settings), "timestamp": at, "status": Health.OFFLINE.value, "source": "device", "link": _LINK}))


def clock_sync(settings: GatewaySettings, *, boot_id: int, device_ms: int, utc: datetime) -> dict[str, Any]:
    """Ancla ``gateway_receive_time``: los ``millis`` de un tick y la hora a la que llegó al gateway."""
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
    """session_id del fondo: uuid5 determinista del dispositivo y del día UTC de ``at`` (cambia a medianoche UTC)."""
    day = at.astimezone(UTC).date().isoformat()
    return str(uuid.uuid5(BACKGROUND_NAMESPACE, f"{settings.astronaut_id}/{settings.device_id}/background/{day}"))


def next_after(at: datetime, step: timedelta, now: datetime) -> datetime:
    """Primer ``at + n·step`` (n >= 0) posterior a ``now``."""
    if at > now:
        return at
    return at + (math.floor((now - at) / step) + 1) * step
