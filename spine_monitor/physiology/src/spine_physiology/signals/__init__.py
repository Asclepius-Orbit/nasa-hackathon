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

    index: int  # 1-based, contado a lo largo de toda la sesión (no por serie), como rep_index del contrato
    start_s: float
    end_s: float
    set_index: int = 1  # 1-based, serie a la que pertenece


@dataclass(frozen=True, slots=True)
class SetWindow:
    """Una serie: de la primera rep a la última, en segundos desde el inicio de la sesión."""

    index: int  # 1-based
    start_s: float
    end_s: float


def _set_period_s(protocol: ExerciseProtocol) -> float:
    """Duración de una serie: reps_per_set × (rep + descanso entre reps)."""
    return protocol.reps_per_set * (protocol.rep_duration_s + protocol.rest_between_reps_s)


def rep_schedule(protocol: ExerciseProtocol) -> tuple[Rep, ...]:
    """Repeticiones del protocolo: lead_in, y por serie `reps_per_set` × (rep + descanso entre reps);
    entre series, `rest_between_sets_s`. `Rep.index` cuenta a lo largo de la sesión."""
    period = protocol.rep_duration_s + protocol.rest_between_reps_s
    reps: list[Rep] = []
    for s in range(protocol.planned_sets):
        set_start = protocol.lead_in_s + s * (_set_period_s(protocol) + protocol.rest_between_sets_s)
        for i in range(protocol.reps_per_set):
            start = set_start + i * period
            reps.append(Rep(index=len(reps) + 1, start_s=start, end_s=start + protocol.rep_duration_s, set_index=s + 1))
    return tuple(reps)


def set_schedule(protocol: ExerciseProtocol) -> tuple[SetWindow, ...]:
    """Series del protocolo: de la primera rep a la última de cada una."""
    reps = rep_schedule(protocol)
    windows: list[SetWindow] = []
    for s in range(1, protocol.planned_sets + 1):
        in_set = [r for r in reps if r.set_index == s]
        windows.append(SetWindow(index=s, start_s=in_set[0].start_s, end_s=in_set[-1].end_s))
    return tuple(windows)


def exercise_duration_s(protocol: ExerciseProtocol) -> float:
    """lead_in + series × reps_per_set × (rep + descanso entre reps) + (series − 1) × descanso entre series + lead_out."""
    p = protocol
    return p.lead_in_s + p.planned_sets * _set_period_s(p) + (p.planned_sets - 1) * p.rest_between_sets_s + p.lead_out_s


def n_samples(duration_s: float, rate_hz: float, samples_per_chunk: int) -> int:
    """Muestras de una sesión de `duration_s`, redondeadas hacia arriba a chunks completos."""
    chunks = int(np.ceil(duration_s * rate_hz / samples_per_chunk - 1e-9))
    return max(chunks, 1) * samples_per_chunk


def sample_times(n: int, rate_hz: float) -> np.ndarray:
    """Tiempo de cada muestra en segundos desde el inicio (i / rate_hz)."""
    return np.arange(n, dtype=np.float64) / rate_hz
