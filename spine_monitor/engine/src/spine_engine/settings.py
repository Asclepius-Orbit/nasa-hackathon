"""Configuración del motor: ventanas, umbrales y arranque. Variables SPINE_ENGINE_* o `.env`."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from spine_contracts import AssessmentDomain

DEFAULT_DOMAIN_WEIGHT = 1.0


class EngineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPINE_ENGINE_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8001

    # z robusto: suelo de la escala, como fracción de la mediana, cuando MAD y desviación son ~0.
    scale_floor: float = 0.05

    # CUSUM sobre la serie de z (k: tolerancia, h: umbral, clip: tope por punto).
    cusum_k: float = 0.5
    cusum_h: float = 4.0
    cusum_clip: float = 3.0
    cusum_min_points: int = 3

    # Kalman de nivel local sobre la misma serie.
    kalman_process_var: float = 0.05
    kalman_measurement_var: float = 1.0
    kalman_threshold: float = 1.0
    kalman_persist: int = 3
    kalman_min_points: int = 3

    # Modelo de riesgo genérico (sin rangos clínicos): peso de cada dominio, puntos de la serie a
    # partir de los que una desviación cuenta entera y escala de la saturación 1 - exp(-suma / escala).
    # Los dominios que falten en los pesos se completan con DEFAULT_DOMAIN_WEIGHT. n_points es el largo
    # de la serie, no el tramo final desviado: con risk_persistence_points <= al mínimo de puntos del
    # detector, el factor de persistencia es siempre 1.
    risk_domain_weights: dict[AssessmentDomain, float] = Field(default_factory=lambda: dict.fromkeys(AssessmentDomain, DEFAULT_DOMAIN_WEIGHT))
    risk_persistence_points: int = 3
    risk_saturation: float = 3.0

    @field_validator("risk_domain_weights")
    @classmethod
    def _complete_domain_weights(cls, weights: dict[AssessmentDomain, float]) -> dict[AssessmentDomain, float]:
        """Los dominios que faltan pesan como por defecto, en vez de quedar apagados."""
        return {**dict.fromkeys(AssessmentDomain, DEFAULT_DOMAIN_WEIGHT), **weights}
