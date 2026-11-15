"""Daily and profile domain objects: DailySelfReport, DailyContext, AstronautProfile.

Owner: B5. Must implement, with the spine_contracts models (`source: simulator` where it exists,
`schema_version` = SCHEMA_VERSION, without passing None to optional fields):
- `build_profile`: AstronautProfile from `member.profile` (never travels over MQTT).
- `build_daily_context`: date, mission_day, phase and gravity of the day; exercise and countermeasures from the
  `DailyActivity` (the same one the latent model used, so the dose matches); medication according to
  `daily_context.medication` (threshold on the day's pain_stiffness + probability, `taken_at` at its time).
- `build_daily_self_report`: one per day, at `self_report.time_utc` + jitter; pain and stiffness NRS
  from the pain_stiffness latent, worst 24 h, locations if NRS >= threshold, sleep with a penalty for
  pain, red_flags always present (empty list = asked and none). Weekly ODI: informational (the simulator does not generate it and it never scores risk).
Everything that goes through `errors(...)` of `spine_contracts.validation` must come out without errors.
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

# Source of all generated records: the simulator never presents itself as real data.
SOURCE = "simulator"


def build_profile(member: CrewMember, *, updated_at: datetime) -> AstronautProfile:
    profile = member.profile
    # Only the optional fields that exist are passed: an explicit None would be emitted as null and the
    # contract does not allow it in fields that are not nullable.
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
    """RNG: `(astronaut_id, day.index, "daily_context")` (only for the medication)."""
    exercise = []
    for bout in activity.exercise:
        # A non-positive-duration block does not exist for the contract (duration_min > 0): it is omitted.
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
        # Only days with pain above the threshold; then decided with the rule's probability.
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

    # Instant: reference time of the day + symmetric uniform jitter, in milliseconds.
    jitter_s = round(float(rng.uniform(-cfg.time_jitter_s, cfg.time_jitter_s)), 3)
    timestamp = day.at(cfg.time_utc) + timedelta(seconds=jitter_s)

    pain = _nrs(rng, cfg.pain_nrs_per_unit * state.pain_stiffness, cfg.nrs_noise_sd)
    stiffness = _nrs(rng, cfg.stiffness_nrs_per_unit * state.pain_stiffness, cfg.nrs_noise_sd)
    worst_24h = min(10, pain + int(rng.integers(cfg.worst_24h_extra_nrs.min, cfg.worst_24h_extra_nrs.max + 1)))

    pain_locations = []
    if pain >= cfg.pain_location_min_nrs:
        pain_locations = [PainLocation(region=loc.region, side=Side(loc.side)) for loc in cfg.pain_locations]

    # Hours lost to pain: sleep drops with pain_nrs (linear penalty, no floor beyond 0).
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
    """NRS integer 0..10: rounding of mean + N(0, noise_sd), clipped to the range."""
    value = mean + float(rng.normal(0.0, noise_sd)) if noise_sd > 0 else mean
    return int(np.clip(np.round(value), 0, 10))
