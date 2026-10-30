"""Objetos de dominio diarios y de perfil: DailySelfReport, DailyContext, AstronautProfile.

Dueño: B5. Debe implementar, con los modelos de spine_contracts (`source: simulator` donde exista,
`schema_version` = SCHEMA_VERSION, sin pasar None a campos opcionales):
- `build_profile`: AstronautProfile desde `member.profile` (nunca viaja por MQTT).
- `build_daily_context`: fecha, mission_day, fase y gravedad del día; ejercicio y contramedidas de la
  `DailyActivity` (la misma que usó el modelo latente, así la dosis cuadra); medicación según
  `daily_context.medication` (umbral sobre pain_stiffness del día + probabilidad, `taken_at` a su hora).
- `build_daily_self_report`: uno cada día, a `self_report.time_utc` + jitter; NRS de dolor y rigidez
  desde el latente pain_stiffness, peor 24 h, localizaciones si NRS >= umbral, sueño con penalización por
  dolor, red_flags siempre presente (lista vacía = preguntado y ninguna). ODI semanal: aplazado.
Todo lo que pasa por `errors(...)` de `spine_contracts.validation` debe salir sin errores.
"""

from datetime import datetime

import numpy as np
from spine_contracts import AstronautProfile, DailyContext, DailySelfReport

from spine_physiology.config import CrewMember, MissionConfig
from spine_physiology.state import DailyActivity, DayInfo, LatentState


def build_profile(member: CrewMember, *, updated_at: datetime) -> AstronautProfile:
    raise NotImplementedError


def build_daily_context(
    rng: np.random.Generator,
    config: MissionConfig,
    member: CrewMember,
    day: DayInfo,
    *,
    activity: DailyActivity,
    state: LatentState,
) -> DailyContext:
    """RNG: `(astronaut_id, day.index, "daily_context")` (solo para la medicación)."""
    raise NotImplementedError


def build_daily_self_report(
    rng: np.random.Generator,
    config: MissionConfig,
    member: CrewMember,
    day: DayInfo,
    *,
    state: LatentState,
    report_id: str,
) -> DailySelfReport:
    """RNG: `(astronaut_id, day.index, "self_report")`; report_id = derive_uuid(seed, astronaut_id, day.index, "self_report")."""
    raise NotImplementedError
