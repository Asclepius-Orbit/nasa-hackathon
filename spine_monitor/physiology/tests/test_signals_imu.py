from pathlib import Path

import numpy as np
import pytest
from spine_contracts import AnatomicalPlane
from spine_physiology.config import ImuSignalConfig, load_config
from spine_physiology.signals import rep_schedule
from spine_physiology.signals.imu import (
    IMU_CHANNELS,
    STANDARD_GRAVITY_MS2,
    angle_profile,
    background_angle_profile,
    effective_rom_deg,
    min_jerk,
    synth_imu,
)
from spine_physiology.state import LatentState

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
ZERO = {"x": 0.0, "y": 0.0, "z": 0.0}


@pytest.fixture(scope="module")
def config():
    return load_config(EXAMPLE)


@pytest.fixture(scope="module")
def params(config) -> ImuSignalConfig:
    return config.signals.imu


@pytest.fixture(scope="module")
def clean(params) -> ImuSignalConfig:
    """Sin ruido ni sesgo, con rangos holgados salvo los que el test cambie."""
    return params.model_copy(
        update={
            "accel_noise_g": 0.0,
            "gyro_noise_dps": 0.0,
            "accel_bias_g": params.accel_bias_g.model_copy(update=ZERO),
            "gyro_bias_dps": params.gyro_bias_dps.model_copy(update=ZERO),
        }
    )


@pytest.fixture(scope="module")
def protocol(config):
    return config.protocol("lat_pulldown_standing_v1")


def state(rom_motor_control=0.0, pain_stiffness=0.0) -> LatentState:
    return LatentState(
        astronaut_id="AST-01",
        day=0,
        spine_elongation_mm=0.0,
        muscle_deconditioning=0.0,
        rom_motor_control=rom_motor_control,
        pain_stiffness=pain_stiffness,
        dose_ratio=1.0,
    )


def norm(arrays: dict[str, np.ndarray]) -> np.ndarray:
    return np.sqrt(arrays["accel_x"] ** 2 + arrays["accel_y"] ** 2 + arrays["accel_z"] ** 2)


def test_min_jerk_profile():
    mj = min_jerk(101)
    assert mj[0] == 0.0 and mj[-1] == pytest.approx(1.0)
    assert mj[50] == pytest.approx(0.5)
    assert np.all(np.diff(mj) >= 0)
    assert len(min_jerk(0)) == 0


def test_effective_rom(params):
    assert effective_rom_deg(30.0, state(), params) == pytest.approx(30.0)
    got = effective_rom_deg(30.0, state(0.1, 0.4), params)
    assert got == pytest.approx(30.0 * (1 - params.rom_loss_gain * 0.1) * (1 - params.pain_rom_gain * 0.4))
    assert got < 30.0
    assert effective_rom_deg(30.0, state(rom_motor_control=1.0 / params.rom_loss_gain + 1.0), params) == 0.0


def test_angle_profile_reps(protocol, params):
    rate = params.sample_rate_hz
    reps = rep_schedule(protocol)
    n = int((reps[-1].end_s + 20.0) * rate)  # alcanza más allá de las reps
    angle = angle_profile(reps, rom_deg=protocol.rom_deg, n_samples=n, rate_hz=rate)
    assert angle.shape == (n,)
    assert angle.min() == 0.0
    assert angle.max() == pytest.approx(protocol.rom_deg, rel=1e-3)
    assert np.all(angle[: int(protocol.lead_in_s * rate)] == 0.0)
    first = reps[0]
    peak = int(round((first.start_s + first.end_s) / 2 * rate))
    assert angle[peak] == pytest.approx(protocol.rom_deg, rel=1e-3)
    assert angle[int(first.end_s * rate) + 1] == 0.0  # descanso entre reps


def test_angle_profile_per_rep_rom(protocol, params):
    rate = params.sample_rate_hz
    reps = rep_schedule(protocol)[:3]
    roms = [10.0, 20.0, 30.0]
    angle = angle_profile(reps, rom_deg=roms, n_samples=int(60 * rate), rate_hz=rate)
    for rep, rom in zip(reps, roms, strict=True):
        seg = angle[int(rep.start_s * rate) : int(rep.end_s * rate)]
        assert seg.max() == pytest.approx(rom, rel=1e-3)
    with pytest.raises(ValueError):
        angle_profile(reps, rom_deg=[1.0], n_samples=100, rate_hz=rate)


def test_background_profile_bends():
    rate, hours = 10.0, 5.0
    n = int(hours * 3600 * rate)
    kwargs = dict(n_samples=n, rate_hz=rate, bends_per_hour=12.0, bend_rom_deg=20.0, bend_duration_s=3.0)
    angle = background_angle_profile(np.random.default_rng(1), **kwargs)
    assert angle.max() == pytest.approx(20.0, rel=1e-3)
    assert angle.min() == 0.0
    # Cada flexión cruza el 50 % del ROM dos veces: ~ 2 cruces por flexión (~60 en 5 h a 12/h).
    crossings = np.count_nonzero(np.diff((angle > 10.0).astype(int)) != 0)
    assert 60 <= crossings <= 180
    assert np.array_equal(angle, background_angle_profile(np.random.default_rng(1), **kwargs))
    assert not np.array_equal(angle, background_angle_profile(np.random.default_rng(2), **kwargs))
    none = background_angle_profile(np.random.default_rng(1), **{**kwargs, "bends_per_hour": 0.0})
    assert not none.any()


def test_gravity_norm_earth_and_micro(clean):
    rate = clean.sample_rate_hz
    still = np.zeros(int(20 * rate))
    earth = synth_imu(
        np.random.default_rng(0), clean, angle_deg=still, plane=AnatomicalPlane.SAGITTAL,
        gravity_g=1.0, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    micro = synth_imu(
        np.random.default_rng(0), clean, angle_deg=still, plane=AnatomicalPlane.SAGITTAL,
        gravity_g=0.0, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    assert set(earth) == set(IMU_CHANNELS)
    assert norm(earth).mean() == pytest.approx(1.0, abs=1e-9)
    # De pie y en reposo el acelerómetro apunta a craneal (sensor_x = cranial en el ejemplo).
    assert earth["accel_x"].mean() == pytest.approx(1.0, abs=1e-9)
    assert norm(micro).max() == pytest.approx(0.0, abs=1e-9)


def test_gravity_norm_rotated_noisy(params):
    """Con ruido y sesgo del ejemplo, y flexionando, |accel| sigue cerca de 1 g (movimiento lento)."""
    rate = params.sample_rate_hz
    t = np.arange(int(30 * rate)) / rate
    angle = 20.0 * (1 - np.cos(2 * np.pi * t / 10.0)) / 2  # lento: aceleración lineal despreciable
    out = synth_imu(
        np.random.default_rng(3), params, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL,
        gravity_g=1.0, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    assert norm(out).mean() == pytest.approx(1.0, abs=0.02)
    # Gravedad parcial (Luna): la norma escala con la gravedad.
    moon = synth_imu(
        np.random.default_rng(3), params, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL,
        gravity_g=0.16, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    assert norm(moon).mean() == pytest.approx(0.16, abs=0.02)


def test_sagittal_tilt_direction(clean):
    """A 30 deg de flexión sagital, la gravedad se reparte en craneal (cos) y posterior (sen) del tronco."""
    angle = np.full(50, 30.0)
    out = synth_imu(
        np.random.default_rng(0), clean, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL,
        gravity_g=1.0, rate_hz=clean.sample_rate_hz, tremor_dps=0.0,
    )  # fmt: skip
    assert out["accel_x"].mean() == pytest.approx(np.cos(np.deg2rad(30.0)))
    # sensor_z = posterior: el "arriba" del mundo queda hacia atrás del tronco inclinado hacia delante.
    assert out["accel_z"].mean() == pytest.approx(np.sin(np.deg2rad(30.0)))
    assert out["accel_y"].mean() == pytest.approx(0.0, abs=1e-12)


def test_gyro_integrates_back_to_angle(clean, protocol):
    rate = clean.sample_rate_hz
    reps = rep_schedule(protocol)
    n = int((reps[-1].end_s + 5.0) * rate)
    angle = angle_profile(reps, rom_deg=protocol.rom_deg, n_samples=n, rate_hz=rate)
    out = synth_imu(
        np.random.default_rng(0), clean, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL,
        gravity_g=1.0, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    # sagital -> eje izquierda; sensor_y = left en el ejemplo: gyro_y = dθ/dt, el resto 0.
    assert np.abs(out["gyro_x"]).max() == 0.0 and np.abs(out["gyro_z"]).max() == 0.0
    integrated = np.concatenate([[0.0], np.cumsum((out["gyro_y"][1:] + out["gyro_y"][:-1]) / 2 / rate)])
    assert np.abs(integrated - angle).max() < 0.02 * protocol.rom_deg
    assert integrated.max() == pytest.approx(protocol.rom_deg, rel=0.02)


def test_transverse_plane_gyro_on_cranial_axis(clean):
    rate = clean.sample_rate_hz
    angle = np.linspace(0.0, 10.0, 100)
    out = synth_imu(
        np.random.default_rng(0), clean, angle_deg=angle, plane=AnatomicalPlane.TRANSVERSE,
        gravity_g=1.0, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    # sensor_x = cranial: el giro transversal aparece en gyro_x y la gravedad no cambia.
    assert out["gyro_x"].mean() == pytest.approx(10.0 / (99 / rate))
    assert np.abs(out["gyro_y"]).max() == 0.0
    assert out["accel_x"].min() == pytest.approx(1.0)


def test_linear_motion_component(clean):
    """En microgravedad solo queda la aceleración lineal: centrípeta/tangencial, escala con lever_arm."""
    rate = clean.sample_rate_hz
    t = np.arange(int(10 * rate)) / rate
    angle = 20.0 * (1 - np.cos(2 * np.pi * t / 2.0)) / 2
    kwargs = dict(angle_deg=angle, plane=AnatomicalPlane.SAGITTAL, gravity_g=0.0, rate_hz=rate, tremor_dps=0.0)
    out = synth_imu(np.random.default_rng(0), clean, **kwargs)
    assert norm(out).max() > 1e-3
    no_arm = synth_imu(np.random.default_rng(0), clean.model_copy(update={"lever_arm_m": 0.0}), **kwargs)
    assert norm(no_arm).max() == pytest.approx(0.0, abs=1e-12)
    omega = np.deg2rad(np.gradient(angle, 1 / rate))
    alpha = np.gradient(omega, 1 / rate)
    # sensor_z = posterior: tangencial (anterior) con signo negativo; sensor_x = cranial: centrípeta negativa.
    assert out["accel_z"] == pytest.approx(-clean.lever_arm_m * alpha / STANDARD_GRAVITY_MS2, abs=1e-12)
    assert out["accel_x"] == pytest.approx(-clean.lever_arm_m * omega**2 / STANDARD_GRAVITY_MS2, abs=1e-12)


def test_ranges_clip(params):
    rate = params.sample_rate_hz
    t = np.arange(int(4 * rate)) / rate
    angle = 40.0 * (1 - np.cos(2 * np.pi * t / 0.2)) / 2  # muy rápido: satura el giróscopo
    tight = params.model_copy(update={"accel_range_g": 2, "gyro_range_dps": 250})
    out = synth_imu(
        np.random.default_rng(0), tight, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL,
        gravity_g=1.0, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    for ch in ("gyro_x", "gyro_y", "gyro_z"):
        assert np.abs(out[ch]).max() <= 250.0
    for ch in ("accel_x", "accel_y", "accel_z"):
        assert np.abs(out[ch]).max() <= 2.0
    assert np.abs(out["gyro_y"]).max() == 250.0  # realmente satura
    free = synth_imu(
        np.random.default_rng(0), params.model_copy(update={"gyro_range_dps": 2000}), angle_deg=angle,
        plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    assert np.abs(free["gyro_y"]).max() > 250.0


def test_bias_noise_and_tremor(params, clean):
    rate = params.sample_rate_hz
    still = np.zeros(int(200 * rate))
    kwargs = dict(angle_deg=still, plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=rate)
    biased = synth_imu(np.random.default_rng(0), params, tremor_dps=0.0, **kwargs)
    assert biased["gyro_x"].mean() == pytest.approx(params.gyro_bias_dps.x, abs=0.02)
    assert biased["gyro_x"].std() == pytest.approx(params.gyro_noise_dps, rel=0.1)
    shaky = synth_imu(np.random.default_rng(0), clean, tremor_dps=2.0, **kwargs)
    assert shaky["gyro_y"].std() == pytest.approx(2.0, rel=0.1)


def test_determinism(params):
    rate = params.sample_rate_hz
    angle = background_angle_profile(
        np.random.default_rng(5), n_samples=int(600 * rate), rate_hz=rate, bends_per_hour=60.0,
        bend_rom_deg=20.0, bend_duration_s=3.0,
    )  # fmt: skip
    kwargs = dict(angle_deg=angle, plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=rate, tremor_dps=1.0)
    a = synth_imu(np.random.default_rng(9), params, **kwargs)
    b = synth_imu(np.random.default_rng(9), params, **kwargs)
    c = synth_imu(np.random.default_rng(10), params, **kwargs)
    for ch in IMU_CHANNELS:
        assert a[ch].dtype == np.float64
        assert np.array_equal(a[ch], b[ch])
    assert not np.array_equal(a["accel_x"], c["accel_x"])
