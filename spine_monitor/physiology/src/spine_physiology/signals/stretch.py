"""Conductive cord (Adafruit 519): length -> resistance -> ADC counts, and back.

Owner: B3. Chain of a session:
1. `cord_length`: true length = rest + elongation_coupling × latent elongation
   + flexion_mm_per_deg × |angle| − offset (diurnal loss, `diurnal_loss_mm`).
2. `apply_hysteresis`: the cord "remembers" the stretch: residual >= 0 that follows at least
   `hysteresis_gain` × (length − rest)+ and, when the stretch ceases, decays with `hysteresis_tau_s`
   (peak hold with exponential decay: r[k] = max(r[k−1]·e^{−dt/τ}, gain × (L[k] − rest)+));
   the effective length seen by the resistance is true + residual. `initial_residual_mm` gives continuity
   between sessions.
3. `length_to_resistance` with the TRUE curve (`params.curve_pairs()`, inverse of R -> L).
4. `resistance_to_adc`: divider of `params.circuit` + noise `adc_noise_counts`, rounding and clipping to
   [0, 2^bits − 1], int64.
5. (quality faults on adc_counts, in `faults`)
6. `derive`: adc -> resistance (inverse divider) -> length with the published CALIBRATION curve.
Verifiable requirements: without noise or hysteresis, derive(adc) recovers the length (quantization error);
after a stretch the residual decays ~e^{-t/τ}; same rng ⇒ same arrays.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.signal import lfilter

from spine_physiology.config import StretchCalibrationConfig, StretchSignalConfig

# (resistance_ohm, length_mm) pairs sorted by resistance, like the contract's `curve.points`.
type CurvePairs = Sequence[tuple[float, float]]

# Exponent cap when rescaling the peak hold by blocks (avoids exp overflow); structural.
_MAX_BLOCK_EXPONENT = 30.0


@dataclass(frozen=True, slots=True)
class CordSamples:
    """Output of `synth_stretch` (all series with the same length)."""

    length_mm: np.ndarray  # true cord length (without hysteresis)
    effective_length_mm: np.ndarray  # true + hysteresis residual (what the resistance sees)
    residual_mm: np.ndarray  # hysteresis residual >= 0 (band_recovering and ground truth)
    creep_mm: np.ndarray  # slow creep (>= 0), already included in effective_length_mm; not hysteresis
    adc_counts: np.ndarray  # int64, before quality faults


def diurnal_loss_mm(params: StretchSignalConfig, *, gravity_g: float, awake_fraction: np.ndarray | float) -> np.ndarray | float:
    """Diurnal shortening: diurnal_loss_mm_at_1g × gravity_g × clip(awake_fraction, 0, 1).

    `awake_fraction` = elapsed fraction of the device's power-on -> power-off window.
    """
    return params.diurnal_loss_mm_at_1g * gravity_g * np.clip(awake_fraction, 0.0, 1.0)


def cord_length(
    params: StretchSignalConfig,
    *,
    rest_length_mm: float,
    elongation_mm: float,
    angle_deg: np.ndarray,
    offset_mm: np.ndarray | float,
) -> np.ndarray:
    """True cord length per sample (mm)."""
    angle = np.abs(np.asarray(angle_deg, dtype=np.float64))
    return rest_length_mm + params.elongation_coupling * elongation_mm + params.flexion_mm_per_deg * angle - offset_mm


def apply_hysteresis(
    length_mm: np.ndarray,
    *,
    rest_length_mm: float,
    rate_hz: float,
    params: StretchSignalConfig,
    initial_residual_mm: float,
) -> tuple[np.ndarray, np.ndarray]:
    """(effective length, residual) per sample.

    Residual = peak hold with exponential decay: r[k] = max(r[k−1]·a, target[k]) with
    a = e^{−dt/τ} and target = hysteresis_gain × max(L − rest, 0). Evaluated by blocks with rescaled
    cummax (no per-sample loop).
    """
    length = np.asarray(length_mm, dtype=np.float64)
    n = length.shape[0]
    target = params.hysteresis_gain * np.maximum(length - rest_length_mm, 0.0)
    residual = np.empty(n, dtype=np.float64)
    rate = 1.0 / (rate_hz * params.hysteresis_tau_s)  # exponent per sample (dt / τ)
    block = max(int(_MAX_BLOCK_EXPONENT / rate), 1)
    carry = max(float(initial_residual_mm), 0.0)
    for start in range(0, n, block):
        stop = min(start + block, n)
        j = np.arange(stop - start, dtype=np.float64)
        scaled = target[start:stop] * np.exp(rate * j)
        scaled[0] = max(scaled[0], carry * np.exp(-rate))
        residual[start:stop] = np.maximum.accumulate(scaled) * np.exp(-rate * j)
        carry = residual[stop - 1]
    return length + residual, residual


def _check_curve(curve: CurvePairs) -> tuple[np.ndarray, np.ndarray]:
    pts = np.asarray(curve, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 2 or pts.shape[0] < 2:
        raise ValueError("curve must have at least two (resistance_ohm, length_mm) points")
    r, length = pts[:, 0], pts[:, 1]
    if np.any(np.diff(r) <= 0) or np.any(np.diff(length) <= 0):
        raise ValueError("curve must be strictly increasing in resistance and length")
    return r, length


def _interp_extrapolate(x: np.ndarray, xp: np.ndarray, fp: np.ndarray) -> np.ndarray:
    """Linear interpolation with extrapolation using the slope of the end segment."""
    x = np.asarray(x, dtype=np.float64)
    y = np.interp(x, xp, fp)
    lo_slope = (fp[1] - fp[0]) / (xp[1] - xp[0])
    hi_slope = (fp[-1] - fp[-2]) / (xp[-1] - xp[-2])
    y = np.where(x < xp[0], fp[0] + (x - xp[0]) * lo_slope, y)
    return np.where(x > xp[-1], fp[-1] + (x - xp[-1]) * hi_slope, y)


def length_to_resistance(length_mm: np.ndarray, curve: CurvePairs) -> np.ndarray:
    """Inverse of the R -> L curve (linear interpolation, extrapolation with the end slope)."""
    r, length = _check_curve(curve)
    return _interp_extrapolate(length_mm, length, r)


def resistance_to_length(resistance_ohm: np.ndarray, curve: CurvePairs) -> np.ndarray:
    """R -> L curve (linear interpolation, extrapolation with the end slope)."""
    r, length = _check_curve(curve)
    return _interp_extrapolate(resistance_ohm, r, length)


def resistance_to_adc(rng: np.random.Generator, resistance_ohm: np.ndarray, params: StretchSignalConfig) -> np.ndarray:
    """ADC counts (int64) of the divider, with noise and clipping to the ADC range."""
    circuit = params.circuit
    full_scale = float(2**circuit.adc_bits - 1)
    resistance = np.asarray(resistance_ohm, dtype=np.float64)
    counts = full_scale * resistance / (resistance + circuit.series_resistor_ohm)
    counts = counts + rng.normal(0.0, params.adc_noise_counts, size=counts.shape)
    # Never at the rails (0 and full scale): the real ADC does not give those readings with the divider connected.
    return np.clip(np.rint(counts), 1.0, full_scale - 1.0).astype(np.int64)


def adc_to_resistance(adc_counts: np.ndarray, params: StretchSignalConfig) -> np.ndarray:
    """Resistance (ohm) from the counts (inverse divider). Counts at the limits give finite values."""
    circuit = params.circuit
    full_scale = float(2**circuit.adc_bits - 1)
    # Half a step of margin at the limits: 0 and full scale give neither 0 nor infinity.
    ratio = np.clip(np.asarray(adc_counts, dtype=np.float64), 0.5, full_scale - 0.5) / full_scale
    return circuit.series_resistor_ohm * ratio / (1.0 - ratio)


def lot_gain(rng: np.random.Generator, params: StretchSignalConfig) -> float:
    """Resistance gain of an astronaut's cord lot: N(1, lot_variation_fraction) clipped to ±3 sd
    and to >= 0.1 (provisional hypothesis). Drawn once per astronaut, with its own rng branch."""
    z = float(np.clip(rng.normal(), -3.0, 3.0))
    return max(1.0 + params.lot_variation_fraction * z, 0.1)


def apply_creep(length_mm: np.ndarray, *, rest_length_mm: float, rate_hz: float, params: StretchSignalConfig) -> np.ndarray:
    """Creep: first-order filter (constant creep_tau_s) of creep_fraction × max(L − rest, 0).
    Starts at 0 at the beginning of each session (no continuity between sessions)."""
    target = params.creep_fraction * np.maximum(np.asarray(length_mm, dtype=np.float64) - rest_length_mm, 0.0)
    a = float(np.exp(-1.0 / (rate_hz * params.creep_tau_s)))
    return np.asarray(lfilter([1.0 - a], [1.0, -a], target), dtype=np.float64)


def synth_stretch(
    rng: np.random.Generator,
    params: StretchSignalConfig,
    *,
    length_mm: np.ndarray,
    rest_length_mm: float,
    rate_hz: float,
    initial_residual_mm: float,
    lot_gain: float = 1.0,
) -> CordSamples:
    """Steps 2-4 of the chain, plus creep, lot gain, dispersion between readings and per-reading noise.

    The ADC noise comes from `rng`; the dispersion (one offset per session) and the per-reading resistance noise
    come from child branches (`rng.spawn`), so turning them on does not shift the ADC noise.
    """
    dispersion_rng, read_rng = rng.spawn(2)
    length = np.asarray(length_mm, dtype=np.float64)
    effective, residual = apply_hysteresis(length, rest_length_mm=rest_length_mm, rate_hz=rate_hz, params=params, initial_residual_mm=initial_residual_mm)
    creep = apply_creep(length, rest_length_mm=rest_length_mm, rate_hz=rate_hz, params=params)
    effective = effective + creep
    resistance = lot_gain * length_to_resistance(effective, params.curve_pairs())
    resistance = resistance + dispersion_rng.normal(0.0, params.repeat_dispersion_ohm)
    resistance = resistance + read_rng.normal(0.0, params.read_noise_ohm, size=resistance.shape)
    adc = resistance_to_adc(rng, resistance, params)
    return CordSamples(length_mm=length, effective_length_mm=effective, residual_mm=residual, creep_mm=creep, adc_counts=adc)


def derive(adc_counts: np.ndarray, params: StretchSignalConfig, calibration: CurvePairs) -> tuple[np.ndarray, np.ndarray]:
    """(resistance_ohm, length_mm) publishable from the counts and the calibration curve."""
    resistance = adc_to_resistance(adc_counts, params)
    return resistance, resistance_to_length(resistance, calibration)


def measured_curve(
    rng: np.random.Generator,
    params: StretchSignalConfig,
    calibration: StretchCalibrationConfig,
    *,
    rest_length_mm: float,
    lot_gain: float = 1.0,
) -> tuple[tuple[float, float], ...]:
    """Curve published in a StretchCalibration: `n_points` (R, L) points from rest upward,
    lengths with N(0, measurement_noise_mm) error, strictly increasing in R and L.
    The first point is the rest (its resistance is the calibration's rest_resistance_ohm).

    Nominal lengths are spread uniformly between the rest and the upper end of the true curve
    (if the rest already exceeds it, the width of the curve itself is used as the reach). The resistance of
    each point is the true one of the nominal length; the measurement error is applied to the length (the first
    point, the rest, is exact). The error is clipped to ±1/4 of the nominal step, which guarantees
    strictly increasing lengths.
    """
    _, l_true = _check_curve(params.curve_pairs())
    top = float(l_true[-1])
    if top <= rest_length_mm:
        top = rest_length_mm + float(l_true[-1] - l_true[0])
    nominal = np.linspace(rest_length_mm, top, calibration.n_points)
    step = (top - rest_length_mm) / (calibration.n_points - 1)
    resistance = lot_gain * length_to_resistance(nominal, params.curve_pairs())
    noise = rng.normal(0.0, calibration.measurement_noise_mm, size=calibration.n_points)
    noise = np.clip(noise, -step / 4.0, step / 4.0)
    noise[0] = 0.0
    measured = nominal + noise
    return tuple((float(r), float(length)) for r, length in zip(resistance, measured, strict=True))
