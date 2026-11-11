"""Arranque del gateway real (Arduino por USB serie -> MQTT v1). Variables SPINE_GATEWAY_* o `.env`.

Aquí va lo que el firmware no puede decir por sí mismo: dónde está el puerto, a qué broker conectarse, a qué
astronauta/dispositivo se atribuyen los datos y el cableado/calibración de fábrica del hardware. La versión de
firmware no se configura: llega en el hello del dispositivo.
"""

import re
from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from spine_contracts import topics
from spine_contracts.models.common_schema import Muscle, Side


def _matches(kind: str, value: str, pattern: re.Pattern[str]) -> str:
    # Mismas expresiones que usa `topics` para construir topics: un id válido aquí nunca rompe un topic.
    if not pattern.fullmatch(value):
        raise ValueError(f"invalid {kind}: {value!r}")
    return value


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPINE_GATEWAY_", env_file=".env", extra="ignore")

    # Puerto serie tal como lo entiende pyserial `serial_for_url`: COM3, /dev/ttyACM0 o socket://host:puerto.
    serial_url: str = Field(default="COM3", min_length=1)
    serial_baud: int = Field(default=250000, gt=0)

    astronaut_id: str = "AST-01"
    device_id: str = "wb-01"

    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = Field(default=1883, ge=1, le=65535)
    mqtt_client_id: str = "spine-gateway"
    # Reconexión con backoff exponencial si el broker rechaza la conexión o la corta: espera inicial (s),
    # factor por intento fallido y tope (s). Vuelve a la inicial tras conectar.
    mqtt_reconnect_initial_s: float = Field(default=1.0, gt=0)
    mqtt_reconnect_factor: float = Field(default=2.0, ge=1)
    mqtt_reconnect_max_s: float = Field(default=30.0, gt=0)

    # Periodos (s) de los mensajes de status y de la sincronización de reloj con el dispositivo.
    status_interval_s: float = Field(default=30.0, gt=0)
    clock_sync_interval_s: float = Field(default=60.0, gt=0)

    emg_stream_id: str = "emg_lat_left"
    imu_stream_id: str = "imu_l3"
    stretch_stream_id: str = "stretch_lumbar"

    # Colocación del electrodo EMG (misma que EmgMvcCalibration y el YAML de misión de ejemplo).
    emg_muscle: Muscle = Muscle.LATISSIMUS_DORSI
    emg_side: Side = Side.LEFT

    # Hardware del wearable.
    adc_bits: int = Field(default=10, gt=0)
    vref_v: float = Field(default=5.0, gt=0)
    emg_input_pin: str = Field(default="A0", min_length=1)
    stretch_input_pin: str = Field(default="A1", min_length=1)
    stretch_series_resistor_ohm: float = Field(default=10000.0, gt=0)
    imu_accel_range_g: int = Field(default=4, gt=0)
    imu_gyro_range_dps: int = Field(default=500, gt=0)
    imu_dlpf_hz: int = Field(default=44, gt=0)
    emg_gain: float = Field(default=1.0, gt=0)
    stretch_rest_length_mm: float = Field(default=100.0, gt=0)

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
        return self
