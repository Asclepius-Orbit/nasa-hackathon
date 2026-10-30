"""Arranque del gateway falso. Variables SPINE_SIM_* o `.env`.

Dueño: D2. Los parámetros de simulación (fisiológicos y técnicos) están en el YAML de misión, no aquí:
aquí solo va dónde está ese YAML, a qué broker conectarse y a qué ritmo avanza el día de misión.
"""

from pathlib import Path

from pydantic import Field
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

    # El día de misión avanza con el reloj de pared × speed (1 = tiempo real, 0 = día fijo). Obligatorio.
    speed: float = Field(ge=0)
    # Índice de día de misión en el arranque del gateway (0 = primer día simulado). Obligatorio.
    start_day: int = Field(ge=0)
