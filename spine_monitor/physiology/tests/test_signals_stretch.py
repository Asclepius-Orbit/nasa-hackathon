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
    measured_curve,
    resistance_to_adc,
    resistance_to_length,
    synth_stretch,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
REST_MM = 400.0  # valor de prueba, independiente del YAML


@pytest.fixture(scope="module")
def config():
    return load_config(EXAMPLE)


@pytest.fixture(scope="module")
def params(config) -> StretchSignalConfig:
    return config.signals.stretch


@pytest.fixture(scope="module")
def ideal(params) -> StretchSignalConfig:
    """Sin ruido de ADC ni histéresis."""
    return params.model_copy(update={"adc_noise_counts": 0.0, "hysteresis_gain": 0.0})


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
    lengths = np.linspace(curve[0][1] - 20.0, curve[-1][1] + 20.0, 500)  # incluye extrapolación
    resistance = length_to_resistance(lengths, curve)
    assert np.all(np.diff(resistance) > 0)
    assert resistance_to_length(resistance, curve) == pytest.approx(lengths)
    # En los nodos reproduce la curva exacta.
    r_nodes = np.array([p[0] for p in curve])
    l_nodes = np.array([p[1] for p in curve])
    assert resistance_to_length(r_nodes, curve) == pytest.approx(l_nodes)
    assert length_to_resistance(l_nodes, curve) == pytest.approx(r_nodes)
    # Extrapolación con la pendiente del tramo extremo.
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
    # El error máximo es la longitud que abarcan +-0,5 cuenta en cada punto (con margen de redondeo).
    r_hi = adc_to_resistance(adc + 0.5, ideal)
    r_lo = adc_to_resistance(adc - 0.5, ideal)
    tol = (resistance_to_length(r_hi, curve) - resistance_to_length(r_lo, curve)) / 2 + 1e-9
    assert np.all(np.abs(recovered - lengths) <= tol)
    assert np.abs(recovered - lengths).max() < 1.0  # mm: la cuantización del ADC de 12 bits es sub-milimétrica


def test_adc_resistance_roundtrip(ideal):
    r = np.array([5400.0, 6012.0, 7560.0])
    adc = resistance_to_adc(np.random.default_rng(0), r, ideal)
    back = adc_to_resistance(adc, ideal)
    d_r = adc_to_resistance(adc + 0.5, ideal) - adc_to_resistance(adc - 0.5, ideal)
    assert np.all(np.abs(back - r) <= d_r)


def test_adc_clips_and_stays_finite(ideal):
    full_scale = 2**ideal.circuit.adc_bits - 1
    huge = resistance_to_adc(np.random.default_rng(0), np.array([1e12, 1e-3]), ideal)
    assert huge.tolist() == [full_scale, 0]
    r = adc_to_resistance(np.array([0, full_scale]), ideal)
    assert np.all(np.isfinite(r)) and np.all(r > 0)


def test_adc_noise(params):
    r = np.full(20000, 6000.0)
    adc = resistance_to_adc(np.random.default_rng(0), r, params)
    assert adc.std() == pytest.approx(params.adc_noise_counts, rel=0.1)  # la cuantización añade poco
    quiet = resistance_to_adc(np.random.default_rng(0), r, params.model_copy(update={"adc_noise_counts": 0.0}))
    assert np.ptp(quiet) == 0


def test_hysteresis_recovery_time_constant(params):
    rate = params.sample_rate_hz
    tau = params.hysteresis_tau_s
    n = int(8 * tau * rate)
    length = np.full(n, REST_MM)
    stretched = int(5 * rate)
    length[:stretched] = REST_MM + 30.0  # estiramiento de 5 s y vuelta al reposo
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
    assert np.all(np.diff(residual[stretched - 1 :]) <= 1e-12)  # solo decae


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
    """Sesiones largas (varios bloques) coinciden con la recursión muestra a muestra."""
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
    assert out.effective_length_mm == pytest.approx(length)  # sin histéresis
    assert not out.residual_mm.any()
    _, recovered = derive(out.adc_counts, ideal, curve)
    assert np.abs(recovered - length).max() < 1.0
    # Con histéresis, la longitud derivada queda por encima de la real (residuo >= 0).
    hyst = synth_stretch(
        np.random.default_rng(0), params.model_copy(update={"adc_noise_counts": 0.0}), length_mm=length,
        rest_length_mm=REST_MM, rate_hz=rate, initial_residual_mm=0.0,
    )  # fmt: skip
    assert hyst.residual_mm.max() > 0
    _, derived = derive(hyst.adc_counts, params, curve)
    assert np.abs(derived - hyst.effective_length_mm).max() < 1.0


def test_derive_uses_calibration_curve(ideal):
    """Una calibración distinta de la real desplaza la longitud derivada, no la resistencia."""
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
    # Error de medida acotado respecto a la curva real.
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
    # Puntos del reposo por encima de la curva real siguen dando una curva válida.
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
