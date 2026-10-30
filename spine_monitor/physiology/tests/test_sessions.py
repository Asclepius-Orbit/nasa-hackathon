import itertools
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
import yaml
from spine_contracts import (
    GravityEnvironment,
    QualityFlag,
    StreamKind,
    dump,
    validation,
)
from spine_physiology import sessions as sess
from spine_physiology.config import MissionConfig
from spine_physiology.device import plan_device_day
from spine_physiology.rng import RngTree
from spine_physiology.signals import rep_schedule
from spine_physiology.state import LatentModel, MissionTimeline

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
A = "AST-01"


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


def test_plan_day_lists_scheduled_sessions_and_background_sorted(config, timeline):
    day = timeline.day(0)
    plans = sess.plan_day(config, config.seed, A, day)
    assert [p.ref.slot for p in plans] == ["am_spine_check", "background", "lat_pulldown", "pm_spine_check"]
    assert [p.start for p in plans] == sorted(p.start for p in plans)
    for plan in plans:
        assert plan.ref == sess.SessionRef(A, 0, plan.ref.slot)
        assert plan.session_id == RngTree(config.seed).uuid(A, 0, plan.ref.slot)
        # Duración entera en chunks.
        assert (plan.end - plan.start) % timedelta(milliseconds=config.signals.chunk_ms) == timedelta(0)
    exercise = next(p for p in plans if p.ref.slot == "lat_pulldown")
    assert exercise.session_type == "exercise" and exercise.reps == rep_schedule(config.protocol("lat_pulldown_standing_v1"))
    assert abs((exercise.start - day.at(config.schedule.sessions[1].time_utc)).total_seconds()) < 5 * 600
    background = next(p for p in plans if p.background)
    assert background.session_type == "spine_check" and background.stream_ids == ("imu_l3", "stretch_lumbar")
    assert background.pauses == ((exercise.start, exercise.end),)


def test_plan_day_is_deterministic_and_depends_on_astronaut_and_day(config, timeline):
    a = sess.plan_day(config, config.seed, A, timeline.day(2))
    assert a == sess.plan_day(config, config.seed, A, timeline.day(2))
    assert a != sess.plan_day(config, config.seed, "AST-02", timeline.day(2))
    assert [p.start.time() for p in a] != [p.start.time() for p in sess.plan_day(config, config.seed, A, timeline.day(3))]


def test_plan_day_respects_phases_every_n_days_and_disabled_background():
    def edit(raw):
        raw["schedule"]["sessions"][0]["phases"] = ["inflight"]
        raw["schedule"]["sessions"][1]["every_n_days"] = 2
        raw["schedule"]["background"]["enabled"] = False

    config = make_config(edit)
    timeline = MissionTimeline(config)
    slots = {d.index: [p.ref.slot for p in sess.plan_day(config, config.seed, A, d)] for d in timeline}
    assert "am_spine_check" not in slots[0] and "am_spine_check" in slots[3]
    assert "lat_pulldown" in slots[0] and "lat_pulldown" not in slots[1] and "lat_pulldown" in slots[2]
    assert all("background" not in s for s in slots.values())


def test_overlapping_sessions_are_shifted_after_the_previous_one():
    def edit(raw):
        for s in raw["schedule"]["sessions"]:
            s["time_utc"] = "10:00"
            s["start_jitter_s"] = 0.0

    config = make_config(edit)
    plans = [p for p in sess.plan_day(config, config.seed, A, MissionTimeline(config).day(0)) if not p.background]
    assert [p.ref.slot for p in plans] == ["am_spine_check", "lat_pulldown", "pm_spine_check"]
    for before, after in itertools.pairwise(plans):
        assert after.start == before.end


def test_sessions_stay_inside_the_device_power_window():
    def edit(raw):
        sessions = raw["schedule"]["sessions"]
        sessions[0].update(time_utc="06:30", start_jitter_s=3600.0)
        sessions[2].update(time_utc="22:29", start_jitter_s=3600.0)

    config = make_config(edit)
    timeline = MissionTimeline(config)
    chunk = timedelta(milliseconds=config.signals.chunk_ms)
    for day in timeline:
        on, off = day.at(config.device.power_on_time_utc), day.at(config.device.power_off_time_utc)
        for plan in sess.plan_day(config, config.seed, A, day):
            assert on <= plan.start < plan.end <= off
            assert (plan.end - plan.start) % chunk == timedelta(0)


def test_background_active_chunks_skip_the_pause_on_the_chunk_grid(config, timeline):
    plans = sess.plan_day(config, config.seed, A, timeline.day(0))
    background = next(p for p in plans if p.background)
    exercise = next(p for p in plans if p.ref.slot == "lat_pulldown")
    chunk = timedelta(milliseconds=config.signals.chunk_ms)
    (a0, a1), (b0, b1) = sess.active_chunks(config, background)
    assert a0 == 0 and b1 == sess.n_chunks(config, background)
    assert background.start + a1 * chunk <= exercise.start < background.start + (a1 + 1) * chunk
    assert background.start + (b0 - 1) * chunk < exercise.end <= background.start + b0 * chunk
    assert sess.active_chunks(config, exercise) == ((0, sess.n_chunks(config, exercise)),)


def test_scheduled_calibrations_follow_every_n_days_and_segment_starts(config, timeline):
    # every_n_days 7 y tramos de 3/8/3 días: calibraciones los días 0, 3 (inicio de tramo), 7, 11 (inicio de tramo).
    ids = {d.index: sess.scheduled_calibrations(config, config.seed, timeline, A, d) for d in timeline}
    stretch = [ids[d]["stretch_lumbar"].calibration_id for d in range(len(timeline))]
    changes = [d for d in range(len(timeline)) if d == 0 or stretch[d] != stretch[d - 1]]
    assert changes == [0, 3, 7, 11]
    assert all(ids[d]["stretch_lumbar"].day == max(c for c in changes if c <= d) for d in ids)
    assert set(ids[0]) == {"imu_l3", "stretch_lumbar"}
    record = ids[3]["stretch_lumbar"]
    host = next(p for p in sess.plan_day(config, config.seed, A, timeline.day(3)) if "stretch_lumbar" in p.stream_ids)
    assert record.session_id == host.session_id and record.created_at == host.start


@pytest.mark.parametrize("stream_id", ["stretch_lumbar", "imu_l3"])
def test_scheduled_calibrations_validate(config, timeline, stream_id):
    record = sess.scheduled_calibrations(config, config.seed, timeline, A, timeline.day(0))[stream_id]
    payload = dump(record.model)
    assert validation.errors("domain/calibration", payload) == []
    assert payload["source"] == "simulator"
    if stream_id == "stretch_lumbar":
        points = payload["curve"]["points"]
        assert payload["rest_resistance_ohm"] == points[0]["resistance_ohm"]
        assert payload["rest_length_mm"] == config.member(A).baseline.cord_rest_length_mm


def test_exercise_sessions_record_an_mvc_per_emg_stream(config, timeline):
    plans = sess.plan_day(config, config.seed, A, timeline.day(0))
    exercise = next(p for p in plans if p.session_type == "exercise")
    mvc = sess.session_mvc_calibrations(config, config.seed, exercise)
    assert set(mvc) == {"emg_lat_left", "emg_lat_right"}
    for stream_id, record in mvc.items():
        payload = dump(record.model)
        assert validation.errors("domain/calibration", payload) == []
        true = config.member(A).baseline.emg_mvc_rms_mv[stream_id]
        assert payload["mvc_rms_mv"] == pytest.approx(true, rel=5 * config.calibration.emg_mvc.measurement_noise_rel)
        assert payload["session_id"] == exercise.session_id
    spine_check = next(p for p in plans if p.ref.slot == "am_spine_check")
    assert sess.session_mvc_calibrations(config, config.seed, spine_check) == {}


def _session_payload(config, timeline, slot, day=0, gravity=None):
    info = timeline.day(day)
    plans = sess.plan_day(config, config.seed, A, info)
    plan = next(p for p in plans if p.ref.slot == slot)
    scheduled = sess.scheduled_calibrations(config, config.seed, timeline, A, info)
    in_force, _ = sess.session_calibrations(config, config.seed, plan, scheduled)
    session = sess.build_session(
        config, plan, day=info, device_plan=plan_device_day(config, config.seed, A, info),
        gravity_environment=gravity or info.gravity_environment, calibration_ids={s: r.calibration_id for s, r in in_force.items()},
    )  # fmt: skip
    return plan, in_force, dump(session)


@pytest.mark.parametrize("slot", ["am_spine_check", "lat_pulldown", "background"])
def test_session_records_validate_and_declare_streams(config, timeline, slot):
    plan, in_force, payload = _session_payload(config, timeline, slot)
    assert validation.errors("domain/session", payload) == []
    assert payload["source"] == "simulator" and payload["session_id"] == plan.session_id
    assert payload["mission_context"] == {"mission_day": -3, "phase": "preflight", "gravity_environment": "earth_1g"}
    declared = {s["stream_id"]: s for s in payload["streams"]}
    assert list(declared) == list(plan.stream_ids)
    for stream_id, declaration in declared.items():
        assert declaration["calibration_id"] == in_force[stream_id].calibration_id
        assert declaration["placement"] == config.device.stream(stream_id).placement.model_dump(mode="json", exclude_none=True)
    if slot == "background":
        assert declared["imu_l3"]["sample_rate_hz"] == config.schedule.background.sample_rates_hz.imu
        assert [e["note"] for e in payload["events"]] == ["background paused", "background resumed"]


def test_exercise_session_has_load_and_rep_events_with_device_clock(config, timeline):
    _plan, _, payload = _session_payload(config, timeline, "lat_pulldown")
    protocol = config.protocol("lat_pulldown_standing_v1")
    assert payload["load_declared"] == {"type": "elastic_band", "nominal_kg": 15.0, "band_stretch_cm": 40.0, "band_id": "blue"}
    assert payload["planned_reps"] == protocol.planned_reps
    events = payload["events"]
    assert [e["event_type"] for e in events[:2]] == ["rep_start", "rep_end"]
    assert [e["rep_index"] for e in events if e["event_type"] == "rep_start"] == list(range(1, protocol.planned_reps + 1))
    assert all("device_ms" in e and "boot_id" in e for e in events)


def test_session_gravity_override_changes_only_the_mission_context(config, timeline):
    _, _, earth = _session_payload(config, timeline, "lat_pulldown")
    _, _, micro = _session_payload(config, timeline, "lat_pulldown", gravity=GravityEnvironment.MICROGRAVITY)
    assert micro["mission_context"]["gravity_environment"] == "microgravity"
    micro["mission_context"]["gravity_environment"] = "earth_1g"
    assert micro == earth


def _synth(config, slot, *, day=0, gravity=None):
    timeline = MissionTimeline(config)
    info = timeline.day(day)
    plan = next(p for p in sess.plan_day(config, config.seed, A, info) if p.ref.slot == slot)
    scheduled = sess.scheduled_calibrations(config, config.seed, timeline, A, info)
    in_force, _ = sess.session_calibrations(config, config.seed, plan, scheduled)
    state = LatentModel(config, timeline, config.seed).state(A, day)
    spec = sess.mission_spec(config, plan, day=info, gravity_environment=gravity or info.gravity_environment)
    return plan, sess.synthesize(config, config.seed, spec, member=config.member(A), state=state, calibrations=in_force)


def _clean(raw):
    for fault in raw["faults"]["quality"].values():
        fault["probability"] = 0.0


def test_synthesis_shapes_channels_and_calibration(config):
    plan, synthesis = _synth(config, "lat_pulldown")
    n_chunks = sess.n_chunks(config, plan)
    assert [s.stream_id for s in synthesis.streams] == list(plan.stream_ids)
    for stream in synthesis.streams:
        lengths = {len(a) for a in stream.channels.values()}
        assert lengths == {n_chunks * stream.samples_per_chunk}
        assert len(stream.quality) == n_chunks
    stretch = synthesis.stream("stretch_lumbar")
    assert set(stretch.channels) == {"adc_counts", "resistance_ohm", "length_mm"}
    assert stretch.calibration_id is not None and stretch.channels["adc_counts"].dtype == np.int64
    assert synthesis.stream("emg_lat_left").calibration_id is None  # la MVC va en Session.streams, no en el chunk


def test_synthesis_is_deterministic(config):
    _, a = _synth(config, "lat_pulldown")
    _, b = _synth(config, "lat_pulldown")
    for sa, sb in zip(a.streams, b.streams, strict=True):
        for name in sa.channels:
            assert np.array_equal(sa.channels[name], sb.channels[name])
        assert sa.quality == sb.quality and sa.episodes == sb.episodes


def test_cord_marks_band_recovering_after_the_reps_but_not_at_rest():
    config = make_config(_clean)
    plan, synthesis = _synth(config, "lat_pulldown")
    stretch = synthesis.stream("stretch_lumbar")
    flagged = [QualityFlag.BAND_RECOVERING in q["length_mm"].flags for q in stretch.quality]
    first_rep_chunk = int(plan.reps[0].start_s * 1000 // config.signals.chunk_ms)
    assert not any(flagged[:first_rep_chunk])  # antes de estirar no hay residuo
    assert flagged[-1]  # lead_out de 120 s con tau 90 s: aún se recupera al final
    assert all(QualityFlag.BAND_RECOVERING not in q["adc_counts"].flags for q in stretch.quality)
    _, rest = _synth(config, "am_spine_check")
    assert all(not q["length_mm"].flags for q in rest.stream("stretch_lumbar").quality)


def test_microgravity_removes_gravity_from_accel_and_keeps_everything_else():
    config = make_config(_clean)
    _, earth = _synth(config, "am_spine_check", day=4, gravity=GravityEnvironment.EARTH_1G)
    _, micro = _synth(config, "am_spine_check", day=4)
    e, m = earth.stream("imu_l3").channels, micro.stream("imu_l3").channels
    accel = lambda c: np.sqrt(c["accel_x"] ** 2 + c["accel_y"] ** 2 + c["accel_z"] ** 2)
    assert np.mean(accel(e)) == pytest.approx(1.0, abs=0.05)
    assert np.mean(accel(m)) < 0.1
    for gyro in ("gyro_x", "gyro_y", "gyro_z"):
        assert np.array_equal(e[gyro], m[gyro])
    # Pérdida diurna del cordón solo con gravedad: más largo en micro.
    assert np.mean(micro.stream("stretch_lumbar").truth["length_mm"]) > np.mean(earth.stream("stretch_lumbar").truth["length_mm"])


def test_background_uses_reduced_rates_and_shared_bends(config):
    _plan, synthesis = _synth(config, "background")
    imu, stretch = synthesis.stream("imu_l3"), synthesis.stream("stretch_lumbar")
    assert imu.rate_hz == config.schedule.background.sample_rates_hz.imu
    assert stretch.rate_hz == config.schedule.background.sample_rates_hz.stretch
    # Mismas flexiones en IMU y cordón (mismo tronco): el ángulo coincide a igual frecuencia.
    assert np.array_equal(imu.truth["angle_deg"], stretch.truth["angle_deg"])
    assert imu.truth["angle_deg"].max() > 0  # 6 flexiones/h en una hora


def test_stream_rate_rejects_emg_in_background(config):
    with pytest.raises(ValueError, match="IMU and cord only"):
        sess.stream_rate(config, "emg_lat_left", background=True)
    assert sess.stream_rate(config, "emg_lat_left", background=False) == config.signals.emg.sample_rate_hz
    assert config.device.stream("imu_l3").stream_kind is StreamKind.IMU
