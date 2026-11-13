"""Protocolo estandarizado de #12: 4 series x 15 reps, colocación de sensores y spine_check pre/post."""

from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from spine_contracts import GravityEnvironment, dump, validation
from spine_physiology import sessions as sess
from spine_physiology.config import ExerciseProtocol, MissionConfig
from spine_physiology.device import plan_device_day
from spine_physiology.signals import exercise_duration_s, rep_schedule, set_schedule
from spine_physiology.state import MissionTimeline

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
A = "AST-01"
LAT = "lat_pulldown_standing_v1"


def make_config(edit=None) -> MissionConfig:
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    if edit:
        edit(raw)
    return MissionConfig.model_validate(raw)


@pytest.fixture(scope="module")
def config() -> MissionConfig:
    return make_config()


@pytest.fixture(scope="module")
def timeline(config) -> MissionTimeline:
    return MissionTimeline(config)


def lat(config) -> ExerciseProtocol:
    protocol = config.protocol(LAT)
    assert isinstance(protocol, ExerciseProtocol)
    return protocol


def test_example_protocol_is_the_standard_lat_pulldown(config):
    p = lat(config)
    assert (p.planned_sets, p.reps_per_set, p.planned_reps, p.rest_between_sets_s) == (4, 15, 60, 30.0)
    assert p.load.band_id == "green" and p.load.nominal_kg == 25.0
    assert p.streams == ["emg_lat_left", "emg_lat_right", "imu_t9", "stretch_spine"]
    assert 4 * 60 <= exercise_duration_s(p) <= 7 * 60  # ~5 min


def test_example_streams_carry_the_standard_placement(config):
    emg = config.device.stream("emg_lat_right").placement
    assert emg.electrode_spacing_cm == 10.0 and emg.ground is not None and emg.ground.location.value == "iliac_crest"
    assert emg.span is not None and (emg.span.from_level.value, emg.span.to_level.value) == ("t11", "l2")
    assert config.device.stream("imu_t9").placement.spine_level.value == "t9"
    cord = config.device.stream("stretch_spine").placement
    assert cord.spine_region.value == "full_spine" and (cord.span.from_level.value, cord.span.to_level.value) == ("c3", "s1")
    assert 600 <= config.member(A).baseline.cord_rest_length_mm <= 700


def test_rep_schedule_counts_reps_across_sets_with_rest_between_sets(config):
    p = lat(config)
    reps = rep_schedule(p)
    assert [r.index for r in reps] == list(range(1, 61))
    assert [r.set_index for r in reps] == [s for s in range(1, 5) for _ in range(15)]
    period = p.rep_duration_s + p.rest_between_reps_s
    assert reps[0].start_s == p.lead_in_s and reps[14].end_s == p.lead_in_s + 14 * period + p.rep_duration_s
    assert reps[15].start_s == p.lead_in_s + 15 * period + p.rest_between_sets_s


def test_set_schedule_and_duration_formula(config):
    p = lat(config)
    sets = set_schedule(p)
    reps = rep_schedule(p)
    assert [s.index for s in sets] == [1, 2, 3, 4]
    for s in sets:
        in_set = [r for r in reps if r.set_index == s.index]
        assert s.start_s == in_set[0].start_s and s.end_s == in_set[-1].end_s
    assert sets[1].start_s - sets[0].end_s == pytest.approx(p.rest_between_reps_s + p.rest_between_sets_s)
    period = p.rep_duration_s + p.rest_between_reps_s
    assert exercise_duration_s(p) == pytest.approx(p.lead_in_s + 4 * 15 * period + 3 * p.rest_between_sets_s + p.lead_out_s)


def test_exercise_session_events_have_sets_and_session_wide_rep_index(config, timeline):
    info = timeline.day(0)
    plans = sess.plan_day(config, config.seed, A, info)
    plan = next(p for p in plans if p.ref.slot == "lat_pulldown")
    scheduled = sess.scheduled_calibrations(config, config.seed, timeline, A, info)
    in_force, _ = sess.session_calibrations(config, config.seed, plan, scheduled)
    session = sess.build_session(
        config, plan, day=info, device_plan=plan_device_day(config, config.seed, A, info),
        gravity_environment=GravityEnvironment.EARTH_1G, calibration_ids={s: r.calibration_id for s, r in in_force.items()},
    )  # fmt: skip
    payload = dump(session)
    assert validation.errors("domain/session", payload) == []
    assert (payload["planned_sets"], payload["reps_per_set"], payload["planned_reps"]) == (4, 15, 60)
    assert "phase" not in payload and "related_session_id" not in payload
    events = payload["events"]
    sets = [(e["event_type"], e["set_index"]) for e in events if e["event_type"] in ("set_start", "set_end")]
    assert sets == [(kind, i) for i in range(1, 5) for kind in ("set_start", "set_end")]
    reps = [e for e in events if e["event_type"] == "rep_start"]
    assert [e["rep_index"] for e in reps] == list(range(1, 61))
    assert [e["set_index"] for e in reps] == [s for s in range(1, 5) for _ in range(15)]
    assert [e["event_type"] for e in events if e["event_type"].startswith("rest")] == ["rest_start", "rest_end"] * 3
    assert events[0]["event_type"] == "set_start" and events[1]["event_type"] == "rep_start"
    stamps = [e["timestamp"] for e in events]
    assert stamps == sorted(stamps)


def test_spine_checks_bracket_the_exercise_and_link_to_it(config, timeline):
    for day in (0, 5, 12):
        plans = {p.ref.slot: p for p in sess.plan_day(config, config.seed, A, timeline.day(day))}
        exercise, pre, post = plans["lat_pulldown"], plans["pre_spine_check"], plans["post_spine_check"]
        assert pre.end <= exercise.start and exercise.start - pre.end <= timedelta(minutes=2)
        assert post.start >= exercise.end and post.start - exercise.end <= timedelta(minutes=2)
        assert (pre.phase, post.phase, exercise.phase) == ("pre", "post", None)
        assert pre.related_session_id == post.related_session_id == exercise.session_id
        assert "am_spine_check" not in plans and "pm_spine_check" not in plans


def test_spine_check_records_validate_with_phase_and_related_session(config, timeline):
    info = timeline.day(1)
    plans = {p.ref.slot: p for p in sess.plan_day(config, config.seed, A, info)}
    for slot, phase in (("pre_spine_check", "pre"), ("post_spine_check", "post")):
        plan = plans[slot]
        scheduled = sess.scheduled_calibrations(config, config.seed, timeline, A, info)
        in_force, _ = sess.session_calibrations(config, config.seed, plan, scheduled)
        payload = dump(sess.build_session(
            config, plan, day=info, device_plan=plan_device_day(config, config.seed, A, info),
            gravity_environment=info.gravity_environment, calibration_ids={s: r.calibration_id for s, r in in_force.items()},
        ))  # fmt: skip
        assert validation.errors("domain/session", payload) == []
        assert payload["phase"] == phase and payload["related_session_id"] == plans["lat_pulldown"].session_id
        assert "planned_sets" not in payload
    background = dump(sess.build_session(
        config, plans["background"], day=info, device_plan=plan_device_day(config, config.seed, A, info),
        gravity_environment=info.gravity_environment, calibration_ids={},
    ))  # fmt: skip
    assert "phase" not in background and "related_session_id" not in background


def test_bracketing_spine_check_is_skipped_when_its_exercise_is_not_planned():
    def edit(raw):
        for s in raw["schedule"]["sessions"]:
            if s["name"] == "lat_pulldown":
                s["every_n_days"] = 2

    config = make_config(edit)
    timeline = MissionTimeline(config)
    odd = [p.ref.slot for p in sess.plan_day(config, config.seed, A, timeline.day(1))]
    assert "pre_spine_check" not in odd and "post_spine_check" not in odd and "lat_pulldown" not in odd
    even = [p.ref.slot for p in sess.plan_day(config, config.seed, A, timeline.day(2))]
    assert {"pre_spine_check", "post_spine_check", "lat_pulldown"} <= set(even)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda d: d["schedule"]["sessions"][0].update(time_utc="07:00", start_jitter_s=5.0), "bracket"),
        (lambda d: d["schedule"]["sessions"][0]["bracket"].update(of="nope"), "bracket"),
        (lambda d: d["schedule"]["sessions"][0]["bracket"].update(phase="mid"), "pre"),
    ],
)
def test_invalid_bracket_fails(edit, message):
    with pytest.raises(ValueError, match=message):
        make_config(edit)


def test_both_emg_sides_get_their_own_mvc_calibration(config, timeline):
    plans = sess.plan_day(config, config.seed, A, timeline.day(0))
    exercise = next(p for p in plans if p.session_type == "exercise")
    mvc = sess.session_mvc_calibrations(config, config.seed, exercise)
    sides = {s: dump(r.model)["side"] for s, r in mvc.items()}
    assert sides == {"emg_lat_left": "left", "emg_lat_right": "right"}
    assert len({r.calibration_id for r in mvc.values()}) == 2
