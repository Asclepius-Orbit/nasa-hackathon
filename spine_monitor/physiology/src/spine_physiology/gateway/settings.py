"""Fake gateway startup. SPINE_SIM_* variables or `.env`.

Owner: D2. The simulation parameters (physiological and technical) live in the mission YAML, not here:
only where that YAML is, which broker to connect to and how fast the mission day advances go here.
"""

from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPINE_SIM_", env_file=".env", extra="ignore")

    # Mission YAML (see configs/example-mission.yaml).
    config: Path
    # Seed; None = the YAML's.
    seed: int | None = Field(default=None, ge=0)

    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    # MQTT client id prefix: each device opens its own connection `<prefix>-<device_id>` (with its
    # own Last Will, since MQTT allows only one per connection).
    mqtt_client_id: str = "spine-sim-gateway"
    # Broker credentials (app/mosquitto/setup.sh leaves them in app/.env). `{device_id}` in the username is replaced by
    # each device's id: the simulator logs in as `sim-01`, `sim-02`... (each with its own ACL) with the same
    # password. None = no credentials (anonymous).
    mqtt_username: str | None = None
    mqtt_password: SecretStr | None = None
    # TLS to the broker (LAN listener 8883); without a CA file the system CAs verify the certificate.
    mqtt_tls: bool = False
    mqtt_tls_ca_file: Path | None = None
    # Reconnection with exponential backoff if the broker rejects or drops the connection: initial wait (s),
    # factor per failed attempt and cap (s). Goes back to the initial one after connecting.
    mqtt_reconnect_initial_s: float = Field(default=1.0, gt=0)
    mqtt_reconnect_factor: float = Field(default=2.0, ge=1)
    mqtt_reconnect_max_s: float = Field(default=30.0, gt=0)

    # The mission day advances with the wall clock × speed (1 = real time, 0 = fixed day). Required.
    speed: float = Field(ge=0)
    # Mission day index when the gateway starts (0 = first simulated day). Required.
    start_day: int = Field(ge=0)

    @model_validator(mode="after")
    def _reconnect_cap(self) -> Self:
        if self.mqtt_reconnect_max_s < self.mqtt_reconnect_initial_s:
            raise ValueError("mqtt_reconnect_max_s must be >= mqtt_reconnect_initial_s")
        return self
