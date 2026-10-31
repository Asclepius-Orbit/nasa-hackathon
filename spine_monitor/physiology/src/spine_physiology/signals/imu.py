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

Notas de implementación:
- `dlpf_hz` no se aplica (el filtro paso bajo del MPU-6050 solo es metadato del dispositivo).
- Si la matriz de montaje fuera zurda (det = −1, físicamente imposible en un sensor real) el giróscopo
  se transforma igualmente con ella, sin corrección de pseudovector.
"""

from collections.abc import Sequence

import numpy as np
from spine_contracts import AnatomicalPlane
from spine_contracts.models.domain.calibration_schema import BodyAxis

from spine_physiology.config import ImuSignalConfig
from spine_physiology.signals import Rep
from spine_physiology.state import LatentState

# Nombres de canal del ImuChunk, en el orden del contrato.
IMU_CHANNELS = ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z")

# Gravedad estándar (m/s²), definición física: convierte la aceleración lineal a g.
STANDARD_GRAVITY_MS2 = 9.80665

# Marco corporal: x = anterior, y = izquierda, z = craneal. Vector de cada eje corporal en ese marco.
_BODY_AXIS_VECTORS: dict[BodyAxis, tuple[float, float, float]] = {
    BodyAxis.ANTERIOR: (1.0, 0.0, 0.0),
    BodyAxis.POSTERIOR: (-1.0, 0.0, 0.0),
    BodyAxis.LEFT: (0.0, 1.0, 0.0),
    BodyAxis.RIGHT: (0.0, -1.0, 0.0),
    BodyAxis.CRANIAL: (0.0, 0.0, 1.0),
    BodyAxis.CAUDAL: (0.0, 0.0, -1.0),
}

# Eje de rotación (marco corporal) de cada plano. Un ángulo positivo inclina el eje craneal del tronco
# hacia anterior (sagital) o hacia la derecha (frontal); en el plano transversal gira sobre sí mismo.
_PLANE_AXES: dict[AnatomicalPlane, tuple[float, float, float]] = {
    AnatomicalPlane.SAGITTAL: (0.0, 1.0, 0.0),
    AnatomicalPlane.FRONTAL: (1.0, 0.0, 0.0),
    AnatomicalPlane.TRANSVERSE: (0.0, 0.0, 1.0),
}


def _min_jerk_tau(tau: np.ndarray) -> np.ndarray:
    """Polinomio minimum-jerk evaluado en τ (recortado a [0, 1])."""
    t = np.clip(tau, 0.0, 1.0)
    return 10.0 * t**3 - 15.0 * t**4 + 6.0 * t**5


def min_jerk(n: int) -> np.ndarray:
    """Perfil minimum-jerk de 0 a 1 en `n` muestras (10τ³ − 15τ⁴ + 6τ⁵)."""
    return _min_jerk_tau(np.linspace(0.0, 1.0, max(n, 0)))


def effective_rom_deg(protocol_rom_deg: float, state: LatentState, params: ImuSignalConfig) -> float:
    """rom × (1 − rom_loss_gain × rom_motor_control) × (1 − pain_rom_gain × pain_stiffness), >= 0."""
    motor = max(1.0 - params.rom_loss_gain * state.rom_motor_control, 0.0)
    pain = max(1.0 - params.pain_rom_gain * state.pain_stiffness, 0.0)
    return max(protocol_rom_deg * motor * pain, 0.0)


def _bend(tau: np.ndarray) -> np.ndarray:
    """Flexión ida y vuelta para τ en [0, 1]: sube minimum-jerk en la primera mitad y baja en la segunda."""
    return np.where(tau < 0.5, _min_jerk_tau(2.0 * tau), _min_jerk_tau(2.0 - 2.0 * tau))


def angle_profile(reps: Sequence[Rep], *, rom_deg: float | Sequence[float], n_samples: int, rate_hz: float) -> np.ndarray:
    """Ángulo de flexión (deg) por muestra; 0 fuera de las reps. `rom_deg` escalar o uno por rep."""
    if isinstance(rom_deg, int | float):
        roms = [float(rom_deg)] * len(reps)
    else:
        roms = [float(r) for r in rom_deg]
        if len(roms) != len(reps):
            raise ValueError("rom_deg must be a scalar or have one value per rep")
    angle = np.zeros(n_samples, dtype=np.float64)
    for rep, rom in zip(reps, roms, strict=True):
        i0 = max(int(np.ceil(rep.start_s * rate_hz - 1e-9)), 0)
        i1 = min(int(np.ceil(rep.end_s * rate_hz - 1e-9)), n_samples)
        if i1 <= i0:
            continue
        tau = (np.arange(i0, i1, dtype=np.float64) / rate_hz - rep.start_s) / (rep.end_s - rep.start_s)
        angle[i0:i1] = rom * _bend(tau)
    return angle


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
    duration_s = n_samples / rate_hz
    count = int(rng.poisson(bends_per_hour * duration_s / 3600.0))
    starts = np.sort(rng.uniform(0.0, max(duration_s - bend_duration_s, 0.0), size=count))
    angle = np.zeros(n_samples, dtype=np.float64)
    for start in starts:
        i0 = int(np.ceil(start * rate_hz - 1e-9))
        i1 = min(int(np.ceil((start + bend_duration_s) * rate_hz - 1e-9)), n_samples)
        if i1 <= i0:
            continue
        tau = (np.arange(i0, i1, dtype=np.float64) / rate_hz - start) / bend_duration_s
        # Flexiones solapadas: manda la mayor (el tronco no se dobla "el doble").
        angle[i0:i1] = np.maximum(angle[i0:i1], bend_rom_deg * _bend(tau))
    return angle


def _mounting_matrix(params: ImuSignalConfig) -> np.ndarray:
    """Matriz 3x3 cuerpo -> sensor: cada fila es el eje del sensor expresado en el marco corporal."""
    m = params.mounting
    return np.array([_BODY_AXIS_VECTORS[m.sensor_x], _BODY_AXIS_VECTORS[m.sensor_y], _BODY_AXIS_VECTORS[m.sensor_z]])


def _rotate(v: np.ndarray, axis: np.ndarray, phi: np.ndarray) -> np.ndarray:
    """Rodrigues: rota `v` (3,) un ángulo `phi` (n,) rad alrededor de `axis` unitario; devuelve (n, 3)."""
    cos = np.cos(phi)[:, None]
    sin = np.sin(phi)[:, None]
    return v * cos + np.cross(axis, v) * sin + axis * np.dot(axis, v) * (1.0 - cos)


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
    """Los 6 canales de `IMU_CHANNELS` (float64, len(angle_deg) muestras cada uno).

    Marco corporal: x anterior, y izquierda, z craneal. El tronco (y el sensor, solidario a él) rota
    `angle_deg` alrededor del eje del plano; la fuerza específica en el marco del tronco es la gravedad
    (g hacia arriba en el mundo) rotada al revés más la aceleración lineal del sensor a `lever_arm_m` del
    eje de flexión (tangencial + centrípeta). Todo se pasa al marco del sensor con la matriz de montaje.
    El rng consume siempre el mismo número de sorteos (ruido accel, ruido gyro, temblor).
    """
    angle = np.asarray(angle_deg, dtype=np.float64)
    n = angle.shape[0]
    dt = 1.0 / rate_hz
    mount = _mounting_matrix(params)
    axis = np.array(_PLANE_AXES[plane])
    up = np.array([0.0, 0.0, 1.0])

    theta = np.deg2rad(angle)
    omega_deg = np.gradient(angle, dt) if n > 1 else np.zeros(n)
    omega = np.deg2rad(omega_deg)
    alpha = np.gradient(omega, dt) if n > 1 else np.zeros(n)

    # Gravedad: el "arriba" del mundo visto desde el tronco rotado (rotación inversa).
    specific_force = gravity_g * _rotate(up, axis, -theta)
    # Aceleración lineal del sensor (r = lever_arm_m sobre el eje craneal): α×r + ω×(ω×r).
    r = params.lever_arm_m * up
    tangential = alpha[:, None] * np.cross(axis, r)
    centripetal = (omega**2)[:, None] * (axis * np.dot(axis, r) - r)
    specific_force = specific_force + (tangential + centripetal) / STANDARD_GRAVITY_MS2

    accel = specific_force @ mount.T
    gyro = (omega_deg[:, None] * axis) @ mount.T

    accel_noise = rng.normal(0.0, params.accel_noise_g, size=(n, 3))
    gyro_noise = rng.normal(0.0, params.gyro_noise_dps, size=(n, 3))
    tremor = rng.normal(0.0, tremor_dps, size=(n, 3))

    ab, gb = params.accel_bias_g, params.gyro_bias_dps
    accel = accel + accel_noise + np.array([ab.x, ab.y, ab.z])
    gyro = gyro + gyro_noise + tremor + np.array([gb.x, gb.y, gb.z])
    accel = np.clip(accel, -float(params.accel_range_g), float(params.accel_range_g))
    gyro = np.clip(gyro, -float(params.gyro_range_dps), float(params.gyro_range_dps))

    return {
        "accel_x": accel[:, 0], "accel_y": accel[:, 1], "accel_z": accel[:, 2],
        "gyro_x": gyro[:, 0], "gyro_y": gyro[:, 1], "gyro_z": gyro[:, 2],
    }  # fmt: skip
