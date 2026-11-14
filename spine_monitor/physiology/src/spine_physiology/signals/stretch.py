"""Cordón conductor (Adafruit 519): longitud -> resistencia -> cuentas de ADC, y vuelta.

Dueño: B3. Cadena de una sesión:
1. `cord_length`: longitud real = reposo + elongation_coupling × elongación latente
   + flexion_mm_per_deg × |ángulo| − offset (pérdida diurna, `diurnal_loss_mm`).
2. `apply_hysteresis`: el cordón "recuerda" el estiramiento: residuo >= 0 que sigue como mínimo
   `hysteresis_gain` × (longitud − reposo)+ y, cuando el estiramiento cesa, decae con `hysteresis_tau_s`
   (retención de pico con caída exponencial: r[k] = max(r[k−1]·e^{−dt/τ}, gain × (L[k] − reposo)+));
   la longitud efectiva que ve la resistencia es real + residuo. `initial_residual_mm` da continuidad
   entre sesiones.
3. `length_to_resistance` con la curva REAL (`params.curve_pairs()`, inversa de R -> L).
4. `resistance_to_adc`: divisor de `params.circuit` + ruido `adc_noise_counts`, redondeo y recorte a
   [0, 2^bits − 1], int64.
5. (fallos de calidad sobre adc_counts, en `faults`)
6. `derive`: adc -> resistencia (divisor inverso) -> longitud con la curva de CALIBRACIÓN publicada.
Requisitos verificables: sin ruido ni histéresis, derive(adc) recupera la longitud (error de cuantización);
tras un estiramiento el residuo cae ~e^{-t/τ}; misma rng ⇒ mismos arrays.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from spine_physiology.config import StretchCalibrationConfig, StretchSignalConfig

# Pares (resistance_ohm, length_mm) ordenados por resistencia, como `curve.points` del contrato.
type CurvePairs = Sequence[tuple[float, float]]

# Tope de exponente al reescalar la retención de pico por bloques (evita overflow de exp); estructural.
_MAX_BLOCK_EXPONENT = 30.0


@dataclass(frozen=True, slots=True)
class CordSamples:
    """Salida de `synth_stretch` (todas las series con la misma longitud)."""

    length_mm: np.ndarray  # longitud real del cordón (sin histéresis)
    effective_length_mm: np.ndarray  # real + residuo de histéresis (lo que ve la resistencia)
    residual_mm: np.ndarray  # residuo de histéresis >= 0 (band_recovering y ground truth)
    adc_counts: np.ndarray  # int64, antes de fallos de calidad


def diurnal_loss_mm(params: StretchSignalConfig, *, gravity_g: float, awake_fraction: np.ndarray | float) -> np.ndarray | float:
    """Acortamiento diurno: diurnal_loss_mm_at_1g × gravity_g × clip(awake_fraction, 0, 1).

    `awake_fraction` = fracción transcurrida de la ventana encendido -> apagado del dispositivo.
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
    """Longitud real del cordón por muestra (mm)."""
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
    """(longitud efectiva, residuo) por muestra.

    Residuo = retención de pico con caída exponencial: r[k] = max(r[k−1]·a, target[k]) con
    a = e^{−dt/τ} y target = hysteresis_gain × max(L − reposo, 0). Se evalúa por bloques con cummax
    reescalado (sin bucle por muestra).
    """
    length = np.asarray(length_mm, dtype=np.float64)
    n = length.shape[0]
    target = params.hysteresis_gain * np.maximum(length - rest_length_mm, 0.0)
    residual = np.empty(n, dtype=np.float64)
    rate = 1.0 / (rate_hz * params.hysteresis_tau_s)  # exponente por muestra (dt / τ)
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
    """Interpolación lineal con extrapolación por la pendiente del tramo extremo."""
    x = np.asarray(x, dtype=np.float64)
    y = np.interp(x, xp, fp)
    lo_slope = (fp[1] - fp[0]) / (xp[1] - xp[0])
    hi_slope = (fp[-1] - fp[-2]) / (xp[-1] - xp[-2])
    y = np.where(x < xp[0], fp[0] + (x - xp[0]) * lo_slope, y)
    return np.where(x > xp[-1], fp[-1] + (x - xp[-1]) * hi_slope, y)


def length_to_resistance(length_mm: np.ndarray, curve: CurvePairs) -> np.ndarray:
    """Inversa de la curva R -> L (interpolación lineal, extrapolación con la pendiente del extremo)."""
    r, length = _check_curve(curve)
    return _interp_extrapolate(length_mm, length, r)


def resistance_to_length(resistance_ohm: np.ndarray, curve: CurvePairs) -> np.ndarray:
    """Curva R -> L (interpolación lineal, extrapolación con la pendiente del extremo)."""
    r, length = _check_curve(curve)
    return _interp_extrapolate(resistance_ohm, r, length)


def resistance_to_adc(rng: np.random.Generator, resistance_ohm: np.ndarray, params: StretchSignalConfig) -> np.ndarray:
    """Cuentas de ADC (int64) del divisor, con ruido y recorte al rango del ADC."""
    circuit = params.circuit
    full_scale = float(2**circuit.adc_bits - 1)
    resistance = np.asarray(resistance_ohm, dtype=np.float64)
    counts = full_scale * resistance / (resistance + circuit.series_resistor_ohm)
    counts = counts + rng.normal(0.0, params.adc_noise_counts, size=counts.shape)
    return np.clip(np.rint(counts), 0.0, full_scale).astype(np.int64)


def adc_to_resistance(adc_counts: np.ndarray, params: StretchSignalConfig) -> np.ndarray:
    """Resistencia (ohm) a partir de las cuentas (divisor inverso). Cuentas en los topes dan valores finitos."""
    circuit = params.circuit
    full_scale = float(2**circuit.adc_bits - 1)
    # Medio escalón de margen en los topes: 0 y fondo de escala no dan 0 ni infinito.
    ratio = np.clip(np.asarray(adc_counts, dtype=np.float64), 0.5, full_scale - 0.5) / full_scale
    return circuit.series_resistor_ohm * ratio / (1.0 - ratio)


def synth_stretch(
    rng: np.random.Generator,
    params: StretchSignalConfig,
    *,
    length_mm: np.ndarray,
    rest_length_mm: float,
    rate_hz: float,
    initial_residual_mm: float,
) -> CordSamples:
    """Pasos 2-4 de la cadena."""
    length = np.asarray(length_mm, dtype=np.float64)
    effective, residual = apply_hysteresis(
        length, rest_length_mm=rest_length_mm, rate_hz=rate_hz, params=params, initial_residual_mm=initial_residual_mm
    )
    resistance = length_to_resistance(effective, params.curve_pairs())
    adc = resistance_to_adc(rng, resistance, params)
    return CordSamples(length_mm=length, effective_length_mm=effective, residual_mm=residual, adc_counts=adc)


def derive(adc_counts: np.ndarray, params: StretchSignalConfig, calibration: CurvePairs) -> tuple[np.ndarray, np.ndarray]:
    """(resistance_ohm, length_mm) publicables a partir de las cuentas y la curva de calibración."""
    resistance = adc_to_resistance(adc_counts, params)
    return resistance, resistance_to_length(resistance, calibration)


def measured_curve(
    rng: np.random.Generator,
    params: StretchSignalConfig,
    calibration: StretchCalibrationConfig,
    *,
    rest_length_mm: float,
) -> tuple[tuple[float, float], ...]:
    """Curva publicada en una StretchCalibration: `n_points` puntos (R, L) desde el reposo hacia arriba,
    longitudes con error N(0, measurement_noise_mm), estrictamente crecientes en R y L.
    El primer punto es el reposo (su resistencia es rest_resistance_ohm de la calibración).

    Las longitudes nominales se reparten uniformemente entre el reposo y el extremo superior de la curva
    real (si el reposo ya lo supera, se usa como alcance el ancho de la propia curva). La resistencia de
    cada punto es la real de la longitud nominal; el error de medida se aplica a la longitud (el primer
    punto, el reposo, es exacto). El error se recorta a ±1/4 del paso nominal, lo que garantiza
    longitudes estrictamente crecientes.
    """
    _, l_true = _check_curve(params.curve_pairs())
    top = float(l_true[-1])
    if top <= rest_length_mm:
        top = rest_length_mm + float(l_true[-1] - l_true[0])
    nominal = np.linspace(rest_length_mm, top, calibration.n_points)
    step = (top - rest_length_mm) / (calibration.n_points - 1)
    resistance = length_to_resistance(nominal, params.curve_pairs())
    noise = rng.normal(0.0, calibration.measurement_noise_mm, size=calibration.n_points)
    noise = np.clip(noise, -step / 4.0, step / 4.0)
    noise[0] = 0.0
    measured = nominal + noise
    return tuple((float(r), float(length)) for r, length in zip(resistance, measured, strict=True))
