"""EMG de superficie crudo, en mV.

Dueño: B2. Modelo: ruido gaussiano filtrado en banda [band_low_hz, band_high_hz] (Butterworth de orden
`filter_order`), modulado por la envolvente de amplitud `mvc_rms_mv × pct_mvc / 100`, más ruido blanco de
fondo `noise_rms_mv`. La fatiga (0..1) desplaza la banda hacia abajo un `fatigue_band_shift × fatiga`
relativo, así que la frecuencia mediana baja a lo largo de la serie. Requisitos verificables en tests:
- RMS en ventanas de esfuerzo constante ∝ %MVC (salvo el suelo de ruido).
- Frecuencia mediana menor con fatiga que sin ella, monótona en la fatiga.
- Misma rng (mismo estado) ⇒ mismo array bit a bit.
"""

from collections.abc import Sequence

import numpy as np
from scipy import signal as sp_signal

from spine_physiology.config import EmgSignalConfig
from spine_physiology.signals import Rep

# Detalles de implementación (no son parámetros fisiológicos ni de misión):
# - La banda con fatiga variable en el tiempo se sintetiza filtrando UNA misma secuencia blanca con
#   `_FATIGUE_LEVELS` bandas repartidas entre la fatiga mínima y máxima de la serie, y mezclando los
#   niveles contiguos con pesos triangulares por muestra (la misma entrada los mantiene correlados).
# - `_MIN_BAND_SCALE` evita que `1 − shift × fatiga` colapse la banda a 0 Hz con shift = fatiga = 1.
# - `_RESPONSE_GRID` es la rejilla de frecuencias para el factor de normalización de potencia del filtro.
_FATIGUE_LEVELS = 9
_MIN_BAND_SCALE = 0.05
_RESPONSE_GRID = 4096
# - `_ECG_SIGMA_S`: anchura (σ, s) del pulso QRS sintético de la fuga de ECG. Hipótesis de forma, sin fuente.
_ECG_SIGMA_S = 0.01


def effective_pct_mvc(protocol_pct_mvc: float, deconditioning: float, params: EmgSignalConfig) -> float:
    """%MVC pico por rep con desacondicionamiento: pct × (1 + deconditioning_pct_mvc_gain × d)."""
    return protocol_pct_mvc * (1.0 + params.deconditioning_pct_mvc_gain * deconditioning)


def effective_fatigue_per_rep(deconditioning: float, params: EmgSignalConfig) -> float:
    """Fatiga por rep con desacondicionamiento: fatigue_per_rep × (1 + deconditioning_fatigue_gain × d)."""
    return params.fatigue_per_rep * (1.0 + params.deconditioning_fatigue_gain * deconditioning)


def _rep_slice(rep: Rep, n_samples: int, rate_hz: float) -> tuple[int, int]:
    """Índices [i0, i1) de las muestras con start_s <= i / rate < end_s, recortados a la serie."""
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
    """%MVC por muestra: `rest_pct_mvc` fuera de las reps y una campana suave hasta el pico dentro.

    `peak_pct_mvc` escalar (igual en todas) o uno por rep. Sin reps: constante `rest_pct_mvc`.
    La campana es un seno² (Hann) que parte y vuelve al nivel de reposo con pendiente nula.
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
    """Índice de fatiga por muestra en [0, 1]: crece durante cada rep (k reps hechas ⇒ ~k × per_rep).

    Dentro de cada rep sube con una rampa suave (smoothstep) y se mantiene entre reps. Se satura en 1.
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


def exercise_fatigue_profile(reps: Sequence[Rep], *, n_samples: int, rate_hz: float, recovery_per_s: float) -> np.ndarray:
    """Interpolate the model's rep fatigue and exponential recovery between efforts."""
    times = np.arange(n_samples, dtype=np.float64) / rate_hz
    profile = np.zeros(n_samples, dtype=np.float64)
    for index, rep in enumerate(reps):
        end_level = rep.fatigue_end
        inside = (times >= rep.start_s) & (times < rep.end_s)
        u = (times[inside] - rep.start_s) / (rep.end_s - rep.start_s)
        profile[inside] = rep.fatigue + (end_level - rep.fatigue) * u
        next_start = reps[index + 1].start_s if index + 1 < len(reps) else np.inf
        rest = (times >= rep.end_s) & (times < next_start)
        profile[rest] = end_level * np.exp(-recovery_per_s * (times[rest] - rep.end_s))
    return profile


def _band_sos(params: EmgSignalConfig, fatigue: float, rate_hz: float) -> np.ndarray:
    """Butterworth paso banda con ambos bordes desplazados un `fatigue_band_shift × fatiga` relativo."""
    scale = max(1.0 - params.fatigue_band_shift * fatigue, _MIN_BAND_SCALE)
    sos = sp_signal.butter(
        params.filter_order,
        [params.band_low_hz * scale, params.band_high_hz * scale],
        btype="bandpass",
        fs=rate_hz,
        output="sos",
    )
    return np.asarray(sos, dtype=np.float64)  # el stub de scipy devuelve una unión por `output`


def _zero_phase_power_gain(sos: np.ndarray) -> float:
    """Potencia de salida de ruido blanco unitario tras sosfiltfilt: media de |H|⁴ en [0, Nyquist]."""
    _, h = sp_signal.sosfreqz(sos, worN=_RESPONSE_GRID)
    return float(np.mean(np.abs(h) ** 4))


def _filter_zero_phase(sos: np.ndarray, x: np.ndarray) -> np.ndarray:
    # Fase cero ⇒ sin retardo; el padding por reflexión por defecto de sosfiltfilt amortigua los
    # transitorios de borde. En series cortas se reduce el padding al máximo posible.
    default_padlen = 3 * (2 * sos.shape[0] + 1)
    return sp_signal.sosfiltfilt(sos, x, padlen=min(default_padlen, x.size - 1))


def _lowpass_zero_phase(x: np.ndarray, cutoff_hz: float, rate_hz: float) -> tuple[np.ndarray, float]:
    """Butterworth de 2.º orden paso bajo de fase cero. Devuelve (y, potencia de salida de ruido blanco unitario)."""
    sos = np.asarray(sp_signal.butter(2, cutoff_hz, btype="lowpass", fs=rate_hz, output="sos"), dtype=np.float64)
    return _filter_zero_phase(sos, x), _zero_phase_power_gain(sos)


def _ecg_pulses(n: int, params: EmgSignalConfig, rate_hz: float, phase_s: float) -> np.ndarray:
    """Tren de complejos QRS (pulsos gaussianos de σ = _ECG_SIGMA_S) de pico `ecg_leak_mv`, uno por latido."""
    t = np.arange(n, dtype=np.float64) / rate_hz
    period = 60.0 / params.ecg_heart_rate_bpm
    nearest = ((t - phase_s + period / 2.0) % period) - period / 2.0  # distancia firmada al latido más cercano
    return params.ecg_leak_mv * np.exp(-0.5 * (nearest / _ECG_SIGMA_S) ** 2)


def _interference(rng: np.random.Generator, params: EmgSignalConfig, n: int, rate_hz: float) -> np.ndarray:
    """Suma de deriva lenta, red eléctrica y fuga de ECG (cada una a 0 si su amplitud es 0)."""
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
        x = np.maximum(x, 0.0)  # el paso bajo puede dejar un rizado negativo minúsculo
    if params.quantize_adc:
        x = _quantize(x, params)
    return x


def _quantize(x: np.ndarray, params: EmgSignalConfig) -> np.ndarray:
    """Redondea a cuentas del ADC (paso `vref / gain / 2^bits`, referido a la entrada, en mV) y satura.

    Cruda: bipolar sobre media escala [-2^(b-1), 2^(b-1) - 1]; rectificada/envolvente: unipolar [0, 2^b - 1].
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
    """EMG crudo en mV (float64, len(pct_mvc) muestras). `pct_mvc` y `fatigue` tienen la misma longitud.

    Un único bloque de ruido blanco se filtra en banda (con la banda desplazada según la fatiga),
    se normaliza a RMS unitario y se multiplica por la envolvente `mvc_rms_mv × pct / 100`; después
    se suma ruido blanco de sensor `noise_rms_mv`. Consume de `rng` 2 × n normales y, solo si están
    encendidas, después las de deriva (n normales), red (1 fase) y ECG (1 fase), por ese orden. Al final
    aplica `signal_kind` (raw | rectified | envelope) y, si `quantize_adc`, la cuantización del ADC.
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
    # Interferencias aditivas: sus normales/fases se sacan de `rng` DESPUÉS de las 2 × n del EMG y solo si
    # están encendidas, así que con ellas apagadas la salida cruda no cambia ni un bit.
    out = out + _interference(rng, params, n, rate_hz)
    return _apply_signal_kind(out, params, rate_hz)


def rms(x: np.ndarray) -> float:
    """Valor cuadrático medio (raíz) de la señal, en sus mismas unidades."""
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        raise ValueError("rms of an empty array is undefined")
    return float(np.sqrt(np.mean(x * x)))


def median_frequency(x: np.ndarray, rate_hz: float) -> float:
    """Frecuencia mediana del espectro de potencia (Welch) en Hz.

    Segmentos de 1 s (resolución 1 Hz) o la serie entera si es más corta; es la frecuencia que divide
    la potencia total en dos mitades iguales (interpolada linealmente sobre la PSD acumulada).
    """
    x = np.asarray(x, dtype=np.float64)
    if x.size < 2:
        raise ValueError("median_frequency needs at least 2 samples")
    freqs, psd = sp_signal.welch(x, fs=rate_hz, nperseg=min(x.size, int(round(rate_hz))))
    cumulative = np.cumsum(psd)
    if cumulative[-1] <= 0.0:
        return 0.0
    return float(np.interp(cumulative[-1] / 2.0, cumulative, freqs))
