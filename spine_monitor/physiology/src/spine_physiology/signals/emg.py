"""Raw surface EMG, in mV.

Owner: B2. Model: Gaussian noise band-filtered to [band_low_hz, band_high_hz] (Butterworth of order
`filter_order`), modulated by the amplitude envelope `mvc_rms_mv × pct_mvc / 100`, plus background white noise
`noise_rms_mv`. Fatigue (0..1) shifts the band downwards by a relative `fatigue_band_shift × fatigue`,
so the median frequency drops along the series. Requirements verifiable in tests:
- RMS in constant-effort windows ∝ %MVC (apart from the noise floor).
- Median frequency lower with fatigue than without it, monotonic in fatigue.
- Same rng (same state) ⇒ bit-for-bit the same array.
"""

from collections.abc import Sequence

import numpy as np
from scipy import signal as sp_signal

from spine_physiology.config import EmgSignalConfig
from spine_physiology.signals import Rep

# Implementation details (not physiological or mission parameters):
# - The band with time-varying fatigue is synthesised by filtering ONE SAME white sequence with
#   `_FATIGUE_LEVELS` bands spread between the minimum and maximum fatigue of the series, and mixing the
#   contiguous levels with per-sample triangular weights (the same input keeps them correlated).
# - `_MIN_BAND_SCALE` keeps `1 − shift × fatigue` from collapsing the band to 0 Hz with shift = fatigue = 1.
# - `_RESPONSE_GRID` is the frequency grid for the filter power-normalisation factor.
_FATIGUE_LEVELS = 9
_MIN_BAND_SCALE = 0.05
_RESPONSE_GRID = 4096
# - `_ECG_SIGMA_S`: width (σ, s) of the synthetic QRS pulse of the ECG leak. Shape hypothesis, no source.
_ECG_SIGMA_S = 0.01


def effective_pct_mvc(protocol_pct_mvc: float, deconditioning: float, params: EmgSignalConfig) -> float:
    """Peak %MVC per rep with deconditioning: pct × (1 + deconditioning_pct_mvc_gain × d)."""
    return protocol_pct_mvc * (1.0 + params.deconditioning_pct_mvc_gain * deconditioning)


def effective_fatigue_per_rep(deconditioning: float, params: EmgSignalConfig) -> float:
    """Fatigue per rep with deconditioning: fatigue_per_rep × (1 + deconditioning_fatigue_gain × d)."""
    return params.fatigue_per_rep * (1.0 + params.deconditioning_fatigue_gain * deconditioning)


def _rep_slice(rep: Rep, n_samples: int, rate_hz: float) -> tuple[int, int]:
    """Indices [i0, i1) of the samples with start_s <= i / rate < end_s, clipped to the series."""
    i0 = int(np.ceil(rep.start_s * rate_hz - 1e-9))
    i1 = int(np.ceil(rep.end_s * rate_hz - 1e-9))
    return min(max(i0, 0), n_samples), min(max(i1, 0), n_samples)


def activation_profile(
    reps: Sequence[Rep],
    *,
    peak_pct_mvc: float | Sequence[float],
    rest_pct_mvc: float,
    n_samples: int,
    rate_hz: float,
) -> np.ndarray:
    """%MVC per sample: `rest_pct_mvc` outside the reps and a smooth bell up to the peak inside.

    `peak_pct_mvc` scalar (the same for all) or one per rep. Without reps: constant `rest_pct_mvc`.
    The bell is a sin² (Hann) that starts and returns to the rest level with zero slope.
    """
    peak_values = np.asarray(peak_pct_mvc, dtype=np.float64)
    if peak_values.ndim == 0:
        peaks = [float(peak_values)] * len(reps)
    else:
        peaks = [float(p) for p in peak_values]
        if len(peaks) != len(reps):
            raise ValueError(f"peak_pct_mvc has {len(peaks)} values for {len(reps)} reps")

    profile = np.full(n_samples, float(rest_pct_mvc), dtype=np.float64)
    for rep, peak in zip(reps, peaks, strict=True):
        i0, i1 = _rep_slice(rep, n_samples, rate_hz)
        if i1 <= i0:
            continue
        u = (np.arange(i0, i1, dtype=np.float64) / rate_hz - rep.start_s) / (rep.end_s - rep.start_s)
        profile[i0:i1] = rest_pct_mvc + (peak - rest_pct_mvc) * np.sin(np.pi * u) ** 2
    return profile


def fatigue_profile(reps: Sequence[Rep], *, fatigue_per_rep: float, n_samples: int, rate_hz: float) -> np.ndarray:
    """Fatigue index per sample in [0, 1]: grows during each rep (k reps done ⇒ ~k × per_rep).

    Within each rep it rises with a smooth ramp (smoothstep) and holds between reps. Saturates at 1.
    """
    profile = np.zeros(n_samples, dtype=np.float64)
    level = 0.0
    cursor = 0
    for rep in reps:
        i0, i1 = _rep_slice(rep, n_samples, rate_hz)
        i0 = max(i0, cursor)  # reps solapadas o desordenadas: no retroceder
        i1 = max(i1, i0)
        profile[cursor:i0] = level
        if i1 > i0:
            u = (np.arange(i0, i1, dtype=np.float64) / rate_hz - rep.start_s) / (rep.end_s - rep.start_s)
            u = np.clip(u, 0.0, 1.0)
            profile[i0:i1] = level + fatigue_per_rep * u * u * (3.0 - 2.0 * u)
        level += fatigue_per_rep
        cursor = i1
    profile[cursor:] = level
    return np.clip(profile, 0.0, 1.0)


def exercise_fatigue_profile(reps: Sequence[Rep], *, n_samples: int, rate_hz: float, recovery_per_s: float, fatigue_per_rep: float = 0.0) -> np.ndarray:
    """Interpolate the model's rep fatigue and exponential recovery between efforts."""
    times = np.arange(n_samples, dtype=np.float64) / rate_hz
    profile = np.zeros(n_samples, dtype=np.float64)
    extra = 0.0
    for index, rep in enumerate(reps):
        end_level = rep.fatigue_end
        inside = (times >= rep.start_s) & (times < rep.end_s)
        u = (times[inside] - rep.start_s) / (rep.end_s - rep.start_s)
        profile[inside] = rep.fatigue + (end_level - rep.fatigue) * u + extra + fatigue_per_rep * u
        next_start = reps[index + 1].start_s if index + 1 < len(reps) else np.inf
        rest = (times >= rep.end_s) & (times < next_start)
        profile[rest] = (end_level + extra + fatigue_per_rep) * np.exp(-recovery_per_s * (times[rest] - rep.end_s))
        if index + 1 < len(reps):
            extra = (extra + fatigue_per_rep) * np.exp(-recovery_per_s * (next_start - rep.end_s))
    return np.clip(profile, 0.0, 1.0)


def _band_sos(params: EmgSignalConfig, fatigue: float, rate_hz: float) -> np.ndarray:
    """Band-pass Butterworth with both edges shifted by a relative `fatigue_band_shift × fatigue`."""
    scale = max(1.0 - params.fatigue_band_shift * fatigue, _MIN_BAND_SCALE)
    sos = sp_signal.butter(
        params.filter_order,
        [params.band_low_hz * scale, params.band_high_hz * scale],
        btype="bandpass",
        fs=rate_hz,
        output="sos",
    )
    return np.asarray(sos, dtype=np.float64)  # the scipy stub returns a union by `output`


def _zero_phase_power_gain(sos: np.ndarray) -> float:
    """Output power of unit white noise after sosfiltfilt: mean of |H|⁴ over [0, Nyquist]."""
    _, h = sp_signal.sosfreqz(sos, worN=_RESPONSE_GRID)
    return float(np.mean(np.abs(h) ** 4))


def _filter_zero_phase(sos: np.ndarray, x: np.ndarray) -> np.ndarray:
    # Zero phase ⇒ no delay; the default reflection padding of sosfiltfilt damps the
    # edge transients. On short series the padding is reduced to the maximum possible.
    default_padlen = 3 * (2 * sos.shape[0] + 1)
    return sp_signal.sosfiltfilt(sos, x, padlen=min(default_padlen, x.size - 1))


def _lowpass_zero_phase(x: np.ndarray, cutoff_hz: float, rate_hz: float) -> tuple[np.ndarray, float]:
    """Butterworth de 2.º orden paso bajo de fase cero. Devuelve (y, potencia de salida de ruido blanco unitario)."""
    sos = np.asarray(sp_signal.butter(2, cutoff_hz, btype="lowpass", fs=rate_hz, output="sos"), dtype=np.float64)
    return _filter_zero_phase(sos, x), _zero_phase_power_gain(sos)


def _ecg_pulses(n: int, params: EmgSignalConfig, rate_hz: float, phase_s: float) -> np.ndarray:
    """Train of QRS complexes (Gaussian pulses of σ = _ECG_SIGMA_S) with peak `ecg_leak_mv`, one per beat."""
    t = np.arange(n, dtype=np.float64) / rate_hz
    period = 60.0 / params.ecg_heart_rate_bpm
    nearest = ((t - phase_s + period / 2.0) % period) - period / 2.0  # signed distance to the nearest beat
    return params.ecg_leak_mv * np.exp(-0.5 * (nearest / _ECG_SIGMA_S) ** 2)


def _interference(rng: np.random.Generator, params: EmgSignalConfig, n: int, rate_hz: float) -> np.ndarray:
    """Sum of slow drift, mains and ECG leak (each one 0 if its amplitude is 0)."""
    t = np.arange(n, dtype=np.float64) / rate_hz
    out = np.zeros(n, dtype=np.float64)
    if params.baseline_wander_mv > 0.0:
        slow, gain = _lowpass_zero_phase(rng.standard_normal(n), params.baseline_wander_hz, rate_hz)
        out += params.baseline_wander_mv * slow / np.sqrt(gain)
    if params.mains_amplitude_mv > 0.0:
        phase = rng.uniform(0.0, 2.0 * np.pi)
        out += params.mains_amplitude_mv * np.sin(2.0 * np.pi * params.mains_hz * t + phase)
    if params.ecg_leak_mv > 0.0:
        out += _ecg_pulses(n, params, rate_hz, rng.uniform(0.0, 60.0 / params.ecg_heart_rate_bpm))
    return out


def _apply_signal_kind(x: np.ndarray, params: EmgSignalConfig, rate_hz: float) -> np.ndarray:
    """raw: tal cual; rectified: |x|; envelope: |x| suavizada (paso bajo de fase cero). Luego cuantiza si procede."""
    if params.signal_kind != "raw":
        x = np.abs(x)
    if params.signal_kind == "envelope":
        x, _ = _lowpass_zero_phase(x, params.envelope_lowpass_hz, rate_hz)
        x = np.maximum(x, 0.0)  # the low-pass can leave a tiny negative ripple
    if params.quantize_adc:
        x = _quantize(x, params)
    return x


def _quantize(x: np.ndarray, params: EmgSignalConfig) -> np.ndarray:
    """Rounds to ADC counts (step `vref / gain / 2^bits`, referred to the input, in mV) and saturates.

    Raw: bipolar around mid-scale [-2^(b-1), 2^(b-1) - 1]; rectified/envelope: unipolar [0, 2^b - 1].
    """
    acq = params.acquisition
    full = 2**acq.adc_bits
    lsb_mv = acq.vref_v * 1000.0 / acq.gain / full
    low, high = (-full // 2, full // 2 - 1) if params.signal_kind == "raw" else (0, full - 1)
    return np.clip(np.round(x / lsb_mv), low, high) * lsb_mv


def synth_emg(
    rng: np.random.Generator,
    params: EmgSignalConfig,
    *,
    mvc_rms_mv: float,
    pct_mvc: np.ndarray,
    fatigue: np.ndarray,
    rate_hz: float,
) -> np.ndarray:
    """Raw EMG in mV (float64, len(pct_mvc) samples). `pct_mvc` and `fatigue` have the same length.

    A single white-noise block is band-filtered (with the band shifted according to fatigue),
    normalised to unit RMS and multiplied by the envelope `mvc_rms_mv × pct / 100`; then
    sensor white noise `noise_rms_mv` is added. It draws 2 × n normals from `rng` and, only if they are
    on, afterwards those of drift (n normals), mains (1 phase) and ECG (1 phase), in that order. At the end it
    applies `signal_kind` (raw | rectified | envelope) and, if `quantize_adc`, the ADC quantisation.
    """
    pct = np.asarray(pct_mvc, dtype=np.float64)
    fat = np.clip(np.asarray(fatigue, dtype=np.float64), 0.0, 1.0)
    if pct.ndim != 1 or pct.shape != fat.shape:
        raise ValueError(f"pct_mvc and fatigue must be 1-D with the same length, got {pct.shape} and {fat.shape}")
    n = pct.size
    white = rng.standard_normal(n)
    sensor_noise = rng.standard_normal(n)
    if n == 0:
        return np.zeros(0, dtype=np.float64)

    f_min, f_max = float(fat.min()), float(fat.max())
    if f_max - f_min <= 1e-12:
        levels = np.array([f_min])
        position = np.zeros(n)
    else:
        levels = np.linspace(f_min, f_max, _FATIGUE_LEVELS)
        position = (fat - f_min) / (f_max - f_min) * (_FATIGUE_LEVELS - 1)

    band = np.zeros(n, dtype=np.float64)
    for k, level in enumerate(levels):
        weight = np.clip(1.0 - np.abs(position - k), 0.0, 1.0)
        if not weight.any():
            continue
        sos = _band_sos(params, float(level), rate_hz)
        unit = _filter_zero_phase(sos, white) / np.sqrt(_zero_phase_power_gain(sos))
        band += weight * unit

    envelope = mvc_rms_mv * pct / 100.0
    out = envelope * band + params.noise_rms_mv * sensor_noise
    # Additive interference: their normals/phases are drawn from `rng` AFTER the 2 × n of the EMG and only if
    # they are on, so with them off the raw output does not change by a single bit.
    out = out + _interference(rng, params, n, rate_hz)
    return _apply_signal_kind(out, params, rate_hz)


def rms(x: np.ndarray) -> float:
    """Root mean square of the signal, in its own units."""
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        raise ValueError("rms of an empty array is undefined")
    return float(np.sqrt(np.mean(x * x)))


def median_frequency(x: np.ndarray, rate_hz: float) -> float:
    """Median frequency of the power spectrum (Welch) in Hz.

    1 s segments (1 Hz resolution) or the whole series if it is shorter; it is the frequency that splits
    the total power into two equal halves (linearly interpolated over the cumulative PSD).
    """
    x = np.asarray(x, dtype=np.float64)
    if x.size < 2:
        raise ValueError("median_frequency needs at least 2 samples")
    freqs, psd = sp_signal.welch(x, fs=rate_hz, nperseg=min(x.size, int(round(rate_hz))))
    cumulative = np.cumsum(psd)
    if cumulative[-1] <= 0.0:
        return 0.0
    return float(np.interp(cumulative[-1] / 2.0, cumulative, freqs))
