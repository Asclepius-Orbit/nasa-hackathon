"""App settings: broker, engine, database, retries and thresholds. SPINE_APP_* variables or `.env`."""

from pathlib import Path
from typing import Self

from pydantic import AwareDatetime, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from monitor.ground.link import GroundWindows


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
    # A debounced reassessment (e.g. after a new baseline) runs this many seconds after the last request for the astronaut;
    # an immediate /assess in between replaces it. 0 still defers it to the loop, without waiting.
    reassess_debounce_s: float = Field(default=5.0, ge=0)
    # An astronaut whose latest assessment is missing or older than `stale_assessment_hours` is reassessed by a loop that
    # looks every this many seconds, so it is also the precision of that threshold.
    reassess_tick_s: float = Field(default=3600.0, gt=0)

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

    # Ground link: up the first `up_min` of every `period_min` minutes from the anchor. Without an anchor no link is configured:
    # the UI shows null and nothing is sent. Retries double per entry, between these seconds; the tick is how often the sender
    # looks for an opened window or an elapsed backoff, so it is also the precision of both.
    ground_window_anchor: AwareDatetime | None = None
    ground_window_period_min: int = Field(default=90, gt=0)
    ground_window_up_min: int = Field(default=10, gt=0)
    ground_retry_min_s: float = Field(default=1.0, gt=0)
    ground_retry_max_s: float = Field(default=300.0, gt=0)
    ground_tick_s: float = Field(default=1.0, gt=0)
    # Longest wait for the ground to answer one send; past it the send counts as a failure (code `timeout`) and backs off.
    ground_send_timeout_s: float = Field(default=30.0, gt=0)

    # Analysis retries: a session whose /features failed because the engine was unreachable or answered 5xx is processed
    # again after a wait that doubles from min to max, at most `max_attempts` times in all. The tick is how often the
    # retrier looks for due sessions, so it is also the precision of the wait.
    processing_retry_min_s: float = Field(default=30.0, gt=0)
    processing_retry_max_s: float = Field(default=900.0, gt=0)
    processing_max_attempts: int = Field(default=5, ge=1)
    processing_retry_tick_s: float = Field(default=5.0, gt=0)

    @model_validator(mode="after")
    def _ground_ranges(self) -> Self:
        if self.ground_window_up_min >= self.ground_window_period_min:
            raise ValueError("ground_window_up_min must be below ground_window_period_min")
        if self.ground_retry_max_s < self.ground_retry_min_s:
            raise ValueError("ground_retry_max_s must not be below ground_retry_min_s")
        if self.processing_retry_max_s < self.processing_retry_min_s:
            raise ValueError("processing_retry_max_s must not be below processing_retry_min_s")
        return self

    @property
    def ground_windows(self) -> GroundWindows | None:
        if self.ground_window_anchor is None:
            return None
        return GroundWindows(self.ground_window_anchor, self.ground_window_period_min, self.ground_window_up_min)
