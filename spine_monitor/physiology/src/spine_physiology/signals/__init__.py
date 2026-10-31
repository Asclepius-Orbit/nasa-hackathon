"""Síntesis de señales a nivel de muestra (numpy/scipy), sin conocer chunks, dispositivo ni contratos.

Cada módulo produce arrays de sesión completa a una frecuencia dada; el troceo en chunks, los fallos y
el sellado de tiempo son de `faults` y `device`. Aquí viven los tipos compartidos entre módulos de
señal (ya implementados: son deterministas y triviales).
"""

from dataclasses import dataclass

import numpy as np

from spine_physiology.config import ExerciseProtocol


@dataclass(frozen=True, slots=True)
class Rep:
    """Una repetición, en segundos desde el inicio de la sesión (fin exclusivo)."""

    index: int  # 1-based, como rep_index del contrato
    start_s: float
    end_s: float


def rep_schedule(protocol: ExerciseProtocol) -> tuple[Rep, ...]:
    """Repeticiones del protocolo: lead_in, y luego rep_duration_s + rest_between_reps_s por rep."""
    period = protocol.rep_duration_s + protocol.rest_between_reps_s
    return tuple(
        Rep(index=i + 1, start_s=protocol.lead_in_s + i * period, end_s=protocol.lead_in_s + i * period + protocol.rep_duration_s)
        for i in range(protocol.planned_reps)
    )


def exercise_duration_s(protocol: ExerciseProtocol) -> float:
    """lead_in + reps × rep + (reps − 1) × descanso + lead_out."""
    p = protocol
    return p.lead_in_s + p.planned_reps * p.rep_duration_s + (p.planned_reps - 1) * p.rest_between_reps_s + p.lead_out_s


def n_samples(duration_s: float, rate_hz: float, samples_per_chunk: int) -> int:
    """Muestras de una sesión de `duration_s`, redondeadas hacia arriba a chunks completos."""
    chunks = int(np.ceil(duration_s * rate_hz / samples_per_chunk - 1e-9))
    return max(chunks, 1) * samples_per_chunk


def sample_times(n: int, rate_hz: float) -> np.ndarray:
    """Tiempo de cada muestra en segundos desde el inicio (i / rate_hz)."""
    return np.arange(n, dtype=np.float64) / rate_hz
