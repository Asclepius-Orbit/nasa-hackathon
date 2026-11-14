"""Estadística reproducible de las tres señales (#131): bandas de amplitud, espectro, ruido, histéresis y rangos.

Complementa a ``test_signals_{emg,imu,stretch}.py`` (que ya fijan cada mecanismo por separado) con comprobaciones
estadísticas con semilla fija y con propiedades Hypothesis sobre cualquier configuración válida. Cada tolerancia dice
de dónde sale: la hoja de especificaciones (spec), la config. del firmware/gateway (config), una medición propia o una
hipótesis (hip.) de ``docs/sensor-sources.md``. Ninguna cifra de EMG está validada con mediciones (el módulo comprado es
defectuoso): los umbrales de EMG describen el modelo del simulador, no el hardware.
"""

from pathlib import Path

import numpy as np
import pytest
import spine_physiology
from hypothesis import given
from hypothesis import strategies as st
from scipy import signal as sp_signal
from spine_contracts import AnatomicalPlane
from spine_physiology.config import EmgSignalConfig, ImuSignalConfig, StretchSignalConfig, load_config
from spine_physiology.signals import n_samples, rep_schedule
from spine_physiology.signals.emg import _quantize, activation_profile, fatigue_profile, rms, synth_emg
from spine_physiology.signals.imu import angle_profile, synth_imu
from spine_physiology.signals.stretch import apply_hysteresis, length_to_resistance, resistance_to_length, synth_stretch

# vía el paquete instalado, no vía parents[]: bajo mutmut los tests se copian a otro árbol sin carpetas hermanas
_EXAMPLE = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
MVC_RMS_MV = 1.8  # valor de prueba (orden de la MVC del ejemplo); hip.: sin medición real del módulo


@pytest.fixture(scope="module")
def config():
    return load_config(_EXAMPLE)


@pytest.fixture(scope="module")
def protocol(config):
    return config.protocol("lat_pulldown_standing_v1")


@pytest.fixture(scope="module")
def emg_params(config) -> EmgSignalConfig:
    return config.signals.emg


@pytest.fixture(scope="module")
def imu_params(config) -> ImuSignalConfig:
    return config.signals.imu


@pytest.fixture(scope="module")
def stretch_params(config) -> StretchSignalConfig:
    return config.signals.stretch


def _emg_session(params: EmgSignalConfig, protocol, seed: int, *, fatigue: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple]:
    """(señal, %MVC, fatiga, reps) de una serie del protocolo (la primera) a la frecuencia de la config."""
    rate = params.sample_rate_hz
    reps = tuple(r for r in rep_schedule(protocol) if r.set_index == 1)
    n = n_samples(reps[-1].end_s + 5.0, rate, int(rate))
    pct = activation_profile(reps, peak_pct_mvc=protocol.pct_mvc, rest_pct_mvc=params.rest_pct_mvc, n_samples=n, rate_hz=rate)
    fat = fatigue_profile(reps, fatigue_per_rep=params.fatigue_per_rep, n_samples=n, rate_hz=rate) if fatigue else np.zeros(n)
    x = synth_emg(np.random.default_rng(seed), params, mvc_rms_mv=MVC_RMS_MV, pct_mvc=pct, fatigue=fat, rate_hz=rate)
    return x, pct, fat, reps


# --- EMG ------------------------------------------------------------------------------------------


@pytest.mark.unit
def test_rest_and_exercise_rms_follow_the_protocol_pct_mvc_and_their_ratio_is_large(emg_params, protocol):
    # Hipótesis del modelo (sin EMG real): RMS = sqrt((MVC x %MVC/100)^2 + ruido^2). En los tramos de reposo (%MVC
    # constante = rest) y durante las reps (campana del perfil) se compara con esa expresión. Tolerancia 8 %: las
    # muestras están correlacionadas por la banda (~430 Hz x 3 s ~ 1300 grados de libertad, sd relativa ~2 %), 4 sigma.
    x, pct, _, reps = _emg_session(emg_params, protocol, seed=101)
    rate = emg_params.sample_rate_hz
    env = MVC_RMS_MV * pct / 100.0
    expected = np.sqrt(env**2 + emg_params.noise_rms_mv**2)
    active = np.zeros(x.size, dtype=bool)
    for r in reps:
        active[int(r.start_s * rate) : int(r.end_s * rate)] = True
    rest = ~active
    rest[: int(0.2 * rate)] = False  # fuera del transitorio de borde del filtro
    rest[int(reps[0].start_s * rate) - int(0.5 * rate) :] &= False
    assert rest.sum() > 5000 and active.sum() > 20000
    assert rms(x[rest]) == pytest.approx(float(np.sqrt(np.mean(expected[rest] ** 2))), rel=0.08)
    assert rms(x[active]) == pytest.approx(float(np.sqrt(np.mean(expected[active] ** 2))), rel=0.08)
    # Con rest_pct = 2 % y pico 45 % el esfuerzo se distingue del reposo con holgura (>10x en RMS).
    assert rms(x[active]) / rms(x[rest]) > 10.0


@pytest.mark.unit
def test_the_raw_signal_is_gaussian_modulated_by_the_envelope(emg_params, protocol):
    # Modelo: x = envolvente x ruido banda de potencia unidad + ruido blanco. Estandarizada por la sd esperada, la serie
    # tiene sd ~1 (tolerancia 5 %, 4 sigma de la sd muestral con ~1300 g.l. por segundo y 20+ s) y casi ninguna muestra
    # a más de 4 sigma (gaussiana: 6e-5; umbral 1e-3, margen por la correlación de las colas).
    x, pct, _, _ = _emg_session(emg_params, protocol, seed=102, fatigue=False)
    sigma = np.sqrt((MVC_RMS_MV * pct / 100.0) ** 2 + emg_params.noise_rms_mv**2)
    edge = int(0.5 * emg_params.sample_rate_hz)
    z = (x / sigma)[edge:-edge]
    assert z.std() == pytest.approx(1.0, rel=0.05)
    assert np.mean(np.abs(z) > 4.0) < 1e-3


@pytest.mark.unit
def test_the_power_sits_in_the_emg_band_with_the_configured_noise_floor(emg_params, protocol):
    # Literatura: banda 20-450 Hz (config del modelo). Welch de 1 Hz con ruido de sensor: >= 97 % de la potencia en
    # banda (el ruido blanco de 0,01 mV reparte < 1 % fuera) y, bajo la banda (4-6 Hz), la PSD cae hasta el suelo del
    # ruido blanco de sensor: sigma^2 / (fs/2) = 2e-7 mV^2/Hz (el Butterworth de orden 4 ida y vuelta atenúa la señal
    # > 90 dB ahí). Tolerancia 25 % (3 bins de 1 Hz, cada uno con ~10 promedios de Welch: sd relativa ~15 %).
    x, _, _, _ = _emg_session(emg_params, protocol, seed=103, fatigue=False)
    rate = emg_params.sample_rate_hz
    freqs, psd = sp_signal.welch(x, fs=rate, nperseg=int(rate))
    in_band = (freqs >= emg_params.band_low_hz) & (freqs <= emg_params.band_high_hz)
    assert psd[in_band].sum() / psd.sum() > 0.97
    noise_floor = emg_params.noise_rms_mv**2 / (rate / 2.0)
    assert psd[(freqs >= 4) & (freqs <= 6)].mean() == pytest.approx(noise_floor, rel=0.25)


@pytest.mark.unit
def test_quantization_error_is_uniform_with_the_adc_lsb(emg_params):
    # config: ADC de 12 bits, 3,3 V, ganancia 1000 (ejemplo). Con señal >> LSB el error de cuantización es uniforme:
    # sd = LSB/sqrt(12). Misma rng con y sin cuantizar => la diferencia es solo la cuantización. Se usa 10 %MVC (RMS
    # 0,18 mV, picos < 1 mV frente a +-1,65 mV de rango) para que no sature. Tolerancia 3 % (n = 5e4, sd relativa del
    # estimador ~0,3 %).
    rate = emg_params.sample_rate_hz
    pct = np.full(int(50 * rate), 10.0)
    fat = np.zeros_like(pct)
    base = emg_params.model_copy(update={"signal_kind": "raw", "quantize_adc": False})
    on = base.model_copy(update={"quantize_adc": True})
    raw = synth_emg(np.random.default_rng(104), base, mvc_rms_mv=MVC_RMS_MV, pct_mvc=pct, fatigue=fat, rate_hz=rate)
    quant = synth_emg(np.random.default_rng(104), on, mvc_rms_mv=MVC_RMS_MV, pct_mvc=pct, fatigue=fat, rate_hz=rate)
    acq = emg_params.acquisition
    lsb = acq.vref_v * 1000.0 / acq.gain / 2**acq.adc_bits
    assert (quant - raw).std() == pytest.approx(lsb / np.sqrt(12.0), rel=0.03)
    assert abs((quant - raw).mean()) < 0.02 * lsb
    counts = quant / lsb
    assert np.allclose(counts, np.round(counts), atol=1e-6)
    assert counts.min() >= -(2 ** (acq.adc_bits - 1)) and counts.max() <= 2 ** (acq.adc_bits - 1) - 1


@pytest.mark.unit
def test_mains_peak_stands_out_of_the_emg_spectrum_at_the_configured_frequency(emg_params, protocol):
    # hip.: amplitud de red sin cifra verificada; con 0,3 mV de pico la red aporta 0,045 mV^2 en ~1,5 bins de 1 Hz frente a
    # ~7e-4 mV^2/Hz del EMG de 30 %MVC (0,54 mV rms en 430 Hz): cociente teórico ~45, umbral 10 (10 dB). Con la red apagada
    # el máximo de 21 bins sobre su mediana queda por debajo (observado 1,2; con la red 42).
    rate = emg_params.sample_rate_hz
    pct = np.full(int(20 * rate), 30.0)
    fat = np.zeros_like(pct)

    def ratio(update: dict) -> tuple[float, float]:
        cfg = emg_params.model_copy(update={"signal_kind": "raw", **update})
        x = synth_emg(np.random.default_rng(105), cfg, mvc_rms_mv=MVC_RMS_MV, pct_mvc=pct, fatigue=fat, rate_hz=rate)
        freqs, psd = sp_signal.welch(x, fs=rate, nperseg=int(rate))
        near = (freqs >= 40) & (freqs <= 60) & (np.abs(freqs - cfg.mains_hz) > 2.0)
        peak = np.argmax(psd[(freqs >= 40) & (freqs <= 60)]) + np.searchsorted(freqs, 40)
        return float(psd[peak] / np.median(psd[near])), float(freqs[peak])

    on_ratio, on_freq = ratio({"mains_amplitude_mv": 0.3})
    off_ratio, _ = ratio({"mains_amplitude_mv": 0.0})
    assert on_freq == pytest.approx(50.0, abs=1.0)
    assert on_ratio > 10.0
    assert off_ratio < 10.0


# --- IMU ------------------------------------------------------------------------------------------


def _imu_session(params: ImuSignalConfig, protocol, seed: int, gravity_g: float = 1.0) -> tuple[dict[str, np.ndarray], np.ndarray, float]:
    rate = params.sample_rate_hz
    reps = tuple(r for r in rep_schedule(protocol) if r.set_index == 1)
    n = n_samples(reps[-1].end_s + 2.0, rate, int(rate))
    angle = angle_profile(reps, rom_deg=protocol.rom_deg, n_samples=n, rate_hz=rate)
    out = synth_imu(np.random.default_rng(seed), params, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL, gravity_g=gravity_g, rate_hz=rate, tremor_dps=0.0)
    return out, angle, rate


@pytest.mark.unit
def test_gyro_and_accel_correlate_with_the_flexion_angle_of_the_session(imu_params, protocol):
    # Con el montaje del ejemplo (sensor_y = izquierda) el giro sagital sale por gyro_y = d(ángulo)/dt, y la gravedad
    # inclinada reparte accel entre sensor_x (craneal, ~cos) y sensor_z (posterior, ~sin). Con ruido, sesgo, DLPF y
    # cuantización del ejemplo: |r| > 0,95 (la señal de 37 dps pico frente a ruido de 0,03 dps; el paso bajo de 44 Hz añade
    # un retardo de ~4 ms que baja r a ~0,99) y un r cercano a 0 con la lectura de otro eje en reposo (gyro_x, sin movimiento).
    out, angle, rate = _imu_session(imu_params, protocol, seed=201)
    omega = np.gradient(angle, 1.0 / rate)
    assert np.corrcoef(out["gyro_y"], omega)[0, 1] > 0.95
    assert abs(np.corrcoef(out["accel_z"], np.sin(np.deg2rad(angle)))[0, 1]) > 0.95
    assert np.corrcoef(out["accel_x"], np.cos(np.deg2rad(angle)))[0, 1] > 0.95
    assert abs(np.corrcoef(out["gyro_x"], omega)[0, 1]) < 0.15


@pytest.mark.unit
def test_gravity_is_one_g_on_earth_and_zero_g_in_microgravity_with_the_example_noise_and_bias(imu_params, protocol):
    # spec: offsets de cero hasta 0,05/0,05/0,08 g, el ejemplo usa 0,01/-0,005/0,002 g (norma 0,011 g) y ruido 400 ug/rtHz
    # x sqrt(44 Hz) = 2,7 mg. En reposo |a| = 1 g y en microgravedad ~ el sesgo: tolerancia 0,02 g (cubre el sesgo del ejemplo
    # y la deriva OU, hip., de sigma 0,002 g) frente al máximo spec de 0,107 g (que no se usa como tolerancia).
    for gravity, expected in ((1.0, 1.0), (0.0, 0.0)):
        rate = imu_params.sample_rate_hz
        out = synth_imu(
            np.random.default_rng(202), imu_params, angle_deg=np.zeros(int(60 * rate)), plane=AnatomicalPlane.SAGITTAL,
            gravity_g=gravity, rate_hz=rate, tremor_dps=0.0,
        )  # fmt: skip
        magnitude = np.sqrt(out["accel_x"] ** 2 + out["accel_y"] ** 2 + out["accel_z"] ** 2)
        assert magnitude.mean() == pytest.approx(expected, abs=0.02)


@pytest.mark.unit
def test_the_example_noise_std_is_the_datasheet_density_times_sqrt_of_the_dlpf_bandwidth(imu_params):
    # spec: densidad 400 ug/rtHz (accel) y 0,005 dps/rtHz (gyro, marcada (*)); el ancho de banda efectivo es el DLPF de
    # 44 Hz (Nyquist = 50 Hz). Se mide con la config del ejemplo (cuantización y sesgo incluidos): la cuantización suma
    # LSB^2/12 (8192 LSB/g, 65,5 LSB/dps: despreciable), y el sesgo y la deriva son lentos, así que se resta la media
    # móvil. Tolerancia 6 % (n = 4e4, se ~0,4 %; el resto es el LSB y la deriva).
    rate = imu_params.sample_rate_hz
    out = synth_imu(
        np.random.default_rng(203), imu_params.model_copy(update={"gyro_bias_drift_sigma_dps": 0.0, "accel_bias_drift_sigma_g": 0.0}),
        angle_deg=np.zeros(int(400 * rate)), plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=rate, tremor_dps=0.0,
    )  # fmt: skip
    bandwidth = min(imu_params.dlpf_hz, rate / 2.0)
    assert out["accel_y"].std() == pytest.approx(imu_params.accel_noise_density_ug_rthz * 1e-6 * np.sqrt(bandwidth), rel=0.06)
    assert out["gyro_x"].std() == pytest.approx(imu_params.gyro_noise_density_dps_rthz * np.sqrt(bandwidth), rel=0.06)


_BIAS = st.floats(min_value=-0.2, max_value=0.2, allow_nan=False)


@pytest.mark.property
@given(
    static=st.tuples(_BIAS, _BIAS, _BIAS),
    sigma=st.floats(min_value=0.0, max_value=0.5, allow_nan=False),
    gyro_static=st.floats(min_value=-40.0, max_value=40.0, allow_nan=False),
    gyro_sigma=st.floats(min_value=0.0, max_value=30.0, allow_nan=False),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_the_sensor_bias_never_exceeds_the_datasheet_maxima_whatever_the_configured_offset_and_drift(imu_params, static, sigma, gyro_static, gyro_sigma, seed):
    # spec (máximos, no típicos): accel 0,05/0,05/0,08 g y gyro 20 dps. En microgravedad y reposo, sin ruido, la lectura es
    # el sesgo: el recorte de la hoja se cumple para cualquier offset y deriva configurados (más un LSB de cuantización).
    params = imu_params.model_copy(
        update={
            "accel_noise_density_ug_rthz": 0.0, "gyro_noise_density_dps_rthz": 0.0, "dlpf_hz": 1e6,
            "accel_bias_g": imu_params.accel_bias_g.model_copy(update=dict(zip("xyz", static, strict=True))),
            "gyro_bias_dps": imu_params.gyro_bias_dps.model_copy(update={"x": gyro_static, "y": -gyro_static, "z": 0.0}),
            "accel_bias_drift_sigma_g": sigma, "gyro_bias_drift_sigma_dps": gyro_sigma, "bias_drift_tau_s": 5.0,
        }
    )  # fmt: skip
    out = synth_imu(np.random.default_rng(seed), params, angle_deg=np.zeros(300), plane=AnatomicalPlane.SAGITTAL, gravity_g=0.0, rate_hz=100.0, tremor_dps=0.0)
    am, gm = params.accel_bias_max_g, params.gyro_bias_max_dps
    accel_lsb, gyro_lsb = 1.0 / 8192.0, 1.0 / 65.5
    for axis, limit in (("x", am.x), ("y", am.y), ("z", am.z)):
        assert np.abs(out[f"accel_{axis}"]).max() <= limit + accel_lsb
    for axis in "xyz":
        assert np.abs(out[f"gyro_{axis}"]).max() <= gm + gyro_lsb


@pytest.mark.property
@given(
    accel_range=st.sampled_from([2, 4, 8, 16]),
    gyro_range=st.sampled_from([250, 500, 1000, 2000]),
    rom=st.floats(min_value=0.0, max_value=170.0, allow_nan=False),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_imu_output_is_int16_counts_inside_the_configured_ranges_for_any_range_and_motion(imu_params, accel_range, gyro_range, rom, seed):
    # spec: palabra de 16 bits en complemento a dos; sensibilidad de la hoja (16384/2^k LSB/g, 131/2^k LSB/dps). Para
    # cualquier rango y recorrido la salida son cuentas enteras en [-32768, 32767] y |valor| <= rango (recorte).
    params = imu_params.model_copy(update={"accel_range_g": accel_range, "gyro_range_dps": gyro_range, "quantize_output": True})
    rate = 100.0
    angle = np.concatenate([np.zeros(20), np.linspace(0.0, rom, 30), np.full(20, rom), np.linspace(rom, 0.0, 10)])
    out = synth_imu(np.random.default_rng(seed), params, angle_deg=angle, plane=AnatomicalPlane.SAGITTAL, gravity_g=1.0, rate_hz=rate, tremor_dps=5.0)
    accel_lsb = 16384.0 * 2.0 / accel_range
    gyro_lsb = {250: 131.0, 500: 65.5, 1000: 32.8, 2000: 16.4}[gyro_range]
    for name, values in out.items():
        lsb = accel_lsb if name.startswith("accel") else gyro_lsb
        limit = float(accel_range if name.startswith("accel") else gyro_range)
        counts = values * lsb
        assert np.allclose(counts, np.round(counts), atol=1e-6)
        assert counts.min() >= -32768 and counts.max() <= 32767
        assert np.abs(values).max() <= limit + 1e-9


# --- Cordón ---------------------------------------------------------------------------------------


def _quiet_stretch(params: StretchSignalConfig, **update) -> StretchSignalConfig:
    zero = {
        "adc_noise_counts": 0.0,
        "read_noise_ohm": 0.0,
        "repeat_dispersion_ohm": 0.0,
        "creep_fraction": 0.0,
        "lot_variation_fraction": 0.0,
        "hysteresis_gain": 0.0,
    }
    return params.model_copy(update={**zero, **update})


@pytest.mark.unit
def test_the_count_noise_spread_matches_adc_noise_read_noise_and_quantization(stretch_params):
    # Config: divisor 10 kOhm / ADC 10 bits. A una R fija las cuentas tienen sd = sqrt(adc_noise^2 + (read_noise x dN/dR)^2
    # + 1/12) con dN/dR = full x Rs/(R+Rs)^2 (derivada del divisor). adc_noise = 1 cuenta (config del ejemplo) y read_noise
    # = 60 ohm (hip.: el ejemplo usa 5 ohm, sin fuente; se sube para que pese frente al ruido de ADC).
    # Tolerancia 5 % (n = 2e4, se ~0,5 %; el redondeo no es exactamente uniforme).
    p = _quiet_stretch(stretch_params, adc_noise_counts=1.0, read_noise_ohm=60.0)
    circuit = p.circuit
    full = 2**circuit.adc_bits - 1
    resistance = 6000.0
    length = resistance_to_length(np.array([resistance]), p.curve_pairs())[0]
    out = synth_stretch(
        np.random.default_rng(301), p, length_mm=np.full(20000, length), rest_length_mm=length, rate_hz=p.sample_rate_hz, initial_residual_mm=0.0
    )
    slope = full * circuit.series_resistor_ohm / (resistance + circuit.series_resistor_ohm) ** 2
    expected = np.sqrt(p.adc_noise_counts**2 + (p.read_noise_ohm * slope) ** 2 + 1.0 / 12.0)
    assert out.adc_counts.std() == pytest.approx(expected, rel=0.05)
    assert out.adc_counts.mean() == pytest.approx(full * resistance / (resistance + circuit.series_resistor_ohm), abs=0.1)


@pytest.mark.unit
def test_the_hysteresis_loop_has_different_up_and_down_paths(stretch_params):
    # hip. (hysteresis_gain 0,15, tau 90 s, sin cifra del fabricante; la hoja solo dice "uno o dos minutos"). Una rampa
    # de subida y bajada de 60 s cada una: a la misma longitud real la efectiva de la bajada supera a la de subida.
    # Cota analítica en el punto medio: subida -> residuo gain x (mitad del estiramiento); bajada -> residuo >= gain x
    # estiramiento total x e^{-30/90}; diferencia >= gain x estiramiento x (e^{-1/3} - 1/2) = 1,95 mm. Se exige el 90 %
    # (muestreo discreto de la rampa: observado 97 %).
    p = _quiet_stretch(stretch_params, hysteresis_gain=0.15)
    rate = p.sample_rate_hz
    rest, stretch = 650.0, 60.0
    ramp = int(60 * rate)
    length = np.concatenate([np.linspace(rest, rest + stretch, ramp), np.linspace(rest + stretch, rest, ramp)])
    out = synth_stretch(np.random.default_rng(302), p, length_mm=length, rest_length_mm=rest, rate_hz=rate, initial_residual_mm=0.0)
    up_mid, down_mid = ramp // 2, ramp + ramp // 2
    assert length[up_mid] == pytest.approx(length[down_mid], abs=0.1)
    gap_mm = out.effective_length_mm[down_mid] - out.effective_length_mm[up_mid]
    assert gap_mm >= 0.9 * 0.15 * stretch * (np.exp(-30.0 / p.hysteresis_tau_s) - 0.5)
    # lo mismo en cuentas de ADC: la bajada lee más resistencia (más cuentas) que la subida a la misma longitud.
    assert out.adc_counts[down_mid - 5 : down_mid + 5].mean() > out.adc_counts[up_mid - 5 : up_mid + 5].mean()
    # sin histéresis no hay lazo.
    flat = synth_stretch(
        np.random.default_rng(302), _quiet_stretch(stretch_params), length_mm=length, rest_length_mm=rest, rate_hz=rate, initial_residual_mm=0.0
    )
    assert flat.effective_length_mm[down_mid] == pytest.approx(flat.effective_length_mm[up_mid], abs=0.1)


@pytest.mark.unit
def test_stretch_counts_span_a_physical_range_when_the_cord_goes_from_rest_to_full_flexion(stretch_params):
    # Medición propia (espalda): 4500 -> 5650 ohm con 50 cm de contactos (no enlazable con esta curva); aquí la curva del
    # ejemplo 5400..7560 ohm da 350..430 cuentas con el divisor de 10 kOhm: el rango útil es una fracción pequeña de 0..1023
    # (hip. de montaje, ver docs). Se comprueba que el paso de reposo a máxima flexión sube las cuentas y queda dentro de 1..1022.
    p = _quiet_stretch(stretch_params)
    lengths = np.array([p.curve[0].length_mm, p.curve[-1].length_mm])
    out = synth_stretch(np.random.default_rng(303), p, length_mm=lengths, rest_length_mm=float(lengths[0]), rate_hz=p.sample_rate_hz, initial_residual_mm=0.0)
    assert 1 <= out.adc_counts[0] < out.adc_counts[1] <= 1022
    assert out.adc_counts[1] - out.adc_counts[0] > 40


@pytest.mark.property
@given(
    lengths=st.lists(st.floats(min_value=500.0, max_value=900.0, allow_nan=False), min_size=2, max_size=60),
    series=st.floats(min_value=1_000.0, max_value=100_000.0, allow_nan=False),
    gain=st.floats(min_value=0.5, max_value=2.0, allow_nan=False),
)
def test_without_noise_counts_never_decrease_when_the_cord_is_longer(stretch_params, lengths, series, gain):
    # La cadena longitud -> resistencia -> divisor -> ADC es monótona no decreciente (curva estrictamente creciente,
    # ganancia de lote positiva, divisor creciente en R): invariante estructural, no una tolerancia.
    circuit = stretch_params.circuit.model_copy(update={"series_resistor_ohm": series})
    p = _quiet_stretch(stretch_params, circuit=circuit)
    ordered = np.sort(np.array(lengths))
    out = synth_stretch(
        np.random.default_rng(0), p, length_mm=ordered, rest_length_mm=float(ordered[0]), rate_hz=p.sample_rate_hz, initial_residual_mm=0.0, lot_gain=gain
    )
    assert np.all(np.diff(out.adc_counts) >= 0)
    resistance = length_to_resistance(ordered, p.curve_pairs())
    assert np.all(np.diff(resistance) >= 0)


@pytest.mark.property
@given(
    adc_bits=st.integers(min_value=8, max_value=16),
    series=st.floats(min_value=100.0, max_value=1e6, allow_nan=False),
    noise=st.floats(min_value=0.0, max_value=500.0, allow_nan=False),
    gain=st.floats(min_value=0.1, max_value=3.0, allow_nan=False),
    length=st.floats(min_value=1.0, max_value=3000.0, allow_nan=False),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_stretch_counts_stay_between_one_and_the_last_code_below_full_scale(stretch_params, adc_bits, series, noise, gain, length, seed):
    # config: el simulador nunca da los topes 0 ni 2^bits - 1 (con el divisor conectado el ADC real no los da): 1..2^b - 2
    # (1..1022 con 10 bits) para cualquier circuito, ruido, ganancia de lote y longitud, incluso fuera de la curva.
    circuit = stretch_params.circuit.model_copy(update={"adc_bits": adc_bits, "series_resistor_ohm": series})
    p = stretch_params.model_copy(update={"circuit": circuit, "adc_noise_counts": noise})
    out = synth_stretch(
        np.random.default_rng(seed), p, length_mm=np.full(50, length), rest_length_mm=length, rate_hz=p.sample_rate_hz, initial_residual_mm=0.0, lot_gain=gain
    )
    assert out.adc_counts.min() >= 1
    assert out.adc_counts.max() <= 2**adc_bits - 2


@pytest.mark.property
@given(
    initial=st.floats(min_value=0.0, max_value=50.0, allow_nan=False),
    gain=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    tau=st.floats(min_value=1.0, max_value=600.0, allow_nan=False),
    lengths=st.lists(st.floats(min_value=600.0, max_value=800.0, allow_nan=False), min_size=1, max_size=80),
)
def test_hysteresis_residual_is_never_negative_and_the_effective_length_never_below_the_real_one(stretch_params, initial, gain, tau, lengths):
    # Invariante del modelo (retención de pico con caída): residuo >= 0, efectiva = real + residuo >= real.
    p = stretch_params.model_copy(update={"hysteresis_gain": gain, "hysteresis_tau_s": tau})
    length = np.array(lengths)
    effective, residual = apply_hysteresis(length, rest_length_mm=650.0, rate_hz=p.sample_rate_hz, params=p, initial_residual_mm=initial)
    assert np.all(residual >= 0.0)
    assert np.all(effective >= length - 1e-9)


# --- EMG: propiedades sobre cualquier configuración válida -----------------------------------------


@pytest.mark.property
@given(
    bits=st.integers(min_value=8, max_value=16),
    vref=st.floats(min_value=0.5, max_value=5.0, allow_nan=False),
    gain=st.floats(min_value=0.5, max_value=3000.0, allow_nan=False),
    kind=st.sampled_from(["raw", "rectified", "envelope"]),
    values=st.lists(st.floats(min_value=-50.0, max_value=50.0, allow_nan=False), min_size=1, max_size=40),
)
def test_emg_quantization_is_idempotent_and_stays_inside_the_adc_range(emg_params, bits, vref, gain, kind, values):
    # config: la cuantización es una proyección a la rejilla del ADC (paso vref/gain/2^bits referido a la entrada,
    # bipolar para crudo y unipolar para rectificada y envolvente): aplicarla dos veces da lo mismo y el resultado cae
    # dentro del rango. Invariante estructural, sin tolerancia estadística (solo 1e-9 del redondeo en flotante).
    acq = emg_params.acquisition.model_copy(update={"adc_bits": bits, "vref_v": vref, "gain": gain})
    params = emg_params.model_copy(update={"acquisition": acq, "signal_kind": kind, "quantize_adc": True})
    x = np.array(values)
    once = _quantize(x, params)
    assert np.array_equal(_quantize(once, params), once)
    lsb = vref * 1000.0 / gain / 2**bits
    low, high = (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1) if kind == "raw" else (0, 2**bits - 1)
    assert once.min() >= low * lsb - 1e-9 and once.max() <= high * lsb + 1e-9
    assert np.abs(once / lsb - np.round(once / lsb)).max() < 1e-6


@pytest.mark.property
@given(
    kind=st.sampled_from(["raw", "rectified", "envelope"]),
    bits=st.integers(min_value=8, max_value=14),
    mvc=st.floats(min_value=0.1, max_value=5.0, allow_nan=False),
    pct=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    mains=st.floats(min_value=0.0, max_value=0.5, allow_nan=False),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_emg_output_is_finite_and_inside_the_adc_range_for_any_valid_configuration(emg_params, kind, bits, mvc, pct, mains, seed):
    # Para cualquier tipo de señal, resolución, MVC, %MVC y red, la salida cuantizada es finita y cabe en el ADC; la
    # rectificada y la envolvente no son negativas.
    acq = emg_params.acquisition.model_copy(update={"adc_bits": bits})
    params = emg_params.model_copy(update={"acquisition": acq, "signal_kind": kind, "quantize_adc": True, "mains_amplitude_mv": mains})
    out = synth_emg(np.random.default_rng(seed), params, mvc_rms_mv=mvc, pct_mvc=np.full(400, pct), fatigue=np.linspace(0.0, 1.0, 400), rate_hz=1000.0)
    lsb = acq.vref_v * 1000.0 / acq.gain / 2**bits
    assert np.isfinite(out).all()
    if kind == "raw":
        assert out.min() >= -(2 ** (bits - 1)) * lsb - 1e-9 and out.max() <= (2 ** (bits - 1) - 1) * lsb + 1e-9
    else:
        assert out.min() >= 0.0 and out.max() <= (2**bits - 1) * lsb + 1e-9
