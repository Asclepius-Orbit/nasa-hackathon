"""Cordón conductor (Adafruit 519): longitud -> resistencia -> cuentas de ADC, y vuelta.

Dueño: B3. Cadena de una sesión:
1. `cord_length`: longitud real = reposo + elongation_coupling × elongación latente
   + flexion_mm_per_deg × |ángulo| − offset (pérdida diurna, `diurnal_loss_mm`).
2. `apply_hysteresis`: el cordón "recuerda" el estiramiento: residuo >= 0 que crece con
   `hysteresis_gain` × el estiramiento dinámico y decae con `hysteresis_tau_s`; la longitud efectiva que
   ve la resistencia es real + residuo. `initial_residual_mm` da continuidad entre sesiones.
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
    raise NotImplementedError


def cord_length(
    params: StretchSignalConfig,
    *,
    rest_length_mm: float,
    elongation_mm: float,
    angle_deg: np.ndarray,
    offset_mm: np.ndarray | float,
) -> np.ndarray:
    """Longitud real del cordón por muestra (mm)."""
    raise NotImplementedError


def apply_hysteresis(
    length_mm: np.ndarray,
    *,
    rest_length_mm: float,
    rate_hz: float,
    params: StretchSignalConfig,
    initial_residual_mm: float,
) -> tuple[np.ndarray, np.ndarray]:
    """(longitud efectiva, residuo) por muestra."""
    raise NotImplementedError


def length_to_resistance(length_mm: np.ndarray, curve: CurvePairs) -> np.ndarray:
    """Inversa de la curva R -> L (interpolación lineal, extrapolación con la pendiente del extremo)."""
    raise NotImplementedError


def resistance_to_length(resistance_ohm: np.ndarray, curve: CurvePairs) -> np.ndarray:
    """Curva R -> L (interpolación lineal, extrapolación con la pendiente del extremo)."""
    raise NotImplementedError


def resistance_to_adc(rng: np.random.Generator, resistance_ohm: np.ndarray, params: StretchSignalConfig) -> np.ndarray:
    """Cuentas de ADC (int64) del divisor, con ruido y recorte al rango del ADC."""
    raise NotImplementedError


def adc_to_resistance(adc_counts: np.ndarray, params: StretchSignalConfig) -> np.ndarray:
    """Resistencia (ohm) a partir de las cuentas (divisor inverso). Cuentas en los topes dan valores finitos."""
    raise NotImplementedError


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
    raise NotImplementedError


def derive(adc_counts: np.ndarray, params: StretchSignalConfig, calibration: CurvePairs) -> tuple[np.ndarray, np.ndarray]:
    """(resistance_ohm, length_mm) publicables a partir de las cuentas y la curva de calibración."""
    raise NotImplementedError


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
    """
    raise NotImplementedError
