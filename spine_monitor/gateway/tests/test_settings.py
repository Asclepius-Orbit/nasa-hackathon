"""Ajustes del gateway real: defaults, override por entorno y validación."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from spine_gateway.settings import GatewaySettings


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Sin .env del directorio actual ni variables heredadas.
    monkeypatch.chdir(tmp_path)
    for name in [n for n in os.environ if n.startswith("SPINE_GATEWAY_")]:
        monkeypatch.delenv(name)


def test_defaults() -> None:
    s = GatewaySettings()
    assert (s.serial_url, s.serial_baud) == ("COM3", 250000)
    assert (s.astronaut_id, s.device_id) == ("AST-01", "wb-01")
    assert (s.mqtt_host, s.mqtt_port, s.mqtt_client_id) == ("127.0.0.1", 1883, "spine-gateway")
    assert (s.mqtt_reconnect_initial_s, s.mqtt_reconnect_factor, s.mqtt_reconnect_max_s) == (1.0, 2.0, 30.0)
    assert (s.serial_reconnect_initial_s, s.serial_reconnect_factor, s.serial_reconnect_max_s) == (1.0, 2.0, 10.0)
    assert (s.status_interval_s, s.clock_sync_interval_s) == (30.0, 60.0)
    assert (s.emg_stream_id, s.imu_stream_id, s.stretch_stream_id) == ("emg_lat_left", "imu_l3", "stretch_lumbar")
    assert (s.emg_muscle.value, s.emg_side.value) == ("latissimus_dorsi", "left")
    assert (s.adc_bits, s.vref_v, s.emg_input_pin, s.stretch_input_pin) == (10, 5.0, "A0", "A1")
    assert (s.stretch_series_resistor_ohm, s.imu_accel_range_g, s.imu_gyro_range_dps, s.imu_dlpf_hz) == (10000.0, 4, 500, 44)
    assert (s.emg_gain, s.stretch_rest_length_mm, s.emg_mvc_window_ms) == (1.0, 100.0, 250)


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_GATEWAY_SERIAL_URL", "socket://localhost:4000")
    monkeypatch.setenv("SPINE_GATEWAY_ASTRONAUT_ID", "AST-123")
    monkeypatch.setenv("SPINE_GATEWAY_EMG_MUSCLE", "trapezius")
    monkeypatch.setenv("SPINE_GATEWAY_EMG_SIDE", "right")
    monkeypatch.setenv("SPINE_GATEWAY_MQTT_PORT", "1884")
    s = GatewaySettings()
    assert s.serial_url == "socket://localhost:4000"
    assert s.astronaut_id == "AST-123"
    assert (s.emg_muscle.value, s.emg_side.value, s.mqtt_port) == ("trapezius", "right", 1884)


def test_link_timing_defaults() -> None:
    s = GatewaySettings()
    assert (s.clock_uncertainty_ms, s.stall_timeout_s, s.calibration_default_duration_s) == (20.0, 2.0, 5.0)


def test_link_timing_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_GATEWAY_CLOCK_UNCERTAINTY_MS", "35.5")
    monkeypatch.setenv("SPINE_GATEWAY_STALL_TIMEOUT_S", "3")
    monkeypatch.setenv("SPINE_GATEWAY_CALIBRATION_DEFAULT_DURATION_S", "8")
    s = GatewaySettings()
    assert (s.clock_uncertainty_ms, s.stall_timeout_s, s.calibration_default_duration_s) == (35.5, 3.0, 8.0)


@pytest.mark.parametrize("name", ["CLOCK_UNCERTAINTY_MS", "STALL_TIMEOUT_S", "CALIBRATION_DEFAULT_DURATION_S"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_link_timing_must_be_positive(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(f"SPINE_GATEWAY_{name}", value)
    with pytest.raises(ValidationError):
        GatewaySettings()


def test_serial_reconnect_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_GATEWAY_SERIAL_RECONNECT_INITIAL_S", "0.5")
    monkeypatch.setenv("SPINE_GATEWAY_SERIAL_RECONNECT_FACTOR", "3")
    monkeypatch.setenv("SPINE_GATEWAY_SERIAL_RECONNECT_MAX_S", "20")
    s = GatewaySettings()
    assert (s.serial_reconnect_initial_s, s.serial_reconnect_factor, s.serial_reconnect_max_s) == (0.5, 3.0, 20.0)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ASTRONAUT_ID", "ast-01"),
        ("ASTRONAUT_ID", "AST-1"),
        ("ASTRONAUT_ID", "AST-01/../x"),
        ("DEVICE_ID", "W"),
        ("DEVICE_ID", "wb/01"),
        ("DEVICE_ID", "wb-01#"),
        ("EMG_STREAM_ID", "Emg"),
        ("IMU_STREAM_ID", "imu-l3"),
        ("STRETCH_STREAM_ID", "a/b"),
        ("EMG_MUSCLE", "heart"),
        ("EMG_SIDE", "up"),
    ],
)
def test_invalid_ids_rejected(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(f"SPINE_GATEWAY_{name}", value)
    with pytest.raises(ValidationError):
        GatewaySettings()


def test_reconnect_max_below_initial_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_GATEWAY_MQTT_RECONNECT_INITIAL_S", "10")
    monkeypatch.setenv("SPINE_GATEWAY_MQTT_RECONNECT_MAX_S", "5")
    with pytest.raises(ValidationError, match="mqtt_reconnect_max_s"):
        GatewaySettings()


def test_reconnect_max_equal_initial_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_GATEWAY_MQTT_RECONNECT_INITIAL_S", "5")
    monkeypatch.setenv("SPINE_GATEWAY_MQTT_RECONNECT_MAX_S", "5")
    assert GatewaySettings().mqtt_reconnect_max_s == 5.0


def test_serial_reconnect_max_below_initial_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_GATEWAY_SERIAL_RECONNECT_INITIAL_S", "10")
    monkeypatch.setenv("SPINE_GATEWAY_SERIAL_RECONNECT_MAX_S", "5")
    with pytest.raises(ValidationError, match="serial_reconnect_max_s"):
        GatewaySettings()


def test_serial_reconnect_max_equal_initial_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_GATEWAY_SERIAL_RECONNECT_INITIAL_S", "5")
    monkeypatch.setenv("SPINE_GATEWAY_SERIAL_RECONNECT_MAX_S", "5")
    assert GatewaySettings().serial_reconnect_max_s == 5.0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SERIAL_BAUD", "0"),
        ("SERIAL_RECONNECT_INITIAL_S", "0"),
        ("SERIAL_RECONNECT_FACTOR", "0.5"),
        ("SERIAL_RECONNECT_MAX_S", "0"),
        ("MQTT_PORT", "0"),
        ("MQTT_PORT", "65536"),
        ("MQTT_RECONNECT_INITIAL_S", "0"),
        ("MQTT_RECONNECT_FACTOR", "0.5"),
        ("MQTT_RECONNECT_MAX_S", "0"),
        ("STATUS_INTERVAL_S", "0"),
        ("CLOCK_SYNC_INTERVAL_S", "-1"),
        ("ADC_BITS", "0"),
        ("VREF_V", "0"),
        ("STRETCH_SERIES_RESISTOR_OHM", "0"),
        ("IMU_ACCEL_RANGE_G", "0"),
        ("IMU_GYRO_RANGE_DPS", "-500"),
        ("IMU_DLPF_HZ", "0"),
        ("EMG_GAIN", "0"),
        ("STRETCH_REST_LENGTH_MM", "-1"),
        ("EMG_MVC_WINDOW_MS", "0"),
    ],
)
def test_non_positive_values_rejected(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(f"SPINE_GATEWAY_{name}", value)
    with pytest.raises(ValidationError):
        GatewaySettings()
