"""Tests de reports.py: perfil, contexto diario y autoinforme diario contra sus schemas y propiedades."""

from datetime import UTC, datetime, time
from pathlib import Path

import numpy as np
import pytest
import yaml
from spine_contracts import GravityEnvironment, MissionPhase
from spine_contracts.messages import dump
from spine_contracts.validation import errors

from spine_physiology.config import CrewMember, MissionConfig, load_config
from spine_physiology.reports import build_daily_context, build_daily_self_report, build_profile
from spine_physiology.rng import derive_uuid, generator
from spine_physiology.state import CountermeasureBout, DailyActivity, DayInfo, ExerciseBout, LatentState

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
UPDATED_AT = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def config() -> MissionConfig:
    return load_config(EXAMPLE)


def variant(config: MissionConfig, edit) -> MissionConfig:
    """Config derivada del ejemplo con una edición sobre el YAML crudo (el modelo es inmutable)."""
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    edit(data)
    return MissionConfig.model_validate(data)


def days_of(config: MissionConfig) -> list[DayInfo]:
    """Días de la misión construidos a mano (no depende de MissionTimeline)."""
    from datetime import timedelta

    out: list[DayInfo] = []
    index = 0
    for seg_index, seg in enumerate(config.segments):
        for k in range(seg.days):
            out.append(
                DayInfo(
                    index=index,
                    date=config.start_date + timedelta(days=index),
                    mission_day=config.start_mission_day + index,
                    phase=seg.phase,
                    gravity_environment=seg.gravity_environment,
                    segment_index=seg_index,
                    day_in_segment=k,
                )
            )
            index += 1
    return out


def activity_for(config: MissionConfig, day: DayInfo, adherence: float = 1.0) -> DailyActivity:
    """Actividad 'tal cual el plan' (sin azar): cada bloque planificado se hace con la adherencia dada."""
    plan = config.daily_context.exercise.get(day.phase, [])
    cm_plan = config.daily_context.countermeasures.get(day.phase, [])
    exercise = tuple(
        ExerciseBout(modality=p.modality, duration_min=p.planned_min * adherence, load_kg=p.load_kg, rpe=p.rpe)
        for p in plan
    )
    countermeasures = tuple(CountermeasureBout(type=c.type, duration_min=c.planned_min) for c in cm_plan)
    return DailyActivity(
        exercise=exercise,
        countermeasures=countermeasures,
        planned_exercise_min=sum(p.planned_min for p in plan),
    )


def state_for(member: CrewMember, day: DayInfo, pain: float, dose_ratio: float = 1.0) -> LatentState:
    return LatentState(
        astronaut_id=member.astronaut_id,
        day=day.index,
        spine_elongation_mm=5.0,
        muscle_deconditioning=0.1,
        rom_motor_control=0.1,
        pain_stiffness=pain,
        dose_ratio=dose_ratio,
    )


def self_report(config: MissionConfig, member: CrewMember, day: DayInfo, pain: float):
    rng = generator(config.seed, member.astronaut_id, day.index, "self_report")
    return build_daily_self_report(
        rng,
        config,
        member,
        day,
        state=state_for(member, day, pain),
        report_id=derive_uuid(config.seed, member.astronaut_id, day.index, "self_report"),
    )


def assert_valid(name: str, payload: dict) -> None:
    problems = errors(name, payload)
    assert problems == [], problems


# --- Perfil -----------------------------------------------------------------------------------------


def test_profile_validates_for_both_astronauts(config):
    for member in config.crew:
        profile = build_profile(member, updated_at=UPDATED_AT)
        payload = dump(profile)
        assert_valid("domain/astronaut-profile", payload)
        assert payload["astronaut_id"] == member.astronaut_id
        assert payload["schema_version"] == "1.0.0"
        assert ("gender_identity" in payload) == (member.profile.gender_identity is not None)


def test_profile_omits_absent_optional_fields(config):
    # AST-02 no tiene spine_history ni gender_identity: no deben aparecer como null.
    member = config.member("AST-02")
    payload = dump(build_profile(member, updated_at=UPDATED_AT))
    assert "spine_history" not in payload
    assert "gender_identity" not in payload
    assert payload["anthropometry"] == {"height_standing_cm": 181, "body_mass_kg": 82}


# --- Contexto diario --------------------------------------------------------------------------------


def test_daily_context_validates_across_all_days_and_astronauts(config):
    for member in config.crew:
        for day in days_of(config):
            activity = activity_for(config, day, member.exercise_adherence)
            state = state_for(member, day, pain=day.index / max(1, config.total_days - 1))
            rng = generator(config.seed, member.astronaut_id, day.index, "daily_context")
            ctx = build_daily_context(rng, config, member, day, activity=activity, state=state)
            payload = dump(ctx)
            assert_valid("domain/daily-context", payload)
            assert payload["date"] == day.date.isoformat()
            assert payload["mission_day"] == day.mission_day
            assert payload["phase"] == day.phase.value
            assert payload["gravity_environment"] == day.gravity_environment.value


def test_daily_context_phase_and_gravity_follow_the_day(config):
    member = config.crew[0]
    day = days_of(config)[config.segments[0].days]  # primer día inflight
    rng = generator(config.seed, member.astronaut_id, day.index, "daily_context")
    ctx = build_daily_context(rng, config, member, day, activity=activity_for(config, day), state=state_for(member, day, 0.0))
    assert ctx.phase is MissionPhase.INFLIGHT
    assert ctx.gravity_environment is GravityEnvironment.MICROGRAVITY


def test_daily_context_exercise_matches_activity(config):
    member = config.crew[0]
    day = days_of(config)[config.segments[0].days]
    activity = activity_for(config, day)
    rng = generator(config.seed, member.astronaut_id, day.index, "daily_context")
    payload = dump(build_daily_context(rng, config, member, day, activity=activity, state=state_for(member, day, 0.0)))
    assert [e["modality"] for e in payload["exercise"]] == [b.modality for b in activity.exercise]
    assert payload["exercise"][0]["load_kg"] == 60.0
    assert payload["exercise"][0]["rpe"] == 6
    assert payload["countermeasures"] == [{"type": "stretching", "duration_min": 15.0}]


def test_daily_context_omits_absent_load_and_rpe(config):
    member = config.crew[0]
    day = days_of(config)[0]
    activity = DailyActivity(
        exercise=(ExerciseBout(modality="other", duration_min=20.0, load_kg=None, rpe=None),),
        countermeasures=(),
        planned_exercise_min=20.0,
    )
    rng = generator(config.seed, member.astronaut_id, day.index, "daily_context")
    payload = dump(build_daily_context(rng, config, member, day, activity=activity, state=state_for(member, day, 0.0)))
    assert payload["exercise"] == [{"modality": "other", "duration_min": 20.0}]
    assert_valid("domain/daily-context", payload)


def test_daily_context_without_activity_is_valid(config):
    member = config.crew[0]
    day = days_of(config)[0]
    empty = DailyActivity(exercise=(), countermeasures=(), planned_exercise_min=0.0)
    rng = generator(config.seed, member.astronaut_id, day.index, "daily_context")
    payload = dump(build_daily_context(rng, config, member, day, activity=empty, state=state_for(member, day, 0.0)))
    assert payload["exercise"] == []
    assert payload["countermeasures"] == []
    assert_valid("domain/daily-context", payload)


def _medication_payload(config: MissionConfig, pain: float, seed: int = 7) -> list[dict]:
    member = config.crew[0]
    day = days_of(config)[2]
    rng = generator(seed, member.astronaut_id, day.index, "daily_context")
    ctx = build_daily_context(
        rng, config, member, day, activity=activity_for(config, day), state=state_for(member, day, pain)
    )
    payload = dump(ctx)
    assert_valid("domain/daily-context", payload)
    return payload.get("medication", [])


def test_medication_only_above_pain_threshold(config):
    # Umbral 0.35 y probabilidad 0.6 en el ejemplo: por debajo nunca se toma.
    sample = [_medication_payload(config, pain=0.2, seed=s) for s in range(50)]
    assert all(m == [] for m in sample)


def test_medication_taken_at_configured_time_when_probability_one(config):
    cfg = variant(config, lambda d: d["daily_context"]["medication"][0].__setitem__("probability", 1.0))
    meds = _medication_payload(cfg, pain=0.9)
    assert len(meds) == 1
    med = meds[0]
    day = days_of(cfg)[2]
    assert datetime.fromisoformat(med["taken_at"]) == day.at(time(8, 0))
    assert med["name"] == "paracetamol"
    assert med["dose_mg"] == 500.0


def test_medication_probability_zero_never_taken(config):
    cfg = variant(config, lambda d: d["daily_context"]["medication"][0].__setitem__("probability", 0.0))
    assert all(_medication_payload(cfg, pain=1.0, seed=s) == [] for s in range(20))


def test_medication_frequency_matches_probability(config):
    taken = sum(bool(_medication_payload(config, pain=0.9, seed=s)) for s in range(400))
    assert 0.5 < taken / 400 < 0.7  # probabilidad 0.6


# --- Autoinforme diario -----------------------------------------------------------------------------


def test_self_report_validates_for_every_day_and_astronaut(config):
    for member in config.crew:
        for day in days_of(config):
            # Latente que recorre 0..1 a lo largo de la misión, para cubrir todas las ramas.
            pain = day.index / max(1, config.total_days - 1)
            rng = generator(config.seed, member.astronaut_id, day.index, "self_report")
            report = build_daily_self_report(
                rng,
                config,
                member,
                day,
                state=state_for(member, day, pain),
                report_id=derive_uuid(config.seed, member.astronaut_id, day.index, "self_report"),
            )
            payload = dump(report)
            assert_valid("domain/self-report", payload)
            assert payload["source"] == "simulator"
            assert payload["kind"] == "daily"
            assert payload["mission_day"] == day.mission_day
            assert isinstance(payload["red_flags"], list)  # preguntado siempre


def test_self_report_is_deterministic(config):
    member = config.crew[1]
    day = days_of(config)[4]
    a = dump(self_report(config, member, day, pain=0.5))
    b = dump(self_report(config, member, day, pain=0.5))
    assert a == b


def test_self_report_differs_across_days(config):
    member = config.crew[0]
    days = days_of(config)
    reports = {repr(dump(self_report(config, member, d, pain=0.5))) for d in days[:4]}
    assert len(reports) > 1


def test_worst_24h_never_below_current_and_capped(config):
    member = config.crew[0]
    days = days_of(config)
    for pain in (0.0, 0.5, 1.0):
        for seed in range(30):
            cfg = variant(config, lambda d, s=seed: d.__setitem__("seed", s))
            payload = dump(self_report(cfg, member, days[0], pain=pain))
            assert payload["pain_nrs_worst_24h"] >= payload["pain_nrs"]
            assert payload["pain_nrs_worst_24h"] <= 10


def test_pain_rises_with_latent(config):
    member = config.crew[0]
    day = days_of(config)[0]

    def mean_pain(latent: float) -> float:
        values = []
        for seed in range(200):
            cfg = variant(config, lambda d, s=seed: d.__setitem__("seed", s))
            values.append(dump(self_report(cfg, member, day, pain=latent))["pain_nrs"])
        return float(np.mean(values))

    low, mid, high = mean_pain(0.0), mean_pain(0.5), mean_pain(1.0)
    assert low < mid < high
    assert high - low > 3  # pain_nrs_per_unit = 10 en el ejemplo


def test_stiffness_rises_with_latent(config):
    member = config.crew[0]
    day = days_of(config)[0]
    low = [dump(self_report(variant(config, lambda d, s=s: d.__setitem__("seed", s)), member, day, 0.0))["stiffness_nrs"] for s in range(50)]
    high = [dump(self_report(variant(config, lambda d, s=s: d.__setitem__("seed", s)), member, day, 1.0))["stiffness_nrs"] for s in range(50)]
    assert np.mean(high) > np.mean(low) + 3


def test_pain_locations_only_above_threshold(config):
    member = config.crew[0]
    day = days_of(config)[0]
    threshold = config.self_report.pain_location_min_nrs
    no_pain = dump(self_report(config, member, day, pain=0.0))
    assert no_pain["pain_nrs"] < threshold
    assert no_pain["pain_locations"] == []
    high = dump(self_report(config, member, day, pain=1.0))
    assert high["pain_nrs"] >= threshold
    assert high["pain_locations"] == [{"region": "lumbar", "side": "midline"}]


def test_timestamp_within_jitter_of_configured_time(config):
    member = config.crew[0]
    day = days_of(config)[3]
    reference = day.at(config.self_report.time_utc)
    jitter = config.self_report.time_jitter_s
    for seed in range(20):
        cfg = variant(config, lambda d, s=seed: d.__setitem__("seed", s))
        report = self_report(cfg, member, day, pain=0.3)
        delta = abs((report.timestamp - reference).total_seconds())
        assert delta <= jitter + 1e-6
        assert report.timestamp.tzinfo is not None


def test_red_flags_never_when_probability_zero(config):
    member = config.crew[0]
    days = days_of(config)
    assert all(dump(self_report(config, member, d, pain=1.0))["red_flags"] == [] for d in days)


def test_red_flags_present_when_probability_one(config):
    def enable(d):
        d["self_report"]["red_flag_probability_per_day"] = 1.0
        d["self_report"]["red_flags"] = ["leg_weakness", "saddle_numbness"]

    cfg = variant(config, enable)
    member = config.crew[0]
    day = days_of(cfg)[0]
    payload = dump(self_report(cfg, member, day, pain=0.2))
    assert_valid("domain/self-report", payload)
    assert len(payload["red_flags"]) == 1
    assert payload["red_flags"][0] in {"leg_weakness", "saddle_numbness"}


def test_sleep_shorter_with_more_pain(config):
    member = config.crew[0]
    day = days_of(config)[0]
    calm = [dump(self_report(variant(config, lambda d, s=s: d.__setitem__("seed", s)), member, day, 0.0))["sleep"]["hours"] for s in range(60)]
    painful = [dump(self_report(variant(config, lambda d, s=s: d.__setitem__("seed", s)), member, day, 1.0))["sleep"]["hours"] for s in range(60)]
    assert np.mean(painful) < np.mean(calm)


def test_all_example_outputs_validate_together(config):
    """Recorrido completo: perfil, contexto y autoinforme de todos los días y astronautas."""
    for member in config.crew:
        assert_valid("domain/astronaut-profile", dump(build_profile(member, updated_at=UPDATED_AT)))
        for day in days_of(config):
            activity = activity_for(config, day, member.exercise_adherence)
            state = state_for(member, day, pain=0.4)
            ctx_rng = generator(config.seed, member.astronaut_id, day.index, "daily_context")
            assert_valid(
                "domain/daily-context",
                dump(build_daily_context(ctx_rng, config, member, day, activity=activity, state=state)),
            )
            assert_valid("domain/self-report", dump(self_report(config, member, day, pain=0.4)))


def test_report_id_is_the_derived_uuid(config):
    member = config.crew[0]
    day = days_of(config)[0]
    report = self_report(config, member, day, pain=0.0)
    assert report.report_id == derive_uuid(config.seed, member.astronaut_id, day.index, "self_report")
