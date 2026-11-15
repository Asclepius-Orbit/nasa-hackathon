"""Engine configuration: windows, thresholds and startup. SPINE_ENGINE_* variables or `.env`."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from spine_contracts import AssessmentDomain

DEFAULT_DOMAIN_WEIGHT = 1.0
# PROVISIONAL until #46: fraction of the repetition peak below which the cord is considered recovered.
# Single source: StretchFamily uses it as the constructor default.
DEFAULT_STRETCH_RECOVERY_FRACTION = 0.1


class EngineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPINE_ENGINE_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8001

    # Provisional protocol values until #46.
    stretch_recovery_wait_s: float = Field(default=120.0, ge=0, allow_inf_nan=False)
    stretch_recovery_fraction: float = Field(default=DEFAULT_STRETCH_RECOVERY_FRACTION, ge=0, le=1, allow_inf_nan=False)

    # robust z: scale floor, as a fraction of the median, when MAD and deviation are ~0.
    scale_floor: float = 0.05

    # CUSUM over the z series (k: tolerance, h: threshold, clip: per-point cap).
    cusum_k: float = 0.5
    cusum_h: float = 4.0
    cusum_clip: float = 3.0
    cusum_min_points: int = 3

    # Local-level Kalman over the same series.
    kalman_process_var: float = 0.05
    kalman_measurement_var: float = 1.0
    kalman_threshold: float = 1.0
    kalman_persist: int = 3
    kalman_min_points: int = 3

    # Generic risk model (no clinical ranges): weight of each domain, series points from
    # which a deviation counts in full and saturation scale 1 - exp(-sum / scale).
    # Domains missing from the weights are filled in with DEFAULT_DOMAIN_WEIGHT. n_points is the length
    # of the series, not the final deviated stretch: with risk_persistence_points <= the detector's
    # minimum number of points, the persistence factor is always 1.
    risk_domain_weights: dict[AssessmentDomain, float] = Field(default_factory=lambda: dict.fromkeys(AssessmentDomain, DEFAULT_DOMAIN_WEIGHT))
    risk_persistence_points: int = 3
    risk_saturation: float = 3.0
    triage_policy_file: Path | None = None

    @field_validator("risk_domain_weights")
    @classmethod
    def _complete_domain_weights(cls, weights: dict[AssessmentDomain, float]) -> dict[AssessmentDomain, float]:
        """Domains that are missing weigh as by default, instead of being left switched off."""
        return {**dict.fromkeys(AssessmentDomain, DEFAULT_DOMAIN_WEIGHT), **weights}
