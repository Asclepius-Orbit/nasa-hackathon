"""Resolución de calibraciones por stream: chunk > sesión, match por stream_id, caducidad (#40)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import FeaturesRequest
from spine_engine.features.calibrations import CalibrationStatus, resolve_calibration

_EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "features-request.json"

SESSION_EMG = "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f12"
OTHER = "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f99"
AT = datetime(2026, 11, 13, 10, 5, tzinfo=UTC)


def calibration(calibration_id: str, stream_id: str, *, valid_until: datetime | None = None) -> dict:
    return {
        "schema_version": "1.0.0",
        "calibration_id": calibration_id,
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "stream_id": stream_id,
        "created_at": "2026-11-13T07:00:00Z",
        "valid_until": valid_until.isoformat() if valid_until else None,
        "source": "device",
        "calibration_type": "emg_mvc",
        "muscle": "latissimus_dorsi",
        "side": "left",
        "mvc_rms_mv": 1.2,
        "window_ms": 500,
        "signal_kind": "raw",
    }


def request(*calibrations: dict) -> FeaturesRequest:
    payload = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    payload["calibrations"] = list(calibrations)
    return FeaturesRequest.model_validate(payload)


def test_session_calibration_is_resolved_by_id_and_stream():
    req = request(calibration(SESSION_EMG, "emg_lat_left"))

    resolution = resolve_calibration(req, "emg_lat_left", at=AT)

    assert resolution.status is CalibrationStatus.RESOLVED
    assert resolution.calibration_id == SESSION_EMG
    assert resolution.calibration is not None and resolution.calibration.calibration_id == SESSION_EMG
    assert resolution.reason is None


def test_chunk_calibration_wins_over_the_session():
    req = request(calibration(SESSION_EMG, "emg_lat_left"), calibration(OTHER, "emg_lat_left"))

    resolution = resolve_calibration(req, "emg_lat_left", chunk_calibration_id=OTHER, at=AT)

    assert resolution.calibration_id == OTHER
    assert resolution.status is CalibrationStatus.RESOLVED


def test_missing_calibration_is_no_calibration():
    resolution = resolve_calibration(request(), "emg_lat_left", at=AT)

    assert resolution.status is CalibrationStatus.MISSING
    assert resolution.calibration_id == SESSION_EMG
    assert resolution.calibration is None
    assert resolution.reason == "no_calibration"


def test_calibration_of_another_stream_does_not_apply():
    resolution = resolve_calibration(request(calibration(SESSION_EMG, "emg_lat_right")), "emg_lat_left", at=AT)

    assert resolution.status is CalibrationStatus.MISSING
    assert resolution.reason == "no_calibration"


@pytest.mark.parametrize(("field", "value"), [("astronaut_id", "AST-02"), ("device_id", "wb-02")])
def test_calibration_from_another_person_or_device_does_not_apply(field, value):
    candidate = calibration(SESSION_EMG, "emg_lat_left")
    candidate[field] = value
    resolution = resolve_calibration(request(candidate), "emg_lat_left", at=AT)
    assert resolution.status is CalibrationStatus.MISSING
    assert resolution.reason == "no_calibration"


def test_calibration_of_another_kind_does_not_apply():
    # imu_t9 es un stream IMU: una calibración emg_mvc con su stream_id no le sirve.
    imu_id = "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f13"
    resolution = resolve_calibration(request(calibration(imu_id, "imu_t9")), "imu_t9", at=AT)

    assert resolution.status is CalibrationStatus.MISSING


def test_stream_without_any_calibration_id_is_no_calibration():
    resolution = resolve_calibration(request(calibration(SESSION_EMG, "undeclared")), "undeclared", at=AT)

    assert resolution.status is CalibrationStatus.MISSING
    assert resolution.calibration_id is None


def test_expired_calibration_is_resolved_but_flagged():
    req = request(calibration(SESSION_EMG, "emg_lat_left", valid_until=AT - timedelta(seconds=1)))

    resolution = resolve_calibration(req, "emg_lat_left", at=AT)

    assert resolution.status is CalibrationStatus.EXPIRED
    assert resolution.calibration is not None
    assert resolution.reason == "calibration_expired"


def test_valid_until_equal_to_the_time_is_still_valid():
    req = request(calibration(SESSION_EMG, "emg_lat_left", valid_until=AT))

    assert resolve_calibration(req, "emg_lat_left", at=AT).status is CalibrationStatus.RESOLVED


def test_without_a_time_expiry_is_not_judged():
    req = request(calibration(SESSION_EMG, "emg_lat_left", valid_until=AT - timedelta(days=30)))

    assert resolve_calibration(req, "emg_lat_left").status is CalibrationStatus.RESOLVED


def test_first_calibration_with_a_repeated_id_wins():
    first = calibration(SESSION_EMG, "emg_lat_left")
    second = calibration(SESSION_EMG, "emg_lat_left", valid_until=AT - timedelta(days=1))

    assert resolve_calibration(request(first, second), "emg_lat_left", at=AT).status is CalibrationStatus.RESOLVED


def test_null_calibrations_list_is_tolerated():
    req = request().model_copy(update={"calibrations": None})

    assert resolve_calibration(req, "emg_lat_left", at=AT).status is CalibrationStatus.MISSING


@pytest.mark.property
@given(st.integers(-(10**6), 10**6))
def test_status_follows_the_sign_of_the_time_to_expiry(delta_s):
    req = request(calibration(SESSION_EMG, "emg_lat_left", valid_until=AT + timedelta(seconds=delta_s)))

    status = resolve_calibration(req, "emg_lat_left", at=AT).status

    assert status is (CalibrationStatus.EXPIRED if delta_s < 0 else CalibrationStatus.RESOLVED)
