"""Arranque del gateway falso. Variables SPINE_SIM_* o `.env`.

Dueño: D2. Los parámetros de simulación (fisiológicos y técnicos) están en el YAML de misión, no aquí:
aquí solo va dónde está ese YAML, a qué broker conectarse y a qué ritmo avanza el día de misión.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPINE_SIM_", env_file=".env", extra="ignore")

    # YAML de misión (ver configs/example-mission.yaml).
    config: Path
    # Semilla; None = la del YAML.
    seed: int | None = None

    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_client_id: str = "spine-sim-gateway"

    # El día de misión avanza con el reloj de pared × speed (1 = tiempo real). Obligatorio.
    speed: float
    # Índice de día de misión en el arranque del gateway. Obligatorio.
    start_day: int
