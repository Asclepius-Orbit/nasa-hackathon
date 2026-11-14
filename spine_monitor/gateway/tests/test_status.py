"""Lógica pura del estado del dispositivo, del ancla de reloj y del session_id de fondo."""

import time
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from spine_contracts import validation
from spine_gateway import status
from spine_gateway.settings import GatewaySettings
from spine_gateway.status import Health, StreamReport, StreamState

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


# --- estado de un stream ---


def test_a_stream_that_does_not_take_part_is_disabled() -> None:
    assert status.stream_state(expected=False, last_frame_at=at(0), since=at(0), now=at(0.1), stall_timeout_s=2.0) is StreamState.DISABLED


@pytest.mark.parametrize(("last", "expected"), [(1.0, StreamState.ACTIVE), (0.0, StreamState.STALLED), (-5.0, StreamState.STALLED), (1.9, StreamState.ACTIVE)])
def test_an_expected_stream_is_active_while_frames_arrive_within_the_stall_timeout(last: float, expected: StreamState) -> None:
    # now = 2 s, timeout 2 s: el lote de hace exactamente 2 s ya no basta.
    assert status.stream_state(expected=True, last_frame_at=at(last), since=at(-10), now=at(2.0), stall_timeout_s=2.0) is expected


@pytest.mark.parametrize(("since", "expected"), [(1.0, StreamState.DISABLED), (0.0, StreamState.STALLED), (-3.0, StreamState.STALLED)])
def test_an_expected_stream_without_data_waits_one_stall_timeout_before_stalling(since: float, expected: StreamState) -> None:
    assert status.stream_state(expected=True, last_frame_at=None, since=at(since), now=at(2.0), stall_timeout_s=2.0) is expected


def test_stall_deadline_is_when_the_state_would_change_without_new_frames() -> None:
    kw = {"now": at(1.0), "stall_timeout_s": 2.0}
    assert status.stall_deadline(expected=True, last_frame_at=at(0.5), since=at(-9), **kw) == at(2.5)
    assert status.stall_deadline(expected=True, last_frame_at=None, since=at(0.2), **kw) == at(2.2)
    # Ya parado, o no participa: nada que vigilar hasta que llegue un lote.
    assert status.stall_deadline(expected=True, last_frame_at=at(-1.0), since=at(-9), **kw) is None
    assert status.stall_deadline(expected=True, last_frame_at=None, since=at(-1.0), **kw) is None
    assert status.stall_deadline(expected=False, last_frame_at=at(0.5), since=at(0), **kw) is None


# --- estado global ---


@pytest.mark.parametrize(
    ("device_known", "states", "expected"),
    [
        (True, [StreamState.ACTIVE, StreamState.ACTIVE, StreamState.DISABLED], Health.OK),
        (True, [StreamState.DISABLED, StreamState.DISABLED], Health.OK),
        (True, [StreamState.ACTIVE, StreamState.STALLED], Health.DEGRADED),
        (True, [StreamState.STALLED], Health.DEGRADED),
        (False, [StreamState.ACTIVE], Health.OFFLINE),
        (False, [StreamState.STALLED], Health.OFFLINE),
    ],
)
def test_health(device_known: bool, states: list[StreamState], expected: Health) -> None:
    assert status.health(device_known=device_known, states=states) is expected


# --- mensajes ---


def test_device_status_reports_the_streams_of_the_usb_serial_link(settings: GatewaySettings) -> None:
    streams = [
        StreamReport("emg_lat_left", "emg", StreamState.ACTIVE, 1000.0, 12),
        StreamReport("imu_t9", "imu", StreamState.STALLED, 10.0, None),
        StreamReport("stretch_spine", "stretch", StreamState.DISABLED, None, None),
    ]
    payload = status.device_status(settings, at=T0, health=Health.DEGRADED, boot_id=7, uptime_ms=1234, firmware_version="fw-1.2.3", streams=streams)
    assert payload == {
        "schema_version": "1.0.0",
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "timestamp": "2026-11-02T09:00:00Z",
        "status": "degraded",
        "source": "device",
        "boot_id": 7,
        "uptime_ms": 1234,
        "firmware_version": "fw-1.2.3",
        "link": {"type": "usb_serial"},
        "streams": [
            {"stream_id": "emg_lat_left", "stream_kind": "emg", "state": "active", "sample_rate_hz": 1000.0, "last_seq": 12},
            {"stream_id": "imu_t9", "stream_kind": "imu", "state": "stalled", "sample_rate_hz": 10.0},
            {"stream_id": "stretch_spine", "stream_kind": "stretch", "state": "disabled"},
        ],
    }
    assert validation.errors("telemetry/device-status", payload) == []


def test_device_status_omits_an_unknown_firmware_and_truncates_a_long_one(settings: GatewaySettings) -> None:
    common = {"at": T0, "health": Health.OK, "boot_id": 1, "uptime_ms": 0, "streams": []}
    assert "firmware_version" not in status.device_status(settings, firmware_version=None, **common)
    long = status.device_status(settings, firmware_version="v" * 40, **common)
    assert long["firmware_version"] == "v" * 32
    assert validation.errors("telemetry/device-status", long) == []


def test_offline_status_says_nothing_about_the_device(settings: GatewaySettings) -> None:
    payload = status.offline_status(settings, at=T0)
    assert payload == {
        "schema_version": "1.0.0",
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "timestamp": "2026-11-02T09:00:00Z",
        "status": "offline",
        "source": "device",
        "link": {"type": "usb_serial"},
    }
    assert validation.errors("telemetry/device-status", payload) == []


def test_clock_sync_anchors_the_tick_at_its_arrival_time(settings: GatewaySettings) -> None:
    payload = status.clock_sync(settings.model_copy(update={"clock_uncertainty_ms": 12.5}), boot_id=9, device_ms=60_000, utc=at(0.25))
    assert payload == {
        "schema_version": "1.0.0",
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "boot_id": 9,
        "device_ms": 60_000,
        "utc": "2026-11-02T09:00:00.250000Z",
        "uncertainty_ms": 12.5,
        "method": "gateway_receive_time",
    }
    assert validation.errors("telemetry/clock-sync", payload) == []


# --- session_id de fondo ---


def test_background_session_id_is_a_uuid5_of_the_device_and_the_utc_day(settings: GatewaySettings) -> None:
    morning = status.background_session_id(settings, at(0))
    assert morning == str(uuid.uuid5(status.BACKGROUND_NAMESPACE, "AST-01/wb-01/background/2026-11-02"))
    assert status.background_session_id(settings, at(14 * 3600 + 59 * 60)) == morning
    assert status.background_session_id(settings, at(15 * 3600)) != morning  # 2026-11-03 00:00 UTC
    # La fecha es la UTC aunque el instante venga en otra zona.
    madrid = (T0 + timedelta(hours=14, minutes=30)).astimezone(timezone(timedelta(hours=1)))
    assert madrid.date().isoformat() == "2026-11-03"
    assert status.background_session_id(settings, madrid) == morning
    other = settings.model_copy(update={"device_id": "wb-02"})
    assert status.background_session_id(other, at(0)) != morning


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="cambiar la zona del proceso necesita time.tzset (Unix)")
def test_background_session_id_ignores_the_local_time_zone(settings: GatewaySettings, monkeypatch: pytest.MonkeyPatch) -> None:
    noon = at(3 * 3600)  # 12:00 UTC del 2 de noviembre: ya es 3 de noviembre en UTC+14
    expected = str(uuid.uuid5(status.BACKGROUND_NAMESPACE, "AST-01/wb-01/background/2026-11-02"))
    monkeypatch.setenv("TZ", "Etc/GMT-14")
    time.tzset()
    try:
        assert status.background_session_id(settings, noon) == expected
    finally:
        monkeypatch.undo()
        time.tzset()


def test_background_namespace_is_fixed() -> None:
    # Cambiarlo cambiaría el session_id de fondo de todos los días ya publicados.
    assert status.BACKGROUND_NAMESPACE == uuid.UUID("5b0d7a0e-3c1f-5e8e-9a4c-2f6d1b7e8a90")


# --- calendario ---


def test_next_after_is_the_first_step_after_now() -> None:
    step = timedelta(seconds=30)
    assert status.next_after(at(10), step, at(0)) == at(10)
    assert status.next_after(at(0), step, at(0)) == at(30)
    assert status.next_after(at(0), step, at(29)) == at(30)
    assert status.next_after(at(0), step, at(30)) == at(60)
    assert status.next_after(at(0), step, at(95)) == at(120)
