"""6-axis IMU (MPU-6050): accel in g, gyro in deg/s, in SENSOR axes.

Owner: B3. Model:
- Trunk flexion angle per sample: each rep goes from 0 to ROM and back, with minimum-jerk
    profiles (half of the rep out, half back). Background: spontaneous Poisson flexions.
- gyro = derivative of the angle about the movement-plane axis + tremor (`tremor_dps`) + noise + bias.
- accel = gravity rotated by the angle (magnitude `gravity_g`: 0 in microgravity) + tangential/centripetal
    acceleration of the `lever_arm_m` arm + noise + bias. At rest on Earth |accel| ≈ 1 g; in
    micro ≈ 0 g. Body axes -> sensor according to `params.mounting` (the accelerometer measures specific force:
    at rest and standing it points cranially). Clipped to ±accel_range_g / ±gyro_range_dps.
Verifiable requirements: ROM measured from the integrated angle ≈ rom_deg; mean |accel| at rest ≈
gravity_g; same rng ⇒ same arrays.

Implementation notes:
- Causal 1st-order low-pass at `dlpf_hz` over movement and tremor (no effect if >= Nyquist); the sensor
    noise (datasheet spectral density x sqrt(min(dlpf, Nyquist))) and the bias are added afterwards.
- Bias = configured offset + Ornstein-Uhlenbeck drift (child rng via `rng.spawn`, does not shift the noise),
    clipped to the datasheet maxima. Output clipped to the range and quantised to int16 counts (optional).
- The session is synthesised in one go; if it were generated in chunks, the filter and drift state would not be carried over.
- If the mounting matrix were left-handed (det = −1, physically impossible on a real sensor) the gyroscope
    is transformed with it all the same, without pseudovector correction.
"""

from collections.abc import Sequence

import numpy as np
from scipy.signal import lfilter
from spine_contracts import AnatomicalPlane
from spine_contracts.models.domain.calibration_schema import BodyAxis

from spine_physiology.config import ImuSignalConfig
from spine_physiology.signals import Rep
from spine_physiology.state import LatentState

# ImuChunk channel names, in the contract order.
IMU_CHANNELS = ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z")

# Standard gravity (m/s²), physical definition: converts linear acceleration to g.
STANDARD_GRAVITY_MS2 = 9.80665

# MPU-6050 sensitivity (spec rev. 3.4, int16 word): accelerometer 16384 LSB/g at +-2 g (divided by 2 each time the
# range doubles); gyroscope per range, with the rounded datasheet values.
ACCEL_FULL_SCALE_LSB = 16384.0
GYRO_LSB_PER_DPS = {250: 131.0, 500: 65.5, 1000: 32.8, 2000: 16.4}

# Body frame: x = anterior, y = left, z = cranial. Vector of each body axis in that frame.
_BODY_AXIS_VECTORS: dict[BodyAxis, tuple[float, float, float]] = {
    BodyAxis.ANTERIOR: (1.0, 0.0, 0.0),
    BodyAxis.POSTERIOR: (-1.0, 0.0, 0.0),
    BodyAxis.LEFT: (0.0, 1.0, 0.0),
    BodyAxis.RIGHT: (0.0, -1.0, 0.0),
    BodyAxis.CRANIAL: (0.0, 0.0, 1.0),
    BodyAxis.CAUDAL: (0.0, 0.0, -1.0),
}

# Rotation axis (body frame) of each plane. A positive angle tilts the cranial axis of the trunk
# anteriorly (sagittal) or to the right (frontal); in the transverse plane it rotates about itself.
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
    """Out-and-back flexion for τ in [0, 1]: rises minimum-jerk in the first half and falls in the second."""
    return np.where(tau < 0.5, _min_jerk_tau(2.0 * tau), _min_jerk_tau(2.0 - 2.0 * tau))


def angle_profile(reps: Sequence[Rep], *, rom_deg: float | Sequence[float], n_samples: int, rate_hz: float) -> np.ndarray:
    """Flexion angle (deg) per sample; 0 outside the reps. `rom_deg` scalar or one per rep."""
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
    """Background angle (deg): rest with spontaneous (Poisson) out-and-back minimum-jerk flexions."""
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
        # Overlapping flexions: the largest wins (the trunk does not bend "twice as much").
        angle[i0:i1] = np.maximum(angle[i0:i1], bend_rom_deg * _bend(tau))
    return angle


def _mounting_matrix(params: ImuSignalConfig) -> np.ndarray:
    """3x3 body -> sensor matrix: each row is the sensor axis expressed in the body frame."""
    m = params.mounting
    return np.array([_BODY_AXIS_VECTORS[m.sensor_x], _BODY_AXIS_VECTORS[m.sensor_y], _BODY_AXIS_VECTORS[m.sensor_z]])


def _rotate(v: np.ndarray, axis: np.ndarray, phi: np.ndarray) -> np.ndarray:
    """Rodrigues: rotates `v` (3,) by an angle `phi` (n,) rad about the unit `axis`; returns (n, 3)."""
    cos = np.cos(phi)[:, None]
    sin = np.sin(phi)[:, None]
    return v * cos + np.cross(axis, v) * sin + axis * np.dot(axis, v) * (1.0 - cos)


def _lowpass(x: np.ndarray, cutoff_hz: float, rate_hz: float) -> np.ndarray:
    """Causal 1st-order low-pass (-3 dB at `cutoff_hz`), initialised at the 1st sample; no effect >= Nyquist."""
    if cutoff_hz >= rate_hz / 2.0 or x.shape[0] == 0:
        return x
    alpha = 1.0 - float(np.exp(-2.0 * np.pi * cutoff_hz / rate_hz))
    zi = (1.0 - alpha) * x[:1]
    y, _ = lfilter([alpha], [1.0, -(1.0 - alpha)], x, axis=0, zi=zi)
    return np.asarray(y)


def _bias(rng: np.random.Generator, static: np.ndarray, sigma: float, tau_s: float, rate_hz: float, n: int, limit: np.ndarray) -> np.ndarray:
    """Bias (n, 3): `static` + Ornstein-Uhlenbeck drift (stationary deviation `sigma`, constant `tau_s`) that
    starts at 0, clipped to +-`limit` per axis (datasheet maxima)."""
    white = rng.standard_normal(size=(n, 3))
    if n == 0:
        return np.clip(np.broadcast_to(static, (0, 3)), -limit, limit)
    a = float(np.exp(-1.0 / (rate_hz * tau_s)))
    drift, _ = lfilter([sigma * np.sqrt(1.0 - a * a)], [1.0, -a], white, axis=0, zi=np.zeros((1, 3)))
    return np.clip(static + drift, -limit, limit)


def _quantize(x: np.ndarray, lsb_per_unit: float) -> np.ndarray:
    """Signed int16 count (saturates at -32768..32767) and back to the physical unit."""
    counts = np.clip(np.round(x * lsb_per_unit), -32768, 32767)
    return counts / lsb_per_unit


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
    """The 6 channels of `IMU_CHANNELS` (float64, len(angle_deg) samples each).

    Body frame: x anterior, y left, z cranial. The trunk (and the sensor, fixed to it) rotates
    `angle_deg` about the plane axis; the specific force in the trunk frame is gravity
    (g upwards in the world) rotated backwards plus the sensor linear acceleration at `lever_arm_m` from the
    flexion axis (tangential + centripetal). Everything is passed to the sensor frame with the mounting matrix.
    The rng always consumes the same number of draws (accel noise, gyro noise, tremor).
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

    # Gravity: the world "up" seen from the rotated trunk (inverse rotation).
    specific_force = gravity_g * _rotate(up, axis, -theta)
    # Linear acceleration of the sensor (r = lever_arm_m along the cranial axis): α×r + ω×(ω×r).
    r = params.lever_arm_m * up
    tangential = alpha[:, None] * np.cross(axis, r)
    centripetal = (omega**2)[:, None] * (axis * np.dot(axis, r) - r)
    specific_force = specific_force + (tangential + centripetal) / STANDARD_GRAVITY_MS2

    accel = specific_force @ mount.T
    gyro = (omega_deg[:, None] * axis) @ mount.T

    bandwidth_hz = min(params.dlpf_hz, rate_hz / 2.0)
    accel_noise = rng.normal(0.0, params.accel_noise_density_ug_rthz * 1e-6 * np.sqrt(bandwidth_hz), size=(n, 3))
    gyro_noise = rng.normal(0.0, params.gyro_noise_density_dps_rthz * np.sqrt(bandwidth_hz), size=(n, 3))
    tremor = rng.normal(0.0, tremor_dps, size=(n, 3))
    # Own branch for the drift: it does not shift the draws above (spawn does not consume the parent stream).
    drift_rng = rng.spawn(1)[0]

    # Movement (and tremor, which is real movement) goes through the low-pass; noise and bias belong to the
    # sensor and are added afterwards.
    accel = _lowpass(accel, params.dlpf_hz, rate_hz)
    gyro = _lowpass(gyro + tremor, params.dlpf_hz, rate_hz)

    ab, gb = params.accel_bias_g, params.gyro_bias_dps
    am, gm = params.accel_bias_max_g, params.gyro_bias_max_dps
    accel_bias = _bias(
        drift_rng, np.array([ab.x, ab.y, ab.z]), params.accel_bias_drift_sigma_g, params.bias_drift_tau_s, rate_hz, n,
        np.array([am.x, am.y, am.z]),
    )  # fmt: skip
    gyro_bias = _bias(drift_rng, np.array([gb.x, gb.y, gb.z]), params.gyro_bias_drift_sigma_dps, params.bias_drift_tau_s, rate_hz, n, np.full(3, gm))
    accel = accel + accel_noise + accel_bias
    gyro = gyro + gyro_noise + gyro_bias
    accel = np.clip(accel, -float(params.accel_range_g), float(params.accel_range_g))
    gyro = np.clip(gyro, -float(params.gyro_range_dps), float(params.gyro_range_dps))
    if params.quantize_output:
        accel = _quantize(accel, ACCEL_FULL_SCALE_LSB * 2.0 / params.accel_range_g)
        gyro = _quantize(gyro, GYRO_LSB_PER_DPS[params.gyro_range_dps])

    return {
        "accel_x": accel[:, 0], "accel_y": accel[:, 1], "accel_z": accel[:, 2],
        "gyro_x": gyro[:, 0], "gyro_y": gyro[:, 1], "gyro_z": gyro[:, 2],
    }  # fmt: skip
