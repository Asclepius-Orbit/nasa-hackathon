"""App settings: broker, engine, database and thresholds. SPINE_APP_* variables or `.env`."""

from pathlib import Path

from pydantic import Field
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

    # Read-only API: data older than this many hours is reported as `stale` in the status.
    stale_assessment_hours: float = 24.0
    stale_session_hours: float = 48.0
    stale_self_report_hours: float = 36.0
    stale_device_hours: float = 0.25

    # UI views: the pre-flight baseline plan (sessions required, days relative to launch, default protocol of the
    # planned ones; the pre-flight examples and the protocol catalog use the lat pulldown) and the history span.
    baseline_required: int = Field(default=3, ge=1)
    baseline_plan_offsets_days: list[int] = [-30, -23, -16]
    baseline_protocol_id: str = "lat_pulldown_standing_v1"
    # At most 366 days: the longest window the history endpoint accepts (`api.MAX_HISTORY_DAYS`; settings cannot import api).
    history_window_days: int = Field(default=30, ge=1, le=366)

    # Live repetition counter (GET /ui/sessions/{id}/reps). Seconds with no imu_l3 chunk until the signal counts as lost,
    # between comments on an idle stream (the Electron reader gives up after 45 s of silence) and between checks of both.
    live_no_signal_s: float = Field(default=2.0, gt=0)
    live_heartbeat_s: float = Field(default=15.0, gt=0, le=15)
    live_tick_s: float = Field(default=0.5, gt=0, le=0.5)
