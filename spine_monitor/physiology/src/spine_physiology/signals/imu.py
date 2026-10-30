"""IMU de 6 ejes (MPU-6050): accel en g, gyro en deg/s, en ejes del SENSOR.

Dueño: B3. Modelo:
- Ángulo de flexión del tronco por muestra: cada rep va de 0 a ROM y vuelve, con perfiles
  minimum-jerk (mitad de la rep a la ida, mitad a la vuelta). Fondo: flexiones espontáneas Poisson.
- gyro = derivada del ángulo sobre el eje del plano de movimiento + temblor (`tremor_dps`) + ruido + bias.
- accel = gravedad rotada por el ángulo (magnitud `gravity_g`: 0 en microgravedad) + aceleración
  tangencial/centrípeta del brazo `lever_arm_m` + ruido + bias. En reposo en Tierra |accel| ≈ 1 g; en
  micro ≈ 0 g. Ejes corporales -> sensor según `params.mounting` (el acelerómetro mide fuerza específica:
  en reposo y de pie apunta a craneal). Recorte a ±accel_range_g / ±gyro_range_dps.
Requisitos verificables: ROM medido del ángulo integrado ≈ rom_deg; media de |accel| en reposo ≈
gravity_g; misma rng ⇒ mismos arrays.
"""

from collections.abc import Sequence

import numpy as np
from spine_contracts import AnatomicalPlane

from spine_physiology.config import ImuSignalConfig
from spine_physiology.signals import Rep
from spine_physiology.state import LatentState

# Nombres de canal del ImuChunk, en el orden del contrato.
IMU_CHANNELS = ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z")


def min_jerk(n: int) -> np.ndarray:
    """Perfil minimum-jerk de 0 a 1 en `n` muestras (10τ³ − 15τ⁴ + 6τ⁵)."""
    raise NotImplementedError


def effective_rom_deg(protocol_rom_deg: float, state: LatentState, params: ImuSignalConfig) -> float:
    """rom × (1 − rom_loss_gain × rom_motor_control) × (1 − pain_rom_gain × pain_stiffness), >= 0."""
    raise NotImplementedError


def angle_profile(reps: Sequence[Rep], *, rom_deg: float | Sequence[float], n_samples: int, rate_hz: float) -> np.ndarray:
    """Ángulo de flexión (deg) por muestra; 0 fuera de las reps. `rom_deg` escalar o uno por rep."""
    raise NotImplementedError


def background_angle_profile(
    rng: np.random.Generator,
    *,
    n_samples: int,
    rate_hz: float,
    bends_per_hour: float,
    bend_rom_deg: float,
    bend_duration_s: float,
) -> np.ndarray:
    """Ángulo (deg) del fondo: reposo con flexiones espontáneas (Poisson) minimum-jerk ida y vuelta."""
    raise NotImplementedError


def synth_imu(
    rng: np.random.Generator,
    params: ImuSignalConfig,
    *,
    angle_deg: np.ndarray,
    plane: AnatomicalPlane,
    gravity_g: float,
    rate_hz: float,
    tremor_dps: float,
) -> dict[str, np.ndarray]:
    """Los 6 canales de `IMU_CHANNELS` (float64, len(angle_deg) muestras cada uno)."""
    raise NotImplementedError
