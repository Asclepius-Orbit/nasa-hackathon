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

from spine_physiology.config import EmgSignalConfig
from spine_physiology.signals import Rep


def effective_pct_mvc(protocol_pct_mvc: float, deconditioning: float, params: EmgSignalConfig) -> float:
    """%MVC pico por rep con desacondicionamiento: pct × (1 + deconditioning_pct_mvc_gain × d)."""
    raise NotImplementedError


def effective_fatigue_per_rep(deconditioning: float, params: EmgSignalConfig) -> float:
    """Fatiga por rep con desacondicionamiento: fatigue_per_rep × (1 + deconditioning_fatigue_gain × d)."""
    raise NotImplementedError


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
    """
    raise NotImplementedError


def fatigue_profile(reps: Sequence[Rep], *, fatigue_per_rep: float, n_samples: int, rate_hz: float) -> np.ndarray:
    """Índice de fatiga por muestra en [0, 1]: crece durante cada rep (k reps hechas ⇒ ~k × per_rep)."""
    raise NotImplementedError


def synth_emg(
    rng: np.random.Generator,
    params: EmgSignalConfig,
    *,
    mvc_rms_mv: float,
    pct_mvc: np.ndarray,
    fatigue: np.ndarray,
    rate_hz: float,
) -> np.ndarray:
    """EMG crudo en mV (float64, len(pct_mvc) muestras). `pct_mvc` y `fatigue` tienen la misma longitud."""
    raise NotImplementedError


def rms(x: np.ndarray) -> float:
    raise NotImplementedError


def median_frequency(x: np.ndarray, rate_hz: float) -> float:
    """Frecuencia mediana del espectro de potencia (Welch) en Hz."""
    raise NotImplementedError
