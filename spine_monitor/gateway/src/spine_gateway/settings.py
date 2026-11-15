"""Real gateway startup (Arduino over USB serial -> MQTT v1). SPINE_GATEWAY_* variables or `.env`.

This holds what the firmware cannot say by itself: where the port is, which broker to connect to, which
astronaut/device the data is attributed to and the factory wiring/calibration of the hardware. The firmware
version is not configured: it arrives in the device hello.
"""

import re
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from spine_contracts import topics
from spine_contracts.catalog import load_calibration_defaults
from spine_contracts.models.common_schema import Muscle, Side
from spine_contracts.models.telemetry.stream_chunk_schema import SignalKind

_CALIBRATION_DEFAULTS = load_calibration_defaults()
DEFAULT_STRETCH_CURVE: tuple[tuple[float, float], ...] = tuple(
    (float(p["resistance_ohm"]), float(p["length_mm"])) for p in _CALIBRATION_DEFAULTS["stretch"]["curve"]
)
"""Default cord curve, from ``contracts/catalog/calibration-defaults.json`` (the same as the example mission).

Absolute points (R ohm, L mm) relative to the reference rest (``rest_resistance_ohm``, ``rest_length_mm`` of the same
file). Calibration scales them to the measured rest: R0 · R / R_ref and L0 · L / L_ref. Unmeasured values (PENDING
measurement); change them with SPINE_GATEWAY_STRETCH_CURVE when the cord is measured. The cord is not linear and has hysteresis.
"""


def _matches(kind: str, value: str, pattern: re.Pattern[str]) -> str:
    # Same expressions that `topics` uses to build topics: an id valid here never breaks a topic.
    if not pattern.fullmatch(value):
        raise ValueError(f"invalid {kind}: {value!r}")
    return value


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPINE_GATEWAY_", env_file=".env", extra="ignore")

    # Serial port as understood by pyserial `serial_for_url`: COM3, /dev/ttyACM0 or socket://host:port.
    serial_url: str = Field(default="COM3", min_length=1)
    serial_baud: int = Field(default=9600, gt=0)
    # Serial port reconnection with exponential backoff if it does not open or is unplugged: initial wait (s), factor and cap (s).
    serial_reconnect_initial_s: float = Field(default=1.0, gt=0)
    serial_reconnect_factor: float = Field(default=2.0, ge=1)
    serial_reconnect_max_s: float = Field(default=10.0, gt=0)

    astronaut_id: str = "AST-01"
    device_id: str = "wb-01"

    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = Field(default=1883, ge=1, le=65535)
    mqtt_client_id: str = "spine-gateway"
    mqtt_username: str | None = None
    mqtt_password: SecretStr | None = None
    mqtt_tls: bool = False
    mqtt_tls_ca_file: Path | None = None
    # Reconnection with exponential backoff if the broker rejects the connection or drops it: initial wait (s),
    # factor per failed attempt and cap (s). Returns to the initial value after connecting.
    mqtt_reconnect_initial_s: float = Field(default=1.0, gt=0)
    mqtt_reconnect_factor: float = Field(default=2.0, ge=1)
    mqtt_reconnect_max_s: float = Field(default=30.0, gt=0)

    # Periods (s) of the status messages and of the clock synchronisation with the device.
    status_interval_s: float = Field(default=30.0, gt=0)
    clock_sync_interval_s: float = Field(default=60.0, gt=0)
    # Estimated error (ms) of the clock anchor: tick arrival time at the gateway (USB latency + line read).
    clock_uncertainty_ms: float = Field(default=20.0, gt=0)
    # A stream without batches for this time (s) becomes `stalled` and the status `degraded`.
    stall_timeout_s: float = Field(default=2.0, gt=0)
    # Duration (s) of a `calibrate` without `duration_s`.
    calibration_default_duration_s: float = Field(default=5.0, gt=0)

    emg_stream_id: str = "emg_lat_left"
    imu_stream_id: str = "imu_t9"
    stretch_stream_id: str = "stretch_spine"

    # EMG electrode placement (same as EmgMvcCalibration and the example mission YAML).
    emg_muscle: Muscle = Muscle.LATISSIMUS_DORSI
    emg_side: Side = Side.LEFT

    # Wearable hardware.
    adc_bits: int = Field(default=10, gt=0)
    vref_v: float = Field(default=5.0, gt=0)
    emg_input_pin: str = Field(default="A0", min_length=1)
    stretch_input_pin: str = Field(default="A1", min_length=1)
    stretch_series_resistor_ohm: float = Field(default=10000.0, gt=0)
    imu_accel_range_g: int = Field(default=4, gt=0)
    imu_gyro_range_dps: int = Field(default=500, gt=0)
    imu_dlpf_hz: int = Field(default=44, gt=0)
    emg_gain: float = Field(default=1.0, gt=0)
    # What the EMG module delivers to the ADC input: ``raw`` (raw, bipolar biased at Vref/2), ``rectified`` or
    # ``envelope``. The Muscle Sensor V3 clone probably gives an envelope, but it is NOT confirmed with the board
    # (issue #13): the default is the assumption, not a fact. It conditions quality, conversion to mV and MVC calibration.
    emg_signal_kind: SignalKind = SignalKind.ENVELOPE
    # Calibration defaults: they come from contracts/catalog/calibration-defaults.json (a single source, the same as the example
    # mission). PENDING measurement (#106): ~650 mm is the C3->S1 cord of the protocol, a placeholder.
    stretch_rest_length_mm: float = Field(default=float(_CALIBRATION_DEFAULTS["stretch"]["rest_length_mm"]), gt=0)
    # Rest point (ohm, mm) to which the default curve is referred: used to scale it to the measured R0 and L0; it
    # is not the calibration R0 (that comes from the counts) nor ``stretch_rest_length_mm``. If you change the curve, change this pair too.
    stretch_curve_rest_length_mm: float = Field(default=float(_CALIBRATION_DEFAULTS["stretch"]["rest_length_mm"]), gt=0)
    stretch_curve_rest_resistance_ohm: float = Field(default=float(_CALIBRATION_DEFAULTS["stretch"]["rest_resistance_ohm"]), gt=0)
    # Cord curve as absolute points (R ohm, L mm) relative to that reference rest, increasing on both axes. In .env as a
    # JSON list of pairs: [[5400, 585], [6012, 650], [6790, 731]]. PENDING measurement: unmeasured default value.
    stretch_curve: tuple[tuple[float, float], ...] = DEFAULT_STRETCH_CURVE
    # Window (ms) of the sliding RMS of the emg_mvc calibration; 250 ms as in the contract example.
    emg_mvc_window_ms: int = Field(default=int(_CALIBRATION_DEFAULTS["emg_mvc"]["window_ms"]), gt=0)

    @field_validator("stretch_curve")
    @classmethod
    def _stretch_curve(cls, value: tuple[tuple[float, float], ...]) -> tuple[tuple[float, float], ...]:
        positive = all(r > 0 and length > 0 for r, length in value)
        rising = all(b[0] > a[0] and b[1] > a[1] for a, b in zip(value, value[1:], strict=False))
        if len(value) < 2 or not positive or not rising:
            raise ValueError("stretch_curve must have at least two (ohm, mm) points, all positive and rising strictly in both axes")
        return value

    @field_validator("astronaut_id")
    @classmethod
    def _astronaut_id(cls, value: str) -> str:
        return _matches("astronaut_id", value, topics.ASTRONAUT_ID)

    @field_validator("device_id")
    @classmethod
    def _device_id(cls, value: str) -> str:
        return _matches("device_id", value, topics.DEVICE_ID)

    @field_validator("emg_stream_id", "imu_stream_id", "stretch_stream_id")
    @classmethod
    def _stream_id(cls, value: str) -> str:
        return _matches("stream_id", value, topics.STREAM_ID)

    @model_validator(mode="after")
    def _reconnect_cap(self) -> Self:
        if self.mqtt_reconnect_max_s < self.mqtt_reconnect_initial_s:
            raise ValueError("mqtt_reconnect_max_s must be >= mqtt_reconnect_initial_s")
        if self.serial_reconnect_max_s < self.serial_reconnect_initial_s:
            raise ValueError("serial_reconnect_max_s must be >= serial_reconnect_initial_s")
        return self
