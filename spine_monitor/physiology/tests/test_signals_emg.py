import time
from pathlib import Path

import numpy as np
import pytest
from scipy import signal as sp_signal
from spine_physiology.config import EmgSignalConfig, load_config
from spine_physiology.signals import Rep, n_samples, rep_schedule
from spine_physiology.signals.emg import (
    activation_profile,
    effective_fatigue_per_rep,
    effective_pct_mvc,
    fatigue_profile,
    median_frequency,
    rms,
    synth_emg,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
MVC_RMS_MV = 1.5  # valor de prueba, independiente del YAML


@pytest.fixture(scope="module")
def config():
    return load_config(EXAMPLE)


@pytest.fixture(scope="module")
def params(config) -> EmgSignalConfig:
    return config.signals.emg


@pytest.fixture(scope="module")
def rate(params) -> float:
    return params.sample_rate_hz


@pytest.fixture(scope="module")
def clean(params) -> EmgSignalConfig:
    """Misma config sin ruido de sensor, para aislar la banda y la amplitud de la señal."""
    return params.model_copy(update={"noise_rms_mv": 0.0})


def constant(value: float, n: int) -> np.ndarray:
    return np.full(n, value, dtype=np.float64)


# --- ganancias de desacondicionamiento --------------------------------------------------------------


def test_effective_values_follow_gains(params):
    assert effective_pct_mvc(40.0, 0.0, params) == 40.0
    assert effective_pct_mvc(40.0, 1.0, params) == pytest.approx(40.0 * (1 + params.deconditioning_pct_mvc_gain))
    assert effective_fatigue_per_rep(0.0, params) == params.fatigue_per_rep
    assert effective_fatigue_per_rep(0.5, params) == pytest.approx(
        params.fatigue_per_rep * (1 + 0.5 * params.deconditioning_fatigue_gain)
    )


def test_effective_values_monotonic_in_deconditioning(params):
    ds = np.linspace(0.0, 1.0, 5)
    pct = [effective_pct_mvc(30.0, d, params) for d in ds]
    fat = [effective_fatigue_per_rep(d, params) for d in ds]
    assert pct == sorted(pct)
    assert fat == sorted(fat)


# --- perfiles ---------------------------------------------------------------------------------------


REPS = (Rep(1, 2.0, 4.0), Rep(2, 6.0, 8.0), Rep(3, 10.0, 12.0))


def test_activation_profile_shape_and_levels(rate):
    n = int(14 * rate)
    prof = activation_profile(REPS, peak_pct_mvc=50.0, rest_pct_mvc=3.0, n_samples=n, rate_hz=rate)
    assert prof.shape == (n,) and prof.dtype == np.float64
    t = np.arange(n) / rate
    # Fuera de las reps: nivel de reposo exacto.
    outside = ~np.any([(t >= r.start_s) & (t < r.end_s) for r in REPS], axis=0)
    assert np.all(prof[outside] == 3.0)
    # Dentro: pico en el centro de la rep, continuo y sin superar el pico.
    assert prof[int(3.0 * rate)] == pytest.approx(50.0, abs=1e-6)
    assert prof.max() <= 50.0 + 1e-9
    assert prof.min() >= 3.0 - 1e-9
    # Rampas suaves: sin saltos entre muestras contiguas.
    assert np.max(np.abs(np.diff(prof))) < 0.05 * 50.0


def test_activation_profile_per_rep_peaks(rate):
    n = int(14 * rate)
    prof = activation_profile(REPS, peak_pct_mvc=[20.0, 40.0, 60.0], rest_pct_mvc=2.0, n_samples=n, rate_hz=rate)
    for rep, peak in zip(REPS, (20.0, 40.0, 60.0), strict=True):
        mid = int((rep.start_s + rep.end_s) / 2 * rate)
        assert prof[mid] == pytest.approx(peak, abs=1e-6)


def test_activation_profile_no_reps_is_rest(rate):
    prof = activation_profile((), peak_pct_mvc=50.0, rest_pct_mvc=2.5, n_samples=100, rate_hz=rate)
    assert np.all(prof == 2.5)


def test_activation_profile_wrong_peak_count_raises(rate):
    with pytest.raises(ValueError):
        activation_profile(REPS, peak_pct_mvc=[1.0, 2.0], rest_pct_mvc=0.0, n_samples=100, rate_hz=rate)


def test_activation_profile_clips_reps_beyond_series(rate):
    # La serie termina a mitad de la segunda rep: no debe fallar ni salirse.
    n = int(7 * rate)
    prof = activation_profile(REPS, peak_pct_mvc=50.0, rest_pct_mvc=2.0, n_samples=n, rate_hz=rate)
    assert prof.shape == (n,)


def test_fatigue_profile_accumulates_per_rep(rate):
    per_rep = 0.05
    n = int(14 * rate)
    fat = fatigue_profile(REPS, fatigue_per_rep=per_rep, n_samples=n, rate_hz=rate)
    assert fat.shape == (n,) and fat.dtype == np.float64
    assert fat[0] == 0.0
    assert np.all(np.diff(fat) >= -1e-12)  # no decrece
    # k reps hechas => k * per_rep (se mide justo al terminar y en los descansos).
    assert fat[int(5.0 * rate)] == pytest.approx(1 * per_rep)
    assert fat[int(9.0 * rate)] == pytest.approx(2 * per_rep)
    assert fat[-1] == pytest.approx(3 * per_rep)
    # Crece dentro de la rep.
    assert fat[int(2.5 * rate)] < fat[int(3.5 * rate)] < 1 * per_rep


def test_fatigue_profile_bounded_in_unit_interval(rate):
    fat = fatigue_profile(REPS, fatigue_per_rep=0.7, n_samples=int(14 * rate), rate_hz=rate)
    assert fat.min() >= 0.0 and fat.max() <= 1.0


def test_profiles_from_protocol_schedule(config, rate):
    protocol = config.protocol("lat_pulldown_standing_v1")
    reps = rep_schedule(protocol)
    n = n_samples(
        reps[-1].end_s + protocol.lead_out_s, rate, config.signals.samples_per_chunk(rate)
    )
    prof = activation_profile(reps, peak_pct_mvc=protocol.pct_mvc, rest_pct_mvc=2.0, n_samples=n, rate_hz=rate)
    assert prof.max() == pytest.approx(protocol.pct_mvc, rel=1e-3)


# --- síntesis ---------------------------------------------------------------------------------------


def test_shape_dtype(clean, rate):
    n = 5000
    x = synth_emg(
        np.random.default_rng(1), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=constant(30, n), fatigue=constant(0, n), rate_hz=rate
    )
    assert x.shape == (n,) and x.dtype == np.float64
    assert np.all(np.isfinite(x))


def test_empty_and_tiny_inputs(clean, rate):
    empty = synth_emg(
        np.random.default_rng(1), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=np.zeros(0), fatigue=np.zeros(0), rate_hz=rate
    )
    assert empty.shape == (0,)
    tiny = synth_emg(
        np.random.default_rng(1), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=constant(30, 5), fatigue=constant(0.1, 5), rate_hz=rate
    )
    assert tiny.shape == (5,) and np.all(np.isfinite(tiny))


def test_mismatched_lengths_raise(clean, rate):
    with pytest.raises(ValueError):
        synth_emg(
            np.random.default_rng(1), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=constant(30, 10), fatigue=constant(0, 9), rate_hz=rate
        )


def test_deterministic_same_seed(params, rate):
    pct = activation_profile(REPS, peak_pct_mvc=50.0, rest_pct_mvc=2.0, n_samples=int(14 * rate), rate_hz=rate)
    fat = fatigue_profile(REPS, fatigue_per_rep=0.1, n_samples=pct.size, rate_hz=rate)

    def run(seed):
        return synth_emg(np.random.default_rng(seed), params, mvc_rms_mv=MVC_RMS_MV, pct_mvc=pct, fatigue=fat, rate_hz=rate)

    a, b, c = run(7), run(7), run(8)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_does_not_use_global_random_state(clean, rate):
    pct, fat = constant(30, 2000), constant(0.2, 2000)
    np.random.seed(0)
    a = synth_emg(np.random.default_rng(3), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=pct, fatigue=fat, rate_hz=rate)
    np.random.seed(123)
    b = synth_emg(np.random.default_rng(3), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=pct, fatigue=fat, rate_hz=rate)
    assert np.array_equal(a, b)


@pytest.mark.parametrize("pct", [10.0, 25.0, 50.0, 100.0])
def test_rms_equals_mvc_times_pct(clean, rate, pct):
    n = int(30 * rate)
    x = synth_emg(
        np.random.default_rng(11), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=constant(pct, n), fatigue=constant(0, n), rate_hz=rate
    )
    assert rms(x) == pytest.approx(MVC_RMS_MV * pct / 100, rel=0.03)


def test_rms_proportional_to_pct(clean, rate):
    n = int(30 * rate)
    levels = [10.0, 20.0, 40.0, 80.0]
    values = [
        rms(synth_emg(np.random.default_rng(5), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=constant(p, n), fatigue=constant(0, n), rate_hz=rate))
        for p in levels
    ]
    ratios = np.array(values) / np.array(levels)
    assert ratios.max() / ratios.min() == pytest.approx(1.0, abs=0.04)


def test_rms_scales_with_mvc(clean, rate):
    n = int(20 * rate)
    kwargs = dict(pct_mvc=constant(40, n), fatigue=constant(0, n), rate_hz=rate)
    a = synth_emg(np.random.default_rng(2), clean, mvc_rms_mv=1.0, **kwargs)
    b = synth_emg(np.random.default_rng(2), clean, mvc_rms_mv=3.0, **kwargs)
    assert rms(b) == pytest.approx(3.0 * rms(a), rel=1e-9)


def test_rms_with_noise_floor_adds_in_quadrature(params, rate):
    n = int(30 * rate)
    pct = 30.0
    x = synth_emg(
        np.random.default_rng(4), params, mvc_rms_mv=MVC_RMS_MV, pct_mvc=constant(pct, n), fatigue=constant(0, n), rate_hz=rate
    )
    expected = np.hypot(MVC_RMS_MV * pct / 100, params.noise_rms_mv)
    assert rms(x) == pytest.approx(expected, rel=0.03)


def test_rest_segments_near_noise_floor(params, rate):
    n = int(30 * rate)
    # Sin reps, solo actividad tónica de reposo: RMS = hypot(mvc*rest/100, ruido).
    x = synth_emg(
        np.random.default_rng(6),
        params,
        mvc_rms_mv=MVC_RMS_MV,
        pct_mvc=constant(params.rest_pct_mvc, n),
        fatigue=constant(0, n),
        rate_hz=rate,
    )
    assert rms(x) == pytest.approx(np.hypot(MVC_RMS_MV * params.rest_pct_mvc / 100, params.noise_rms_mv), rel=0.03)
    # Y con reposo nulo, exactamente el suelo de ruido del sensor.
    x0 = synth_emg(
        np.random.default_rng(6), params, mvc_rms_mv=MVC_RMS_MV, pct_mvc=constant(0, n), fatigue=constant(0, n), rate_hz=rate
    )
    assert rms(x0) == pytest.approx(params.noise_rms_mv, rel=0.03)


def test_rest_between_reps_much_quieter_than_effort(params, rate):
    pct = activation_profile(REPS, peak_pct_mvc=60.0, rest_pct_mvc=params.rest_pct_mvc, n_samples=int(14 * rate), rate_hz=rate)
    x = synth_emg(
        np.random.default_rng(9), params, mvc_rms_mv=MVC_RMS_MV, pct_mvc=pct, fatigue=constant(0, pct.size), rate_hz=rate
    )
    rest_rms = rms(x[int(4.2 * rate) : int(5.8 * rate)])
    effort_rms = rms(x[int(2.8 * rate) : int(3.2 * rate)])
    assert rest_rms < 0.1 * effort_rms


def test_output_inside_band(clean, params, rate):
    n = int(20 * rate)
    x = synth_emg(
        np.random.default_rng(13), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=constant(50, n), fatigue=constant(0, n), rate_hz=rate
    )
    freqs, psd = sp_signal.welch(x, fs=rate, nperseg=int(rate))
    power = psd.sum()
    in_band = psd[(freqs >= params.band_low_hz) & (freqs <= params.band_high_hz)].sum()
    assert in_band / power > 0.98
    below = psd[freqs < params.band_low_hz / 2].sum()
    above = psd[freqs > params.band_high_hz * 1.2].sum()
    assert below / power < 0.005
    assert above / power < 0.005


def test_median_frequency_inside_band(clean, params, rate):
    n = int(20 * rate)
    x = synth_emg(
        np.random.default_rng(14), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=constant(50, n), fatigue=constant(0, n), rate_hz=rate
    )
    assert params.band_low_hz < median_frequency(x, rate) < params.band_high_hz


def test_median_frequency_drops_with_fatigue(clean, rate):
    n = int(20 * rate)
    medians = []
    for fatigue in (0.0, 0.25, 0.5, 0.75, 1.0):
        x = synth_emg(
            np.random.default_rng(21),
            clean,
            mvc_rms_mv=MVC_RMS_MV,
            pct_mvc=constant(50, n),
            fatigue=constant(fatigue, n),
            rate_hz=rate,
        )
        medians.append(median_frequency(x, rate))
    assert all(b < a for a, b in zip(medians, medians[1:], strict=False)), medians
    assert medians[-1] < 0.9 * medians[0]


def test_median_frequency_shift_matches_configured_shift(clean, rate):
    n = int(30 * rate)
    kwargs = dict(mvc_rms_mv=MVC_RMS_MV, pct_mvc=constant(50, n), rate_hz=rate)
    fresh = synth_emg(np.random.default_rng(22), clean, fatigue=constant(0, n), **kwargs)
    tired = synth_emg(np.random.default_rng(22), clean, fatigue=constant(1, n), **kwargs)
    expected = 1.0 - clean.fatigue_band_shift
    assert median_frequency(tired, rate) / median_frequency(fresh, rate) == pytest.approx(expected, rel=0.08)


def test_median_frequency_falls_over_fatigue_ramp(clean, rate):
    # Fatiga creciente dentro de una misma serie: la mediana de la ventana final es menor que la inicial.
    n = int(40 * rate)
    fatigue = np.linspace(0.0, 1.0, n)
    x = synth_emg(
        np.random.default_rng(23), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=constant(50, n), fatigue=fatigue, rate_hz=rate
    )
    early = median_frequency(x[: int(8 * rate)], rate)
    late = median_frequency(x[-int(8 * rate) :], rate)
    assert late < 0.9 * early


def test_amplitude_stable_across_fatigue_ramp(clean, rate):
    # La mezcla de niveles de banda no debe alterar de forma apreciable el RMS.
    n = int(40 * rate)
    x = synth_emg(
        np.random.default_rng(24),
        clean,
        mvc_rms_mv=MVC_RMS_MV,
        pct_mvc=constant(50, n),
        fatigue=np.linspace(0.0, 1.0, n),
        rate_hz=rate,
    )
    expected = MVC_RMS_MV * 0.5
    for start in range(0, n, int(8 * rate)):
        assert rms(x[start : start + int(8 * rate)]) == pytest.approx(expected, rel=0.08)


def test_fatigue_over_session_lowers_median_frequency(clean, rate):
    # Perfil real: tras varias reps con fatiga acumulada, la frecuencia mediana de las últimas reps baja.
    reps = tuple(Rep(i + 1, 2.0 + 4.0 * i, 5.0 + 4.0 * i) for i in range(10))
    n = int(44 * rate)
    pct = activation_profile(reps, peak_pct_mvc=60.0, rest_pct_mvc=2.0, n_samples=n, rate_hz=rate)
    fat = fatigue_profile(reps, fatigue_per_rep=0.1, n_samples=n, rate_hz=rate)
    x = synth_emg(np.random.default_rng(31), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=pct, fatigue=fat, rate_hz=rate)
    first = reps[0]
    last = reps[-1]
    mf_first = median_frequency(x[int((first.start_s + 0.5) * rate) : int((first.end_s - 0.5) * rate)], rate)
    mf_last = median_frequency(x[int((last.start_s + 0.5) * rate) : int((last.end_s - 0.5) * rate)], rate)
    assert mf_last < mf_first


def test_no_edge_transient(clean, rate):
    # El filtro de fase cero no deja arranque ni cola: el RMS de los bordes iguala al del centro.
    n = int(20 * rate)
    x = synth_emg(
        np.random.default_rng(41), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=constant(50, n), fatigue=constant(0.4, n), rate_hz=rate
    )
    edge = int(0.25 * rate)
    expected = MVC_RMS_MV * 0.5
    assert rms(x[:edge]) == pytest.approx(expected, rel=0.25)
    assert rms(x[-edge:]) == pytest.approx(expected, rel=0.25)


def test_ten_minutes_is_fast(params, rate):
    n = int(600 * rate)
    t0 = time.perf_counter()
    x = synth_emg(
        np.random.default_rng(51),
        params,
        mvc_rms_mv=MVC_RMS_MV,
        pct_mvc=constant(30, n),
        fatigue=np.linspace(0.0, 0.6, n),
        rate_hz=rate,
    )
    elapsed = time.perf_counter() - t0
    assert x.shape == (n,)
    assert elapsed < 10.0


# --- utilidades de análisis -------------------------------------------------------------------------


def test_rms_known_values():
    assert rms(np.array([3.0, -3.0, 3.0, -3.0])) == pytest.approx(3.0)
    t = np.arange(1000) / 1000
    assert rms(np.sin(2 * np.pi * 50 * t)) == pytest.approx(1 / np.sqrt(2), rel=1e-6)
    with pytest.raises(ValueError):
        rms(np.zeros(0))


def test_median_frequency_known_tone():
    rate = 1000.0
    t = np.arange(int(10 * rate)) / rate
    assert median_frequency(np.sin(2 * np.pi * 100 * t), rate) == pytest.approx(100.0, abs=2.0)


def test_rms_and_median_frequency_reject_inputs_too_short_to_measure(rate):
    with pytest.raises(ValueError, match=r"^rms of an empty array is undefined$"):
        rms(np.array([]))
    with pytest.raises(ValueError, match=r"^median_frequency needs at least 2 samples$"):
        median_frequency(np.array([1.0]), rate)
    assert np.isfinite(median_frequency(np.array([1.0, -1.0]), rate))  # dos muestras bastan


def test_median_frequency_of_degenerate_spectra(rate):
    assert median_frequency(np.zeros(500), rate) == 0.0  # sin potencia no hay frecuencia mediana
    nyquist = np.tile([1.0, -1.0], 250)  # toda la potencia en la ultima banda
    assert median_frequency(nyquist, rate) > 0.9 * rate / 2


def test_fatigue_above_one_counts_as_one(clean, rate):
    pct = constant(40.0, 600)

    def run(fatigue):
        return synth_emg(np.random.default_rng(5), clean, mvc_rms_mv=MVC_RMS_MV, pct_mvc=pct, fatigue=constant(fatigue, 600), rate_hz=rate)

    assert np.array_equal(run(2.5), run(1.0))
    assert not np.array_equal(run(1.0), run(0.5))
