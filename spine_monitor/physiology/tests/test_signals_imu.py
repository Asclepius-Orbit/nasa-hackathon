from pathlib import Path
from typing import Any

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
    """Without noise, bias, drift, filter or quantisation: the ideal model."""
    return params.model_copy(
        update={
            "accel_noise_density_ug_rthz": 0.0,
            "gyro_noise_density_dps_rthz": 0.0,
            "accel_bias_drift_sigma_g": 0.0,
            "gyro_bias_drift_sigma_dps": 0.0,
            "dlpf_hz": 1e6,
            "quantize_output": False,
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
    n = int((reps[-1].end_s + 20.0) * rate)  # reaches beyond the reps
    angle = angle_profile(reps, rom_deg=protocol.rom_deg, n_samples=n, rate_hz=rate)
    assert angle.shape == (n,)
    assert angle.min() == 0.0
    assert angle.max() == pytest.approx(protocol.rom_deg, rel=1e-3)
    assert np.all(angle[: int(protocol.lead_in_s * rate)] == 0.0)
    first = reps[0]
    peak = int(round((first.start_s + first.end_s) / 2 * rate))
    assert angle[peak] == pytest.approx(protocol.rom_deg, rel=1e-3)
    assert angle[int(first.end_s * rate) + 1] == 0.0  # rest between reps


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
    # Each flexion crosses 50 % of the ROM twice: ~ 2 crossings per flexion (~60 in 5 h at 12/h).
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
    # Standing at rest the accelerometer points cranially (sensor_x = cranial in the example).
    assert earth["accel_x"].mean() == pytest.approx(1.0, abs=1e-9)
    assert norm(micro).max() == pytest.approx(0.0, abs=1e-9)


def test_gravity_norm_rotated_noisy(params):
    """With the example noise and bias, while flexing, |accel| stays close to 1 g (slow motion)."""
    rate = params.sample_rate_hz
    t = np.arange(int(30 * rate)) / rate
    angle = 20.0 * (1 - np.cos(2 * np.pi * t / 10.0)) / 2  # slow: negligible linear acceleration
    out = synth_imu(
        np.random.default_rng(3), params, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL,
        gravity_g=1.0, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    assert norm(out).mean() == pytest.approx(1.0, abs=0.02)
    # Partial gravity (Moon): the norm scales with gravity.
    moon = synth_imu(
        np.random.default_rng(3), params, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL,
        gravity_g=0.16, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    assert norm(moon).mean() == pytest.approx(0.16, abs=0.02)


def test_sagittal_tilt_direction(clean):
    """At 30 deg of sagittal flexion, gravity is split into cranial (cos) and posterior (sin) trunk components."""
    angle = np.full(50, 30.0)
    out = synth_imu(
        np.random.default_rng(0), clean, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL,
        gravity_g=1.0, rate_hz=clean.sample_rate_hz, tremor_dps=0.0,
    )  # fmt: skip
    assert out["accel_x"].mean() == pytest.approx(np.cos(np.deg2rad(30.0)))
    # sensor_z = posterior: the world "up" ends up behind the trunk leaning forward.
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
    # sagittal -> left axis; sensor_y = left in the example: gyro_y = dθ/dt, the rest 0.
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
    # sensor_x = cranial: the transverse rotation shows up in gyro_x and gravity does not change.
    assert out["gyro_x"].mean() == pytest.approx(10.0 / (99 / rate))
    assert np.abs(out["gyro_y"]).max() == 0.0
    assert out["accel_x"].min() == pytest.approx(1.0)


def test_linear_motion_component(clean):
    """In microgravity only the linear acceleration remains: centripetal/tangential, scales with lever_arm."""
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
    # sensor_z = posterior: tangential (anterior) with negative sign; sensor_x = cranial: negative centripetal.
    assert out["accel_z"] == pytest.approx(-clean.lever_arm_m * alpha / STANDARD_GRAVITY_MS2, abs=1e-12)
    assert out["accel_x"] == pytest.approx(-clean.lever_arm_m * omega**2 / STANDARD_GRAVITY_MS2, abs=1e-12)


def test_ranges_clip(params):
    rate = params.sample_rate_hz
    t = np.arange(int(4 * rate)) / rate
    angle = 40.0 * (1 - np.cos(2 * np.pi * t / 0.2)) / 2  # very fast: saturates the gyroscope
    tight = params.model_copy(update={"accel_range_g": 2, "gyro_range_dps": 250})
    out = synth_imu(
        np.random.default_rng(0), tight, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL,
        gravity_g=1.0, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    for ch in ("gyro_x", "gyro_y", "gyro_z"):
        assert np.abs(out[ch]).max() <= 250.0
    for ch in ("accel_x", "accel_y", "accel_z"):
        assert np.abs(out[ch]).max() <= 2.0
    assert np.abs(out["gyro_y"]).max() == 250.0  # really saturates
    free = synth_imu(
        np.random.default_rng(0), params.model_copy(update={"gyro_range_dps": 2000}), angle_deg=angle,
        plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    assert np.abs(free["gyro_y"]).max() > 250.0


def test_bias_and_tremor(params, clean):
    rate = params.sample_rate_hz
    still = np.zeros(int(200 * rate))
    kwargs: Any = dict(angle_deg=still, plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=rate)
    biased = synth_imu(np.random.default_rng(0), params, tremor_dps=0.0, **kwargs)
    assert biased["gyro_x"].mean() == pytest.approx(params.gyro_bias_dps.x, abs=0.05)
    shaky = synth_imu(np.random.default_rng(0), clean, tremor_dps=2.0, **kwargs)
    assert shaky["gyro_y"].std() == pytest.approx(2.0, rel=0.1)


def _still(rate: float, seconds: float = 200.0) -> Any:
    return dict(angle_deg=np.zeros(int(seconds * rate)), plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=rate, tremor_dps=0.0)


def test_lowpass_attenuates_above_cutoff_and_keeps_passband(clean):
    rate = 100.0
    t = np.arange(int(20 * rate)) / rate
    base = clean.model_copy(update={"lever_arm_m": 0.0})
    filt = base.model_copy(update={"dlpf_hz": 5.0})

    def gyro_amp(params, freq):
        angle = np.sin(2 * np.pi * freq * t)
        out = synth_imu(np.random.default_rng(0), params, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=rate, tremor_dps=0.0)
        return np.abs(out["gyro_y"][500:]).max()

    assert gyro_amp(filt, 1.0) / gyro_amp(base, 1.0) > 0.95
    assert gyro_amp(filt, 20.0) / gyro_amp(base, 20.0) < 0.3


def test_lowpass_is_causal_and_responds_gradually(clean):
    rate = 100.0
    angle = np.zeros(200)
    angle[100:] = 30.0
    params = clean.model_copy(update={"lever_arm_m": 0.0, "dlpf_hz": 10.0})
    out = synth_imu(np.random.default_rng(0), params, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=rate, tremor_dps=0.0)
    ideal = np.cos(np.deg2rad(30.0))
    assert np.all(out["accel_x"][:100] == 1.0)  # no pre-response
    assert out["accel_x"][100] > ideal + 0.5 * (1.0 - ideal)  # takes time to follow the step
    assert np.all(np.diff(out["accel_x"][99:]) <= 1e-12)  # monotono
    assert out["accel_x"][-1] == pytest.approx(ideal, abs=1e-3)


def test_lowpass_no_effect_at_or_above_nyquist(clean):
    angle = 10.0 * np.sin(np.arange(200) / 5.0)
    kwargs: Any = dict(angle_deg=angle, plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=100.0, tremor_dps=0.0)
    ideal = synth_imu(np.random.default_rng(0), clean, **kwargs)
    at_nyquist = synth_imu(np.random.default_rng(0), clean.model_copy(update={"dlpf_hz": 50.0}), **kwargs)
    below = synth_imu(np.random.default_rng(0), clean.model_copy(update={"dlpf_hz": 49.0}), **kwargs)
    for ch in IMU_CHANNELS:
        assert np.array_equal(at_nyquist[ch], ideal[ch])
    assert not np.array_equal(below["gyro_y"], ideal["gyro_y"])


@pytest.mark.parametrize("dlpf", [44.0, 10.0, 80.0])
def test_noise_std_follows_datasheet_density_times_sqrt_bandwidth(clean, dlpf):
    rate = 100.0
    params = clean.model_copy(update={"accel_noise_density_ug_rthz": 400.0, "gyro_noise_density_dps_rthz": 0.005, "dlpf_hz": dlpf})
    out = synth_imu(np.random.default_rng(4), params, **_still(rate, 400.0))
    bw = min(dlpf, rate / 2)
    assert out["accel_y"].std() == pytest.approx(400e-6 * np.sqrt(bw), rel=0.05)
    assert out["gyro_x"].std() == pytest.approx(0.005 * np.sqrt(bw), rel=0.05)


def test_bias_drift_is_slow_bounded_and_starts_at_static_bias(clean):
    rate = 10.0
    params = clean.model_copy(
        update={
            "gyro_bias_drift_sigma_dps": 5.0,
            "accel_bias_drift_sigma_g": 0.05,
            "bias_drift_tau_s": 600.0,
            "gyro_bias_dps": clean.gyro_bias_dps.model_copy(update={"x": 1.0}),
        }
    )
    out = synth_imu(np.random.default_rng(1), params, **_still(rate, 6 * 3600.0))
    assert out["gyro_x"][0] == pytest.approx(1.0, abs=0.2)
    assert out["gyro_x"].std() > 1.0  # deriva real
    assert np.abs(np.diff(out["gyro_x"])).max() < 0.5  # lenta
    assert np.abs(out["gyro_x"]).max() <= params.gyro_bias_max_dps
    assert np.abs(out["gyro_z"]).max() <= params.gyro_bias_max_dps
    assert np.abs(out["accel_y"]).max() <= params.accel_bias_max_g.y + 1e-12
    assert np.abs(out["accel_z"]).max() <= params.accel_bias_max_g.z + 1e-12


def test_bias_drift_bounded_when_sigma_exceeds_datasheet_max(clean):
    params = clean.model_copy(update={"gyro_bias_drift_sigma_dps": 100.0, "bias_drift_tau_s": 60.0, "gyro_bias_max_dps": 20.0})
    out = synth_imu(np.random.default_rng(2), params, **_still(10.0, 3600.0))
    assert np.abs(out["gyro_y"]).max() == pytest.approx(20.0)  # llega al tope, no lo pasa


def test_no_drift_keeps_bias_constant(clean):
    params = clean.model_copy(update={"gyro_bias_dps": clean.gyro_bias_dps.model_copy(update={"y": 0.5})})
    out = synth_imu(np.random.default_rng(1), params, **_still(10.0, 600.0))
    assert np.all(out["gyro_y"] == 0.5)


def test_drift_does_not_shift_the_noise_stream(params):
    """The white noise comes from the same rng as before: a different drift does not change its samples."""
    a = params.model_copy(update={"gyro_bias_drift_sigma_dps": 0.0, "quantize_output": False})
    b = a.model_copy(update={"gyro_bias_drift_sigma_dps": 0.5})
    kwargs = _still(100.0, 20.0)
    x = synth_imu(np.random.default_rng(3), a, **kwargs)["gyro_x"]
    y = synth_imu(np.random.default_rng(3), b, **kwargs)["gyro_x"]
    assert np.abs(np.diff(y - x)).max() < 0.1  # the difference is only the slow drift


def test_quantization_to_int16_steps(params):
    rate = params.sample_rate_hz
    t = np.arange(int(20 * rate)) / rate
    angle = 20.0 * (1 - np.cos(2 * np.pi * t / 4.0)) / 2
    out = synth_imu(np.random.default_rng(0), params, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=rate, tremor_dps=1.0)
    for ch in ("accel_x", "accel_y", "accel_z"):
        counts = out[ch] * 8192.0
        assert np.array_equal(counts, np.round(counts))
        assert len(np.unique(counts)) > 5
    for ch in ("gyro_x", "gyro_y", "gyro_z"):
        counts = out[ch] * 65.5
        assert counts == pytest.approx(np.round(counts), abs=1e-9)


@pytest.mark.parametrize(("accel_range", "gyro_range", "accel_lsb", "gyro_lsb"), [(2, 250, 16384.0, 131.0), (8, 1000, 4096.0, 32.8), (16, 2000, 2048.0, 16.4)])
def test_quantization_lsb_follows_range(params, accel_range, gyro_range, accel_lsb, gyro_lsb):
    p = params.model_copy(update={"accel_range_g": accel_range, "gyro_range_dps": gyro_range})
    out = synth_imu(np.random.default_rng(0), p, **{**_still(100.0, 5.0), "tremor_dps": 1.0})
    assert out["accel_x"] * accel_lsb == pytest.approx(np.round(out["accel_x"] * accel_lsb), abs=1e-9)
    assert out["gyro_y"] * gyro_lsb == pytest.approx(np.round(out["gyro_y"] * gyro_lsb), abs=1e-9)


def test_quantization_saturates_at_int16_limits(params):
    huge = params.model_copy(update={"accel_range_g": 4, "gyro_range_dps": 500})
    t = np.arange(400) / 100.0
    angle = 40.0 * (1 - np.cos(2 * np.pi * t / 0.2)) / 2
    out = synth_imu(np.random.default_rng(0), huge, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=100.0, tremor_dps=0.0)
    assert out["gyro_y"].max() <= 32767 / 65.5 and out["gyro_y"].min() >= -32768 / 65.5
    assert out["gyro_y"].max() == pytest.approx(500.0, abs=0.02)


def test_quantization_can_be_disabled(params):
    p = params.model_copy(update={"quantize_output": False, "accel_noise_density_ug_rthz": 400.0})
    out = synth_imu(np.random.default_rng(0), p, **_still(100.0, 5.0))
    counts = out["accel_y"] * 8192.0
    assert not np.allclose(counts, np.round(counts))


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


def test_background_bends_are_rare_at_a_few_per_hour():
    for seed in range(5):
        angle = background_angle_profile(
            np.random.default_rng(seed), n_samples=600, rate_hz=10.0, bends_per_hour=6.0, bend_rom_deg=20.0, bend_duration_s=3.0
        )  # 60 s: 0.1 expected flexions
        assert np.count_nonzero(angle) < 0.5 * angle.size


def test_two_samples_are_enough_for_a_gyro_signal(clean):
    out = synth_imu(
        np.random.default_rng(0), clean, angle_deg=np.array([0.0, 10.0]), plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=10.0, tremor_dps=0.0
    )
    assert max(abs(out[c]).max() for c in ("gyro_x", "gyro_y", "gyro_z")) == pytest.approx(100.0)  # 10 deg en 0.1 s
