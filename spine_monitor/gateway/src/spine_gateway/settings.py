"""Arranque del gateway real (Arduino por USB serie -> MQTT v1). Variables SPINE_GATEWAY_* o `.env`.

Aquí va lo que el firmware no puede decir por sí mismo: dónde está el puerto, a qué broker conectarse, a qué
astronauta/dispositivo se atribuyen los datos y el cableado/calibración de fábrica del hardware. La versión de
firmware no se configura: llega en el hello del dispositivo.
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
"""Curva por defecto del cordón, de ``contracts/catalog/calibration-defaults.json`` (la misma que la misión de ejemplo).

Puntos absolutos (R ohm, L mm) respecto al reposo de referencia (``rest_resistance_ohm``, ``rest_length_mm`` del mismo
fichero). La calibración los escala al reposo medido: R0 · R / R_ref y L0 · L / L_ref. Valores sin medir (PENDIENTE
medición Aguado); se cambian con SPINE_GATEWAY_STRETCH_CURVE al medir el cordón. El cordón no es lineal y tiene histéresis.
"""


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
    # Reconexión del puerto serie con backoff exponencial si no abre o se desenchufa: espera inicial (s), factor y tope (s).
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
    # Reconexión con backoff exponencial si el broker rechaza la conexión o la corta: espera inicial (s),
    # factor por intento fallido y tope (s). Vuelve a la inicial tras conectar.
    mqtt_reconnect_initial_s: float = Field(default=1.0, gt=0)
    mqtt_reconnect_factor: float = Field(default=2.0, ge=1)
    mqtt_reconnect_max_s: float = Field(default=30.0, gt=0)

    # Periodos (s) de los mensajes de status y de la sincronización de reloj con el dispositivo.
    status_interval_s: float = Field(default=30.0, gt=0)
    clock_sync_interval_s: float = Field(default=60.0, gt=0)
    # Error estimado (ms) del ancla de reloj: hora de llegada del tick al gateway (latencia USB + lectura de línea).
    clock_uncertainty_ms: float = Field(default=20.0, gt=0)
    # Un stream sin lotes durante este tiempo (s) pasa a `stalled` y el estado a `degraded`.
    stall_timeout_s: float = Field(default=2.0, gt=0)
    # Duración (s) de un `calibrate` sin `duration_s`.
    calibration_default_duration_s: float = Field(default=5.0, gt=0)

    emg_stream_id: str = "emg_lat_left"
    imu_stream_id: str = "imu_t9"
    stretch_stream_id: str = "stretch_spine"

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
    # Qué entrega el módulo EMG a la entrada del ADC: ``raw`` (cruda, bipolar polarizada a Vref/2), ``rectified`` o
    # ``envelope``. El clon del Muscle Sensor V3 probablemente da envolvente, pero NO está confirmado con la placa
    # (issue #13): el default es la suposición, no un hecho. Condiciona calidad, conversión a mV y calibración MVC.
    emg_signal_kind: SignalKind = SignalKind.ENVELOPE
    # Defaults de calibración: salen de contracts/catalog/calibration-defaults.json (una sola fuente, la misma que la misión
    # de ejemplo). PENDIENTE medición Aguado (#106): ~650 mm es el cordón C3->S1 del protocolo, un marcador de posición.
    stretch_rest_length_mm: float = Field(default=float(_CALIBRATION_DEFAULTS["stretch"]["rest_length_mm"]), gt=0)
    # Punto de reposo (ohm, mm) al que está referida la curva por defecto: sirve para escalarla a la R0 y la L0 medidas; no
    # es la R0 de la calibración (sale de las cuentas) ni ``stretch_rest_length_mm``. Si cambias la curva, cambia también este par.
    stretch_curve_rest_length_mm: float = Field(default=float(_CALIBRATION_DEFAULTS["stretch"]["rest_length_mm"]), gt=0)
    stretch_curve_rest_resistance_ohm: float = Field(default=float(_CALIBRATION_DEFAULTS["stretch"]["rest_resistance_ohm"]), gt=0)
    # Curva del cordón como puntos absolutos (R ohm, L mm) respecto a ese reposo de referencia, crecientes en ambos ejes. En .env como
    # lista JSON de pares: [[5400, 585], [6012, 650], [6790, 731]]. PENDIENTE medición Aguado: valor por defecto sin medir.
    stretch_curve: tuple[tuple[float, float], ...] = DEFAULT_STRETCH_CURVE
    # Ventana (ms) del RMS deslizante de la calibración emg_mvc; 250 ms como el ejemplo del contrato.
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
