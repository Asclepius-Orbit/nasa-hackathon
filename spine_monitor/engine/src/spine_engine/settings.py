"""Configuración del motor: ventanas, umbrales y arranque. Variables SPINE_ENGINE_* o `.env`."""

from pydantic_settings import BaseSettings, SettingsConfigDict


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
