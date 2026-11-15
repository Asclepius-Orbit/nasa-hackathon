from pathlib import Path

import numpy as np
import pytest
from spine_physiology.config import StretchSignalConfig, load_config
from spine_physiology.signals.stretch import (
    adc_to_resistance,
    apply_hysteresis,
    cord_length,
    derive,
    diurnal_loss_mm,
    length_to_resistance,
    lot_gain,
    measured_curve,
    resistance_to_adc,
    resistance_to_length,
    synth_stretch,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
REST_MM = 400.0  # test value, independent of the YAML
# Turns off the new non-idealities (per-reading noise, dispersion between readings, creep, lot).
QUIET = {"read_noise_ohm": 0.0, "repeat_dispersion_ohm": 0.0, "creep_fraction": 0.0, "lot_variation_fraction": 0.0}


@pytest.fixture(scope="module")
def config():
    return load_config(EXAMPLE)


@pytest.fixture(scope="module")
def params(config) -> StretchSignalConfig:
    return config.signals.stretch


@pytest.fixture(scope="module")
def ideal(params) -> StretchSignalConfig:
    """Without ADC noise or hysteresis."""
    return params.model_copy(update={"adc_noise_counts": 0.0, "hysteresis_gain": 0.0, **QUIET})


def test_diurnal_loss(params):
    assert diurnal_loss_mm(params, gravity_g=1.0, awake_fraction=1.0) == pytest.approx(params.diurnal_loss_mm_at_1g)
    assert diurnal_loss_mm(params, gravity_g=0.0, awake_fraction=1.0) == 0.0
    assert diurnal_loss_mm(params, gravity_g=1.0, awake_fraction=2.0) == pytest.approx(params.diurnal_loss_mm_at_1g)
    got = diurnal_loss_mm(params, gravity_g=1.0, awake_fraction=np.array([-1.0, 0.0, 0.5, 1.0]))
    assert got == pytest.approx(params.diurnal_loss_mm_at_1g * np.array([0.0, 0.0, 0.5, 1.0]))


def test_cord_length(params):
    angle = np.array([0.0, 10.0, -10.0])
    got = cord_length(params, rest_length_mm=REST_MM, elongation_mm=5.0, angle_deg=angle, offset_mm=1.0)
    base = REST_MM + params.elongation_coupling * 5.0 - 1.0
    assert got == pytest.approx(base + params.flexion_mm_per_deg * np.array([0.0, 10.0, 10.0]))
    per_sample = cord_length(
        params, rest_length_mm=REST_MM, elongation_mm=0.0, angle_deg=angle, offset_mm=np.array([0.0, 1.0, 2.0])
    )  # fmt: skip
    assert per_sample[2] - per_sample[1] == pytest.approx(-1.0)


def test_curve_monotone_inversion(params):
    curve = params.curve_pairs()
    lengths = np.linspace(curve[0][1] - 20.0, curve[-1][1] + 20.0, 500)  # includes extrapolation
    resistance = length_to_resistance(lengths, curve)
    assert np.all(np.diff(resistance) > 0)
    assert resistance_to_length(resistance, curve) == pytest.approx(lengths)
    # At the nodes it reproduces the exact curve.
    r_nodes = np.array([p[0] for p in curve])
    l_nodes = np.array([p[1] for p in curve])
    assert resistance_to_length(r_nodes, curve) == pytest.approx(l_nodes)
    assert length_to_resistance(l_nodes, curve) == pytest.approx(r_nodes)
    # Extrapolation with the slope of the end segment.
    slope = (l_nodes[-1] - l_nodes[-2]) / (r_nodes[-1] - r_nodes[-2])
    assert resistance_to_length(np.array([r_nodes[-1] + 100.0]), curve)[0] == pytest.approx(l_nodes[-1] + 100.0 * slope)


def test_curve_rejects_non_monotone():
    with pytest.raises(ValueError):
        length_to_resistance(np.array([1.0]), [(1.0, 10.0), (2.0, 5.0)])
    with pytest.raises(ValueError):
        resistance_to_length(np.array([1.0]), [(1.0, 10.0)])


def test_adc_roundtrip_within_quantisation(ideal):
    curve = ideal.curve_pairs()
    lengths = np.linspace(curve[0][1], curve[-1][1], 400)
    r = length_to_resistance(lengths, curve)
    adc = resistance_to_adc(np.random.default_rng(0), r, ideal)
    assert adc.dtype == np.int64
    full_scale = 2**ideal.circuit.adc_bits - 1
    assert adc.min() >= 0 and adc.max() <= full_scale
    _, recovered = derive(adc, ideal, curve)
    # The maximum error is the length spanned by +-0.5 count at each point (with rounding margin).
    r_hi = adc_to_resistance(adc + 0.5, ideal)
    r_lo = adc_to_resistance(adc - 0.5, ideal)
    tol = (resistance_to_length(r_hi, curve) - resistance_to_length(r_lo, curve)) / 2 + 1e-9
    assert np.all(np.abs(recovered - lengths) <= tol)
    assert np.abs(recovered - lengths).max() <= 2.0  # mm: one step of the 10-bit ADC spans ~3 mm in this stretch


def test_adc_resistance_roundtrip(ideal):
    r = np.array([5400.0, 6012.0, 7560.0])
    adc = resistance_to_adc(np.random.default_rng(0), r, ideal)
    back = adc_to_resistance(adc, ideal)
    d_r = adc_to_resistance(adc + 0.5, ideal) - adc_to_resistance(adc - 0.5, ideal)
    assert np.all(np.abs(back - r) <= d_r)


def test_adc_clips_and_stays_finite(ideal):
    full_scale = 2**ideal.circuit.adc_bits - 1
    huge = resistance_to_adc(np.random.default_rng(0), np.array([1e12, 1e-3]), ideal)
    assert huge.tolist() == [full_scale - 1, 1]  # never at the ADC rails
    r = adc_to_resistance(np.array([0, full_scale]), ideal)
    assert np.all(np.isfinite(r)) and np.all(r > 0)


def test_adc_noise(params):
    r = np.full(20000, 6000.0)
    adc = resistance_to_adc(np.random.default_rng(0), r, params)
    assert adc.std() == pytest.approx(params.adc_noise_counts, rel=0.1)  # quantization adds little
    quiet = resistance_to_adc(np.random.default_rng(0), r, params.model_copy(update={"adc_noise_counts": 0.0}))
    assert np.ptp(quiet) == 0


def test_hysteresis_recovery_time_constant(params):
    rate = params.sample_rate_hz
    tau = params.hysteresis_tau_s
    n = int(8 * tau * rate)
    length = np.full(n, REST_MM)
    stretched = int(5 * rate)
    length[:stretched] = REST_MM + 30.0  # 5 s stretch and return to rest
    effective, residual = apply_hysteresis(
        length, rest_length_mm=REST_MM, rate_hz=rate, params=params, initial_residual_mm=0.0
    )  # fmt: skip
    assert residual[0] == pytest.approx(params.hysteresis_gain * 30.0)
    assert np.all(residual >= 0)
    assert effective == pytest.approx(length + residual)
    peak = residual[stretched - 1]
    for k in (1, 2, 3):
        idx = stretched - 1 + int(k * tau * rate)
        assert residual[idx] == pytest.approx(peak * np.exp(-k), rel=1e-6)
    assert np.all(np.diff(residual[stretched - 1 :]) <= 1e-12)  # it only decays


def test_hysteresis_holds_under_sustained_stretch_and_continues_across_sessions(params):
    rate = params.sample_rate_hz
    length = np.full(int(10 * rate), REST_MM + 10.0)
    _, residual = apply_hysteresis(
        length, rest_length_mm=REST_MM, rate_hz=rate, params=params, initial_residual_mm=0.0
    )  # fmt: skip
    assert residual == pytest.approx(params.hysteresis_gain * 10.0)
    rest = np.full(int(10 * rate), REST_MM)
    _, cont = apply_hysteresis(rest, rest_length_mm=REST_MM, rate_hz=rate, params=params, initial_residual_mm=2.0)
    assert cont[0] == pytest.approx(2.0 * np.exp(-1 / (rate * params.hysteresis_tau_s)))
    assert cont[-1] < cont[0]


def test_hysteresis_blocks_equal_naive_recursion(params):
    """Long sessions (several blocks) match the sample-by-sample recursion."""
    rate = params.sample_rate_hz
    n = int(40 * params.hysteresis_tau_s * rate)
    rng = np.random.default_rng(0)
    length = REST_MM + np.maximum(rng.normal(0.0, 20.0, size=n), 0.0) * (rng.random(n) < 0.002)
    _, residual = apply_hysteresis(length, rest_length_mm=REST_MM, rate_hz=rate, params=params, initial_residual_mm=1.0)
    a = np.exp(-1 / (rate * params.hysteresis_tau_s))
    target = params.hysteresis_gain * np.maximum(length - REST_MM, 0.0)
    expected = np.empty(n)
    prev = 1.0
    for k in range(n):
        prev = max(prev * a, target[k])
        expected[k] = prev
    assert residual == pytest.approx(expected, rel=1e-9, abs=1e-12)


def test_synth_stretch_chain(ideal, params):
    rate = params.sample_rate_hz
    curve = ideal.curve_pairs()
    t = np.arange(int(30 * rate)) / rate
    angle = 20.0 * (1 - np.cos(2 * np.pi * t / 6.0)) / 2
    length = cord_length(ideal, rest_length_mm=REST_MM, elongation_mm=4.0, angle_deg=angle, offset_mm=0.0)
    out = synth_stretch(
        np.random.default_rng(0), ideal, length_mm=length, rest_length_mm=REST_MM, rate_hz=rate, initial_residual_mm=0.0
    )  # fmt: skip
    assert out.adc_counts.dtype == np.int64
    assert out.length_mm == pytest.approx(length)
    assert out.effective_length_mm == pytest.approx(length)  # without hysteresis
    assert not out.residual_mm.any()
    _, recovered = derive(out.adc_counts, ideal, curve)
    assert np.abs(recovered - length).max() < 1.0
    # With hysteresis, the derived length stays above the real one (residual >= 0).
    hyst = synth_stretch(
        np.random.default_rng(0), params.model_copy(update={"adc_noise_counts": 0.0, **QUIET}), length_mm=length,
        rest_length_mm=REST_MM, rate_hz=rate, initial_residual_mm=0.0,
    )  # fmt: skip
    assert hyst.residual_mm.max() > 0
    _, derived = derive(hyst.adc_counts, params, curve)
    assert np.abs(derived - hyst.effective_length_mm).max() < 1.0


def test_derive_uses_calibration_curve(ideal):
    """A calibration different from the real one shifts the derived length, not the resistance."""
    curve = ideal.curve_pairs()
    shifted = tuple((r, length + 5.0) for r, length in curve)
    adc = resistance_to_adc(np.random.default_rng(0), np.array([6000.0]), ideal)
    r0, l0 = derive(adc, ideal, curve)
    r1, l1 = derive(adc, ideal, shifted)
    assert r0 == pytest.approx(r1)
    assert l1[0] - l0[0] == pytest.approx(5.0)


def test_measured_curve(config, params):
    cal = config.calibration.stretch
    curve = measured_curve(np.random.default_rng(0), params, cal, rest_length_mm=REST_MM)
    assert len(curve) == cal.n_points
    rs = [p[0] for p in curve]
    ls = [p[1] for p in curve]
    assert all(b > a for a, b in zip(rs, rs[1:], strict=False))
    assert all(b > a for a, b in zip(ls, ls[1:], strict=False))
    assert ls[0] == REST_MM
    assert rs[0] == pytest.approx(float(length_to_resistance(np.array([REST_MM]), params.curve_pairs())[0]))
    # Measurement error bounded with respect to the real curve.
    true = resistance_to_length(np.array(rs), params.curve_pairs())
    step = (params.curve_pairs()[-1][1] - REST_MM) / (cal.n_points - 1)
    assert np.abs(np.array(ls) - true).max() <= step / 4 + 1e-9


def test_measured_curve_noise_scale_and_zero_noise(config, params):
    cal = config.calibration.stretch
    exact = measured_curve(
        np.random.default_rng(0), params, cal.model_copy(update={"measurement_noise_mm": 0.0}), rest_length_mm=REST_MM
    )  # fmt: skip
    true = resistance_to_length(np.array([p[0] for p in exact]), params.curve_pairs())
    assert np.array([p[1] for p in exact]) == pytest.approx(true)
    # Rest points above the real curve still give a valid curve.
    high = measured_curve(np.random.default_rng(0), params, cal, rest_length_mm=params.curve_pairs()[-1][1] + 10.0)
    assert all(b[1] > a[1] and b[0] > a[0] for a, b in zip(high, high[1:], strict=False))


def test_determinism(config, params):
    rate = params.sample_rate_hz
    t = np.arange(int(20 * rate)) / rate
    length = REST_MM + 15.0 * (1 - np.cos(2 * np.pi * t / 4.0)) / 2
    kwargs = dict(length_mm=length, rest_length_mm=REST_MM, rate_hz=rate, initial_residual_mm=0.5)
    a = synth_stretch(np.random.default_rng(4), params, **kwargs)
    b = synth_stretch(np.random.default_rng(4), params, **kwargs)
    c = synth_stretch(np.random.default_rng(5), params, **kwargs)
    assert np.array_equal(a.adc_counts, b.adc_counts) and np.array_equal(a.residual_mm, b.residual_mm)
    assert not np.array_equal(a.adc_counts, c.adc_counts)
    cal = config.calibration.stretch
    m1 = measured_curve(np.random.default_rng(1), params, cal, rest_length_mm=REST_MM)
    m2 = measured_curve(np.random.default_rng(1), params, cal, rest_length_mm=REST_MM)
    assert m1 == m2


def test_curve_validation_boundaries_and_messages():
    x = np.array([1.0])
    # Two points are enough; small (< 1) but positive increments are valid.
    assert resistance_to_length(np.array([1.25]), [(1.0, 10.0), (1.5, 10.5)])[0] == pytest.approx(10.25)
    with pytest.raises(ValueError, match=r"^curve must have at least two \(resistance_ohm, length_mm\) points$"):
        resistance_to_length(x, [(1.0, 10.0)])
    message = r"^curve must be strictly increasing in resistance and length$"
    for flat in ([(1.0, 10.0), (1.0, 20.0)], [(1.0, 10.0), (2.0, 10.0)], [(1.0, 10.0), (3.0, 20.0), (2.0, 30.0)], [(1.0, 10.0), (3.0, 20.0), (4.0, 15.0)]):
        with pytest.raises(ValueError, match=message):
            length_to_resistance(x, flat)


def test_measured_curve_spreads_lengths_evenly_up_to_the_top_of_the_true_curve(config, params):
    # Real curve of the example: lengths 585..812 mm; 4 calibration points.
    # Rest 650: uniform split 650 -> 812 in 3 steps of 54: 650, 704, 758, 812.
    # Rest 820 (above the range): range = 820 + (812 - 585) = 1047, steps of 227/3: 820, 895.67, 971.33, 1047.
    assert params.curve_pairs()[0][1] == 585.0 and params.curve_pairs()[-1][1] == 812.0 and config.calibration.stretch.n_points == 4
    cal = config.calibration.stretch.model_copy(update={"measurement_noise_mm": 0.0})
    expected = {650.0: [650.0, 704.0, 758.0, 812.0], 820.0: [820.0, 820 + 227 / 3, 820 + 454 / 3, 1047.0]}
    for rest, lengths in expected.items():
        curve = measured_curve(np.random.default_rng(0), params, cal, rest_length_mm=rest)
        assert [p[1] for p in curve] == pytest.approx(lengths)


def test_measurement_error_is_clipped_to_a_quarter_of_the_step_on_both_sides(config, params):
    cal = config.calibration.stretch.model_copy(update={"measurement_noise_mm": 1000.0})
    true = params.curve_pairs()
    step = (true[-1][1] - REST_MM) / (cal.n_points - 1)
    nominal = np.linspace(REST_MM, true[-1][1], cal.n_points)
    seen_low = seen_high = False
    for seed in range(20):
        lengths = np.array([p[1] for p in measured_curve(np.random.default_rng(seed), params, cal, rest_length_mm=REST_MM)])
        error = (lengths - nominal)[1:]
        assert np.abs(error).max() <= step / 4 + 1e-9 and lengths[0] == REST_MM
        seen_low |= bool(np.isclose(error.min(), -step / 4))
        seen_high |= bool(np.isclose(error.max(), step / 4))
    assert seen_low and seen_high


def _quiet(params, **update):
    return params.model_copy(update={"adc_noise_counts": 0.0, "hysteresis_gain": 0.0, **QUIET, **update})


def _flat(params, resistance_ohm=6000.0, n=4000, seed=1, **update):
    """Constant cord at the length that the true curve associates with `resistance_ohm`."""
    p = _quiet(params, **update)
    length = resistance_to_length(np.array([resistance_ohm]), p.curve_pairs())[0]
    out = synth_stretch(
        np.random.default_rng(seed), p, length_mm=np.full(n, length), rest_length_mm=length,
        rate_hz=p.sample_rate_hz, initial_residual_mm=0.0,
    )  # fmt: skip
    return p, out


def test_adc_never_touches_the_rails(params):
    full_scale = 2**params.circuit.adc_bits - 1
    noisy = params.model_copy(update={"adc_noise_counts": 50.0})
    counts = resistance_to_adc(np.random.default_rng(0), np.array([1e-3] * 500 + [1e12] * 500), noisy)
    assert counts.min() == 1 and counts.max() == full_scale - 1  # recorta, no descarta


def test_example_circuit_is_the_firmware_divider(params):
    assert params.circuit.adc_bits == 10 and params.circuit.series_resistor_ohm == 10000.0


def test_read_noise_is_per_reading_in_ohms(params):
    _, quiet = _flat(params)
    _, noisy = _flat(params, read_noise_ohm=300.0)
    r_quiet = adc_to_resistance(quiet.adc_counts, params)
    r_noisy = adc_to_resistance(noisy.adc_counts, params)
    assert np.ptp(r_quiet) < 30.0  # quantisation only
    assert r_noisy.std() == pytest.approx(300.0, rel=0.15)
    assert r_noisy.mean() == pytest.approx(r_quiet.mean(), abs=30.0)


def test_repeat_dispersion_is_one_offset_per_session_matching_the_bench_spread(params):
    # Bench: three readings of the same length differ by up to ~150 Ω. The offset is constant within the reading.
    means = []
    for seed in range(300):
        p, out = _flat(params, n=50, seed=seed, repeat_dispersion_ohm=60.0)
        r = adc_to_resistance(out.adc_counts, p)
        assert np.ptp(r) < 30.0  # constant within the session (quantisation only)
        means.append(r.mean())
    assert np.std(means) == pytest.approx(60.0, rel=0.2)
    assert abs(np.mean(means) - 6000.0) < 25.0
    triplets = np.array(means).reshape(100, 3)
    assert np.median(np.ptp(triplets, axis=1)) < 150.0


def test_lot_gain_is_per_astronaut_positive_and_zero_spread_is_identity(params):
    gains = np.array([lot_gain(np.random.default_rng(s), params) for s in range(2000)])
    assert gains.mean() == pytest.approx(1.0, abs=0.01)
    assert gains.std() == pytest.approx(params.lot_variation_fraction, rel=0.1)
    assert gains.min() > 0.0
    assert lot_gain(np.random.default_rng(0), params.model_copy(update={"lot_variation_fraction": 0.0})) == 1.0
    assert lot_gain(np.random.default_rng(3), params) == lot_gain(np.random.default_rng(3), params)


def test_lot_gain_scales_resistance_and_the_published_curve(params, config):
    p = _quiet(params)
    length = resistance_to_length(np.array([6000.0]), p.curve_pairs())[0]
    scaled = synth_stretch(
        np.random.default_rng(1), p, length_mm=np.full(100, length), rest_length_mm=length,
        rate_hz=p.sample_rate_hz, initial_residual_mm=0.0, lot_gain=1.1,
    )  # fmt: skip
    assert adc_to_resistance(scaled.adc_counts, p).mean() == pytest.approx(6600.0, rel=0.01)
    cal = config.calibration.stretch.model_copy(update={"measurement_noise_mm": 0.0})
    plain = measured_curve(np.random.default_rng(0), p, cal, rest_length_mm=REST_MM)
    gained = measured_curve(np.random.default_rng(0), p, cal, rest_length_mm=REST_MM, lot_gain=1.1)
    assert [g[0] for g in gained] == pytest.approx([1.1 * b[0] for b in plain])
    assert [g[1] for g in gained] == pytest.approx([b[1] for b in plain])


def test_creep_grows_under_sustained_stretch_and_recovers_slowly(params):
    p = _quiet(params, creep_fraction=0.02)
    rate = p.sample_rate_hz
    tau = p.creep_tau_s
    held = int(6 * tau * rate)
    length = np.full(int(14 * tau * rate), REST_MM)
    length[:held] = REST_MM + 40.0
    out = synth_stretch(
        np.random.default_rng(0), p, length_mm=length, rest_length_mm=REST_MM, rate_hz=rate, initial_residual_mm=0.0
    )  # fmt: skip
    extra = out.effective_length_mm - length
    assert extra[0] < 0.1 * 0.02 * 40.0  # starts with no creep
    assert extra[held - 1] == pytest.approx(0.02 * 40.0, rel=0.01)  # satura en creep_fraction x estiramiento
    assert np.all(np.diff(extra[:held]) >= -1e-12)  # crece
    k = held - 1 + int(tau * rate)
    assert extra[k] == pytest.approx(extra[held - 1] * np.exp(-1), rel=0.01)  # recovers with creep_tau_s
    assert not out.residual_mm.any()  # the hysteresis residual keeps its meaning
    assert out.creep_mm == pytest.approx(extra)


def test_new_noise_sources_use_their_own_streams(params):
    length = np.full(200, REST_MM + 5.0)

    def run(p: StretchSignalConfig):
        return synth_stretch(np.random.default_rng(9), p, length_mm=length, rest_length_mm=REST_MM, rate_hz=20.0, initial_residual_mm=0.0)

    a = run(params)
    b = run(params)
    assert np.array_equal(a.adc_counts, b.adc_counts)
    off = params.model_copy(update={"read_noise_ohm": 0.0, "repeat_dispersion_ohm": 0.0})
    base = run(off)
    assert not np.array_equal(a.adc_counts, base.adc_counts)
    # The ADC noise comes from the main branch: with the new branches off, it uses the same stream as
    # resistance_to_adc directly with the same seed (the child branches do not shift it).
    resistance = length_to_resistance(base.effective_length_mm, off.curve_pairs())
    assert np.array_equal(base.adc_counts, resistance_to_adc(np.random.default_rng(9), resistance, off))
