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

from datetime import datetime, timedelta

import numpy as np
from spine_contracts import AstronautProfile, DailyContext, DailySelfReport
from spine_contracts.messages import SCHEMA_VERSION
from spine_contracts.models.common_schema import Source
from spine_contracts.models.domain.daily_context_schema import Countermeasure, ExerciseItem, MedicationItem
from spine_contracts.models.domain.daily_context_schema import Type as CountermeasureType
from spine_contracts.models.domain.self_report_schema import PainLocation, Side, Sleep

from spine_physiology.config import CrewMember, MissionConfig
from spine_physiology.state import DailyActivity, DayInfo, LatentState

# Fuente de todos los registros generados: el simulador nunca se presenta como dato real.
SOURCE = "simulator"


def build_profile(member: CrewMember, *, updated_at: datetime) -> AstronautProfile:
    profile = member.profile
    # Solo se pasan los campos opcionales que existen: un None explícito se emitiría como null y el
    # contrato no lo admite en campos que no son nullable.
    optional = {}
    if profile.gender_identity is not None:
        optional["gender_identity"] = profile.gender_identity
    if profile.spine_history is not None:
        optional["spine_history"] = profile.spine_history
    if profile.space_history is not None:
        optional["space_history"] = profile.space_history
    if profile.fitness is not None:
        optional["fitness"] = profile.fitness
    return AstronautProfile(
        schema_version=SCHEMA_VERSION,
        astronaut_id=member.astronaut_id,
        updated_at=updated_at,
        date_of_birth=profile.date_of_birth,
        sex_at_birth=profile.sex_at_birth,
        anthropometry=profile.anthropometry,
        **optional,
    )


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
    exercise = []
    for bout in activity.exercise:
        # Un bloque de duración no positiva no existe para el contrato (duration_min > 0): se omite.
        if bout.duration_min <= 0:
            continue
        item = {"modality": bout.modality, "duration_min": bout.duration_min}
        if bout.load_kg is not None:
            item["load_kg"] = bout.load_kg
        if bout.rpe is not None:
            item["rpe"] = bout.rpe
        exercise.append(ExerciseItem(**item))

    countermeasures = [
        Countermeasure(type=CountermeasureType(bout.type), duration_min=bout.duration_min)
        for bout in activity.countermeasures
        if bout.duration_min > 0
    ]

    medication = []
    for rule in config.daily_context.medication:
        # Solo los días con dolor por encima del umbral; luego se decide con la probabilidad de la regla.
        if state.pain_stiffness < rule.pain_threshold:
            continue
        if rng.random() >= rule.probability:
            continue
        item = {
            "name": rule.name,
            "category": rule.category,
            "taken_at": day.at(rule.time_utc),
        }
        if rule.dose_mg is not None:
            item["dose_mg"] = rule.dose_mg
        medication.append(MedicationItem(**item))

    return DailyContext(
        schema_version=SCHEMA_VERSION,
        astronaut_id=member.astronaut_id,
        date=day.date,
        mission_day=day.mission_day,
        phase=day.phase,
        gravity_environment=day.gravity_environment,
        medication=medication,
        exercise=exercise,
        countermeasures=countermeasures,
    )


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
    cfg = config.self_report

    # Instante: hora de referencia del día + jitter uniforme simétrico, en milisegundos.
    jitter_s = round(float(rng.uniform(-cfg.time_jitter_s, cfg.time_jitter_s)), 3)
    timestamp = day.at(cfg.time_utc) + timedelta(seconds=jitter_s)

    pain = _nrs(rng, cfg.pain_nrs_per_unit * state.pain_stiffness, cfg.nrs_noise_sd)
    stiffness = _nrs(rng, cfg.stiffness_nrs_per_unit * state.pain_stiffness, cfg.nrs_noise_sd)
    worst_24h = min(10, pain + int(rng.integers(cfg.worst_24h_extra_nrs.min, cfg.worst_24h_extra_nrs.max + 1)))

    pain_locations = []
    if pain >= cfg.pain_location_min_nrs:
        pain_locations = [PainLocation(region=loc.region, side=Side(loc.side)) for loc in cfg.pain_locations]

    # Horas perdidas por dolor: el sueño baja con pain_nrs (penalización lineal, sin piso más allá de 0).
    hours = float(rng.normal(cfg.sleep_hours.mean, cfg.sleep_hours.sd)) - cfg.sleep_pain_penalty_h_per_nrs * pain
    hours = round(float(np.clip(hours, 0.0, 24.0)), 2)
    quality = _nrs(rng, cfg.sleep_quality_nrs.mean, cfg.sleep_quality_nrs.sd)

    red_flags = []
    if cfg.red_flag_probability_per_day > 0 and rng.random() < cfg.red_flag_probability_per_day:
        red_flags = [cfg.red_flags[int(rng.integers(len(cfg.red_flags)))]]

    return DailySelfReport(
        schema_version=SCHEMA_VERSION,
        report_id=report_id,
        astronaut_id=member.astronaut_id,
        timestamp=timestamp,
        mission_day=day.mission_day,
        source=Source(SOURCE),
        kind="daily",
        pain_nrs=pain,
        pain_nrs_worst_24h=worst_24h,
        pain_locations=pain_locations,
        stiffness_nrs=stiffness,
        sleep=Sleep(hours=hours, quality_nrs=quality),
        red_flags=red_flags,
    )


def _nrs(rng: np.random.Generator, mean: float, noise_sd: float) -> int:
    """Entero NRS 0..10: redondeo de mean + N(0, noise_sd), recortado al rango."""
    value = mean + float(rng.normal(0.0, noise_sd)) if noise_sd > 0 else mean
    return int(np.clip(np.round(value), 0, 10))
