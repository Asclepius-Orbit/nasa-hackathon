from datetime import date, timedelta
from pathlib import Path

import pytest
from spine_contracts import GravityEnvironment, MissionPhase
from spine_physiology.config import MissionConfig, load_config
from spine_physiology.curves import PiecewiseLinear
from spine_physiology.state import LatentModel, MissionTimeline

CONFIG = Path(__file__).parents[1] / "configs" / "example-mission.yaml"
LATENT_NAMES = ("spine_elongation", "muscle_deconditioning", "rom_motor_control", "pain_stiffness")


@pytest.fixture(scope="module")
def cfg() -> MissionConfig:
    return load_config(CONFIG)


def make(cfg, seed=5):
    return LatentModel(cfg, MissionTimeline(cfg), seed)


def with_curves(cfg, **update):
    curves = cfg.curves.model_copy(
        update={n: getattr(cfg.curves, n).model_copy(update=update) for n in LATENT_NAMES}
    )
    return cfg.model_copy(update={"curves": curves})


def test_timeline(cfg):
    tl = MissionTimeline(cfg)
    assert len(tl) == cfg.total_days == 14
    days = list(tl)
    assert [d.index for d in days] == list(range(14))
    assert [d.mission_day for d in days] == [cfg.start_mission_day + i for i in range(14)]
    assert days[0].date == cfg.start_date and isinstance(days[0].date, date)
    assert days[1].date == cfg.start_date + timedelta(days=1)
    phases = [MissionPhase.PREFLIGHT] * 3 + [MissionPhase.INFLIGHT] * 8 + [MissionPhase.POSTFLIGHT] * 3
    assert [d.phase for d in days] == phases
    assert days[2].gravity_g == 1.0 and days[3].gravity_g == 0.0
    assert days[3].gravity_environment is GravityEnvironment.MICROGRAVITY
    assert [d.segment_index for d in days] == [0] * 3 + [1] * 8 + [2] * 3
    assert [d.day_in_segment for d in days][:5] == [0, 1, 2, 0, 1]
    assert tl.day(13) is tl.days[13]
    with pytest.raises(IndexError):
        tl.day(14)
    with pytest.raises(IndexError):
        tl.day(-1)


def test_deterministic_and_seed_sensitive(cfg):
    a = make(cfg).trajectory("AST-01")
    assert a == make(cfg).trajectory("AST-01")
    assert a != make(cfg, seed=6).trajectory("AST-01")
    assert len(a) == 14


def test_independent_of_call_order(cfg):
    full = make(cfg).trajectory("AST-01")
    m = make(cfg)
    for d in (9, 2, 13, 0, 7):
        assert m.state("AST-01", d) == full[d]
    assert make(cfg).state("AST-01", 12) == full[12]
    assert make(cfg).activity("AST-01", 12) == make(cfg).activity("AST-01", 12)


def test_state_follows_curve_when_sd_zero(cfg):
    flat = with_curves(cfg, daily_sd=0.0, dose_drift_per_day=0.0)
    traj = make(flat).trajectory("AST-01")
    mult = flat.crew[0].latent_multipliers
    el = PiecewiseLinear.from_curve(flat.curves.spine_elongation)
    de = PiecewiseLinear.from_curve(flat.curves.muscle_deconditioning)
    for s, info in zip(traj, MissionTimeline(flat), strict=True):
        assert s.spine_elongation_mm == pytest.approx(mult.spine_elongation * el(info.mission_day))
        expected = min(1.0, max(0.0, mult.muscle_deconditioning * de(info.mission_day)))
        assert s.muscle_deconditioning == pytest.approx(expected)


def test_index_latents_clipped(cfg):
    noisy = with_curves(cfg, daily_sd=5.0)
    traj = make(noisy).trajectory("AST-01")
    for s in traj:
        for v in (s.muscle_deconditioning, s.rom_motor_control, s.pain_stiffness):
            assert 0.0 <= v <= 1.0
    # La elongación no se recorta.
    assert any(abs(s.spine_elongation_mm) > 1.0 for s in traj)


def test_dose_drift_applies(cfg):
    base = with_curves(cfg, daily_sd=0.0)
    member = base.crew[0].model_copy(update={"exercise_adherence": 0.0})
    c2 = base.model_copy(update={"crew": [member]})
    m = make(c2)
    el = PiecewiseLinear.from_curve(c2.curves.spine_elongation)
    drift = c2.curves.spine_elongation.dose_drift_per_day
    for d in range(len(m._timeline)):
        deficit = sum(1 - m.activity("AST-01", k).dose_ratio for k in range(d + 1))
        expected = el(m._timeline.day(d).mission_day) + drift * deficit
        assert m.state("AST-01", d).spine_elongation_mm == pytest.approx(expected)
    assert m.activity("AST-01", 5).dose_ratio < 1.0


def test_activity_skips(cfg):
    items = {p: [i.model_copy(update={"skip_probability": 1.0}) for i in v] for p, v in cfg.daily_context.exercise.items()}
    c2 = cfg.model_copy(update={"daily_context": cfg.daily_context.model_copy(update={"exercise": items})})
    act = make(c2).activity("AST-01", 4)
    assert act.exercise == () and act.exercise_min == 0.0
    assert act.planned_exercise_min > 0 and act.dose_ratio == 0.0
    assert make(cfg).activity("AST-01", 4).planned_exercise_min == act.planned_exercise_min
