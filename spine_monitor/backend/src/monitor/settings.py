"""App settings: broker, engine, database and thresholds. SPINE_APP_* variables or `.env`."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPINE_APP_", env_file=".env", extra="ignore")

    # MQTT broker (on-board Mosquitto).
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_client_id: str = "spine-app"
    mqtt_reconnect_min_s: float = 1.0
    mqtt_reconnect_max_s: float = 30.0

    # Engine (stateless local API).
    engine_url: str = "http://127.0.0.1:8001"
    engine_timeout_s: float = 60.0

    # SQLite: the app owns all the state.
    db_path: Path = Path("spine_monitor.db")

    # A command with no ack within this time becomes `timeout`.
    ack_timeout_s: float = 10.0

    # /assess: days of history (feature sets, self-reports, daily context) sent to the engine.
    assess_window_days: int = 14

    # Chunks whose session_id is not an app session (e.g. the gateway's background monitoring).
    store_unknown_session_chunks: bool = True

    # The Oswestry is due when the last one is older than this many days.
    weekly_odi_interval_days: int = 7
