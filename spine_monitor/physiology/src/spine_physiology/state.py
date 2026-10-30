"""Línea de tiempo de la misión y estado latente diario por astronauta.

Dueño: B1. Implementa:
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
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

import numpy as np
from spine_contracts import GravityEnvironment, MissionPhase

from spine_physiology.config import LATENTS, CrewMember, MissionConfig
from spine_physiology.curves import PiecewiseLinear
from spine_physiology.rng import RngTree

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
        days: list[DayInfo] = []
        for seg_index, segment in enumerate(config.segments):
            for day_in_segment in range(segment.days):
                index = len(days)
                days.append(
                    DayInfo(
                        index=index,
                        date=config.start_date + timedelta(days=index),
                        mission_day=config.start_mission_day + index,
                        phase=segment.phase,
                        gravity_environment=segment.gravity_environment,
                        segment_index=seg_index,
                        day_in_segment=day_in_segment,
                    )
                )
        self._days = tuple(days)

    def __len__(self) -> int:
        return len(self._days)

    def __iter__(self) -> Iterator[DayInfo]:
        return iter(self._days)

    def day(self, index: int) -> DayInfo:
        """Lanza IndexError fuera de [0, len)."""
        if not 0 <= index < len(self._days):
            raise IndexError(f"day index {index} out of range [0, {len(self._days)})")
        return self._days[index]

    @property
    def days(self) -> tuple[DayInfo, ...]:
        return self._days


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
    exercise: list[ExerciseBout] = []
    planned = 0.0
    for item in config.daily_context.exercise[day.phase]:
        planned += item.planned_min
        # Se consumen siempre los dos números para que el flujo no dependa de si se salta o no.
        skipped = rng.random() < item.skip_probability
        minutes = max(0.0, float(rng.normal(item.planned_min * member.exercise_adherence, item.sd_min)))
        if not skipped and minutes > 0:
            exercise.append(ExerciseBout(item.modality, minutes, item.load_kg, item.rpe))
    countermeasures: list[CountermeasureBout] = []
    for cm in config.daily_context.countermeasures[day.phase]:
        skipped = rng.random() < cm.skip_probability
        minutes = max(0.0, float(rng.normal(cm.planned_min, cm.sd_min)))
        if not skipped and minutes > 0:
            countermeasures.append(CountermeasureBout(cm.type, minutes))
    return DailyActivity(tuple(exercise), tuple(countermeasures), planned)



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
        self._config = config
        self._timeline = timeline
        self._tree = RngTree(seed)
        self._curves = {name: PiecewiseLinear.from_curve(config.curves.curve(name)) for name in LATENTS}
        self._activity_cache: dict[tuple[str, int], DailyActivity] = {}

    def activity(self, astronaut_id: str, day: int) -> DailyActivity:
        key = (astronaut_id, day)
        cached = self._activity_cache.get(key)
        if cached is None:
            member = self._config.member(astronaut_id)
            rng = self._tree.rng(astronaut_id, day, "activity")
            cached = plan_daily_activity(rng, self._config, member, self._timeline.day(day))
            self._activity_cache[key] = cached
        return cached

    def _dose_deficit(self, astronaut_id: str, day: int) -> float:
        """Σ_{k<=day} (1 − dose_ratio_k) a partir de las actividades deterministas de cada día."""
        return sum(1.0 - self.activity(astronaut_id, k).dose_ratio for k in range(day + 1))

    def state(self, astronaut_id: str, day: int) -> LatentState:
        member = self._config.member(astronaut_id)
        info = self._timeline.day(day)
        deficit = self._dose_deficit(astronaut_id, day)
        noise = self._tree.rng(astronaut_id, day, "latent").standard_normal(len(LATENTS))  # orden de LATENTS
        values: dict[str, float] = {}
        for i, name in enumerate(LATENTS):
            curve_cfg = self._config.curves.curve(name)
            multiplier = getattr(member.latent_multipliers, name)
            value = multiplier * self._curves[name](info.mission_day) + curve_cfg.dose_drift_per_day * deficit
            value += curve_cfg.daily_sd * float(noise[i])
            values[name] = value if name == "spine_elongation" else min(1.0, max(0.0, value))
        return LatentState(
            astronaut_id=astronaut_id,
            day=day,
            spine_elongation_mm=values["spine_elongation"],
            muscle_deconditioning=values["muscle_deconditioning"],
            rom_motor_control=values["rom_motor_control"],
            pain_stiffness=values["pain_stiffness"],
            dose_ratio=self.activity(astronaut_id, day).dose_ratio,
        )

    def trajectory(self, astronaut_id: str) -> tuple[LatentState, ...]:
        """Un LatentState por día de la misión, en orden."""
        return tuple(self.state(astronaut_id, d) for d in range(len(self._timeline)))
