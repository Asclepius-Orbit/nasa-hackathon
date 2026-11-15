"""Sample-level signal synthesis (numpy/scipy), with no knowledge of chunks, device or contracts.

Each module produces full-session arrays at a given rate; chunking, faults and
timestamping belong to `faults` and `device`. Here live the types shared between signal
modules (already implemented: they are deterministic and trivial).
"""

import math
from dataclasses import dataclass

import numpy as np

from spine_physiology.config import ExerciseProtocol


@dataclass(frozen=True, slots=True)
class Rep:
    """A repetition, in seconds from the start of the session (exclusive end)."""

    index: int  # 1-based, counted over the whole session (not per set), like the contract's rep_index
    start_s: float
    end_s: float
    set_index: int = 1  # 1-based, set it belongs to
    fatigue: float = 0.0
    capacity: float = 1.0
    amplitude_factor: float = 1.0
    fatigue_end: float = 0.0


@dataclass(frozen=True, slots=True)
class SetWindow:
    """A set: from the first rep to the last, in seconds from the start of the session."""

    index: int  # 1-based
    start_s: float
    end_s: float


def _set_period_s(protocol: ExerciseProtocol) -> float:
    """Duration of a set: reps_per_set × (rep + rest between reps)."""
    return protocol.reps_per_set * (protocol.rep_duration_s + protocol.rest_between_reps_s)


_MIN_REP_DURATION_S = 0.05  # keeps every rep strictly positive; a no-op for realistic variability


def rep_schedule(protocol: ExerciseProtocol, *, rng: np.random.Generator | None = None) -> tuple[Rep, ...]:
    """Protocol repetitions: lead_in, and per set `reps_per_set` × (rep + rest between reps);
    between sets, `rest_between_sets_s`. `Rep.index` counts across the session."""
    rng = rng if rng is not None else np.random.default_rng(0)
    reps: list[Rep] = []
    cursor = protocol.lead_in_s
    fatigue = 0.0
    for s in range(protocol.planned_sets):
        for _ in range(protocol.reps_per_set):
            variation = float(rng.uniform(-protocol.individual_variability, protocol.individual_variability))
            capacity = max(1.0 - 0.4 * fatigue, 0.4)
            duration = max(protocol.rep_duration_s * (1.0 + 0.5 * fatigue + variation), _MIN_REP_DURATION_S)
            end_fatigue = min(1.0, fatigue + protocol.effort_load * duration / protocol.fatigue_resistance)
            reps.append(
                Rep(
                    index=len(reps) + 1,
                    start_s=cursor,
                    end_s=cursor + duration,
                    set_index=s + 1,
                    fatigue=fatigue,
                    capacity=capacity,
                    amplitude_factor=capacity * (1.0 + variation),
                    fatigue_end=end_fatigue,
                )
            )
            fatigue = end_fatigue
            cursor += duration + protocol.rest_between_reps_s
            fatigue *= math.exp(-protocol.recovery_per_s * protocol.rest_between_reps_s)
        if s + 1 < protocol.planned_sets:
            cursor += protocol.rest_between_sets_s
            fatigue *= math.exp(-protocol.recovery_per_s * protocol.rest_between_sets_s)
    return tuple(reps)


def set_schedule(protocol: ExerciseProtocol, reps: tuple[Rep, ...] | None = None) -> tuple[SetWindow, ...]:
    """Protocol sets: from the first rep to the last of each one."""
    reps = rep_schedule(protocol) if reps is None else reps
    windows: list[SetWindow] = []
    for s in range(1, protocol.planned_sets + 1):
        in_set = [r for r in reps if r.set_index == s]
        windows.append(SetWindow(index=s, start_s=in_set[0].start_s, end_s=in_set[-1].end_s))
    return tuple(windows)


def exercise_duration_s(protocol: ExerciseProtocol, reps: tuple[Rep, ...] | None = None) -> float:
    """lead_in + sets × reps_per_set × (rep + rest between reps) + (sets − 1) × rest between sets + lead_out."""
    reps = rep_schedule(protocol) if reps is None else reps
    return reps[-1].end_s + protocol.rest_between_reps_s + protocol.lead_out_s


def n_samples(duration_s: float, rate_hz: float, samples_per_chunk: int) -> int:
    """Samples of a session of `duration_s`, rounded up to whole chunks."""
    chunks = int(np.ceil(duration_s * rate_hz / samples_per_chunk - 1e-9))
    return max(chunks, 1) * samples_per_chunk


def sample_times(n: int, rate_hz: float) -> np.ndarray:
    """Time of each sample in seconds from the start (i / rate_hz)."""
    return np.arange(n, dtype=np.float64) / rate_hz
