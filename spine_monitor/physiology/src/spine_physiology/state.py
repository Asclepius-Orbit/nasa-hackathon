"""Línea de tiempo de la misión y estado latente diario por astronauta.

Dueño: B1. Debe implementar:
- `MissionTimeline`: expande los tramos del config en días (`DayInfo`) con fecha, `mission_day`, fase,
  gravedad y posición en el tramo.
- `plan_daily_activity`: ejercicio general y contramedidas reales del día (lo que luego publica el
  DailyContext), a partir del plan por fase, la adherencia del astronauta, `sd_min` y `skip_probability`.
- `LatentModel`: latentes diarios = multiplicador × curva(mission_day) + deriva por dosis + N(0, daily_sd).
  Los latentes índice (`muscle_deconditioning`, `rom_motor_control`, `pain_stiffness`) se recortan a
  [0, 1]. RNG por rama: `(astronaut_id, day, "activity")` y `(astronaut_id, day, "latent")`, de modo que
  `state(a, d)` sale igual se pida solo o dentro de la trayectoria completa (la deriva por dosis es
  acumulativa: recorre la actividad de los días 0..d, que es barata y también regenerable por día).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Literal

import numpy as np
from spine_contracts import GravityEnvironment, MissionPhase

from spine_physiology.config import CrewMember, MissionConfig

# Gravedad de cada entorno en g. Definición física del enum del contrato, no un parámetro.
GRAVITY_G: dict[GravityEnvironment, float] = {
    GravityEnvironment.EARTH_1G: 1.0,
    GravityEnvironment.MICROGRAVITY: 0.0,
    GravityEnvironment.MOON_0_16G: 0.16,
    GravityEnvironment.MARS_0_38G: 0.38,
}


@dataclass(frozen=True, slots=True)
class DayInfo:
    """Un día de misión."""

    index: int  # 0 = primer día simulado; clave de RNG y de carpetas de salida
    date: date
    mission_day: int  # start_mission_day + index
    phase: MissionPhase
    gravity_environment: GravityEnvironment
    segment_index: int
    day_in_segment: int  # 0 = primer día del tramo

    @property
    def start(self) -> datetime:
        """00:00 UTC del día."""
        return datetime.combine(self.date, time(0, 0), tzinfo=UTC)

    def at(self, time_of_day: time) -> datetime:
        """Instante UTC de una hora del día."""
        return datetime.combine(self.date, time_of_day, tzinfo=UTC)

    @property
    def gravity_g(self) -> float:
        return GRAVITY_G[self.gravity_environment]


class MissionTimeline:
    """Días de la misión en orden, a partir de `start_date`, `start_mission_day` y `segments`."""

    def __init__(self, config: MissionConfig) -> None:
        raise NotImplementedError

    def __len__(self) -> int:
        raise NotImplementedError

    def __iter__(self) -> Iterator[DayInfo]:
        raise NotImplementedError

    def day(self, index: int) -> DayInfo:
        """Lanza IndexError fuera de [0, len)."""
        raise NotImplementedError

    @property
    def days(self) -> tuple[DayInfo, ...]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ExerciseBout:
    """Un bloque de ejercicio general hecho (DailyContext.exercise)."""

    modality: Literal["ared", "cevis", "t2", "other"]
    duration_min: float
    load_kg: float | None
    rpe: int | None


@dataclass(frozen=True, slots=True)
class CountermeasureBout:
    type: Literal["axial_loading_suit", "stretching", "other"]
    duration_min: float


@dataclass(frozen=True, slots=True)
class DailyActivity:
    """Lo que el astronauta hizo de verdad ese día (los saltados no aparecen)."""

    exercise: tuple[ExerciseBout, ...]
    countermeasures: tuple[CountermeasureBout, ...]
    planned_exercise_min: float  # suma del plan de la fase (antes de adherencia y saltos)

    @property
    def exercise_min(self) -> float:
        return sum(b.duration_min for b in self.exercise)

    @property
    def dose_ratio(self) -> float:
        """Minutos reales / planificados; 1 si no había nada planificado."""
        return self.exercise_min / self.planned_exercise_min if self.planned_exercise_min > 0 else 1.0


def plan_daily_activity(rng: np.random.Generator, config: MissionConfig, member: CrewMember, day: DayInfo) -> DailyActivity:
    """Ejercicio y contramedidas reales del día según el plan de su fase, adherencia, sd y saltos."""
    raise NotImplementedError


@dataclass(frozen=True, slots=True)
class LatentState:
    """Latentes de un astronauta un día (ver convenciones de unidades en `config`)."""

    astronaut_id: str
    day: int
    spine_elongation_mm: float
    muscle_deconditioning: float
    rom_motor_control: float
    pain_stiffness: float
    dose_ratio: float  # del propio día (DailyActivity.dose_ratio)


class LatentModel:
    """Latentes y actividad diarios de toda la tripulación, regenerables por (astronauta, día)."""

    def __init__(self, config: MissionConfig, timeline: MissionTimeline, seed: int) -> None:
        raise NotImplementedError

    def activity(self, astronaut_id: str, day: int) -> DailyActivity:
        raise NotImplementedError

    def state(self, astronaut_id: str, day: int) -> LatentState:
        raise NotImplementedError

    def trajectory(self, astronaut_id: str) -> tuple[LatentState, ...]:
        """Un LatentState por día de la misión, en orden."""
        raise NotImplementedError

