"""Arranque del gateway falso. Variables SPINE_SIM_* o `.env`.

Dueño: D2. Los parámetros de simulación (fisiológicos y técnicos) están en el YAML de misión, no aquí:
aquí solo va dónde está ese YAML, a qué broker conectarse y a qué ritmo avanza el día de misión.
"""

from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPINE_SIM_", env_file=".env", extra="ignore")

    # YAML de misión (ver configs/example-mission.yaml).
    config: Path
    # Semilla; None = la del YAML.
    seed: int | None = Field(default=None, ge=0)

    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    # Prefijo del identificador MQTT: cada dispositivo abre su conexión `<prefijo>-<device_id>` (con su
    # propio Last Will, que MQTT solo admite uno por conexión).
    mqtt_client_id: str = "spine-sim-gateway"
    # Reconexión con backoff exponencial si el broker rechaza la conexión o la corta: espera inicial (s),
    # factor por intento fallido y tope (s). Vuelve a la inicial tras conectar.
    mqtt_reconnect_initial_s: float = Field(default=1.0, gt=0)
    mqtt_reconnect_factor: float = Field(default=2.0, ge=1)
    mqtt_reconnect_max_s: float = Field(default=30.0, gt=0)

    # El día de misión avanza con el reloj de pared × speed (1 = tiempo real, 0 = día fijo). Obligatorio.
    speed: float = Field(ge=0)
    # Índice de día de misión en el arranque del gateway (0 = primer día simulado). Obligatorio.
    start_day: int = Field(ge=0)

    @model_validator(mode="after")
    def _reconnect_cap(self) -> Self:
        if self.mqtt_reconnect_max_s < self.mqtt_reconnect_initial_s:
            raise ValueError("mqtt_reconnect_max_s must be >= mqtt_reconnect_initial_s")
        return self
