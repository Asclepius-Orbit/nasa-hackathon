import json
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import yaml
from spine_contracts import GravityEnvironment, QualityFlag, StreamKind
from spine_physiology import sessions as sess
from spine_physiology import truth
from spine_physiology.config import MissionConfig
from spine_physiology.device import ChunkOutcome, plan_device_day
from spine_physiology.faults import FaultEpisode
from spine_physiology.signals import emg as emg_signal
from spine_physiology.signals.imu import effective_rom_deg
from spine_physiology.state import LatentModel, MissionTimeline

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
CATALOG = Path(__file__).resolve().parents[2] / "contracts" / "catalog" / "feature-catalog.json"
A = "AST-01"


def make_config(clean: bool = False) -> MissionConfig:
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    if clean:
        for fault in raw["faults"]["quality"].values():
            fault["probability"] = 0.0
    return MissionConfig.model_validate(raw)


@pytest.fixture(scope="module")
def config() -> MissionConfig:
    return make_config(clean=True)


@pytest.fixture(scope="module")
def model(config):
    timeline = MissionTimeline(config)
    return timeline, LatentModel(config, timeline, config.seed)


def synth(config, model, slot, day=0, gravity=None):
    timeline, latents = model
    info = timeline.day(day)
    plan = next(p for p in sess.plan_day(config, config.seed, A, info) if p.ref.slot == slot)
    scheduled = sess.scheduled_calibrations(config, config.seed, timeline, A, info)
    in_force, _ = sess.session_calibrations(config, config.seed, plan, scheduled)
    state = latents.state(A, day)
    spec = sess.mission_spec(config, plan, day=info, gravity_environment=gravity or info.gravity_environment)
    synthesis = sess.synthesize(config, config.seed, spec, member=config.member(A), state=state, calibrations=in_force)
    return plan, state, synthesis


def catalog_patterns() -> list[re.Pattern[str]]:
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))["entries"]
    return [re.compile("^" + re.sub(r"\\\{[a-z_]+\\\}", "[a-z0-9_]+", re.escape(e["feature_id"])) + "$") for e in entries]


def by_id(features):
    return {f["feature_id"]: f for f in features}


def test_exercise_features_use_catalog_ids_and_units(config, model):
    _plan, state, synthesis = synth(config, model, "lat_pulldown")
    features = truth.expected_features(config, synthesis, state=state)
    patterns = catalog_patterns()
    units = {e["feature_id"].rsplit(".", 1)[1]: e["unit"] for e in json.loads(CATALOG.read_text(encoding="utf-8"))["entries"]}
    for f in features:
        assert any(p.match(f["feature_id"]) for p in patterns), f["feature_id"]
        assert f["unit"] == units[f["feature_id"].rsplit(".", 1)[1]]
    ids = by_id(features)
    expected = {
        "emg.latissimus_dorsi.left.rms_pct_mvc", "emg.latissimus_dorsi.right.peak_pct_mvc", "emg.latissimus_dorsi.left.median_freq_hz",
        "emg.latissimus_dorsi.left.median_freq_slope_hz_per_rep", "emg.latissimus_dorsi.symmetry_index_pct",
        "imu.t9.sagittal.rom_deg", "imu.t9.sagittal.peak_angular_velocity_dps", "stretch.full_spine.peak_elongation_mm",
        "stretch.full_spine.range_mm", "exercise.rep_count",
    }  # fmt: skip
    assert expected <= set(ids)
    protocol = config.protocol("lat_pulldown_standing_v1")
    assert ids["exercise.rep_count"]["value"] == protocol.planned_reps
    assert ids["imu.t9.sagittal.rom_deg"]["value"] == pytest.approx(effective_rom_deg(protocol.rom_deg, state, config.signals.imu))
    peak = emg_signal.effective_pct_mvc(protocol.pct_mvc, state.muscle_deconditioning, config.signals.emg)
    assert ids["emg.latissimus_dorsi.left.peak_pct_mvc"]["value"] == pytest.approx(peak, rel=1e-3)
    # Campana seno² hasta el pico desde el reposo: RMS entre el reposo y el pico.
    assert config.signals.emg.rest_pct_mvc < ids["emg.latissimus_dorsi.left.rms_pct_mvc"]["value"] < peak
    assert ids["emg.latissimus_dorsi.symmetry_index_pct"]["value"] == 0.0
    # La fatiga baja la frecuencia mediana rep a rep.
    assert ids["emg.latissimus_dorsi.left.median_freq_slope_hz_per_rep"]["value"] < 0


def test_expected_emg_rms_matches_the_synthesized_signal(config, model):
    plan, state, synthesis = synth(config, model, "lat_pulldown")
    stream = synthesis.stream("emg_lat_left")
    ids = by_id(truth.expected_features(config, synthesis, state=state))
    t = np.arange(stream.channels["emg"].size) / stream.rate_hz
    mask = np.zeros(t.size, dtype=bool)
    for rep in plan.reps:
        mask |= (t >= rep.start_s) & (t < rep.end_s)
    measured = emg_signal.rms(stream.channels["emg"][mask])
    assert measured == pytest.approx(ids["emg.latissimus_dorsi.left.rms_mv"]["value"], rel=0.05)
    first = plan.reps[0]
    rep = stream.channels["emg"][(t >= first.start_s) & (t < first.end_s)]
    assert emg_signal.median_frequency(rep, stream.rate_hz) == pytest.approx(ids["emg.latissimus_dorsi.left.median_freq_hz"]["value"], rel=0.2)


def test_spine_check_elongation_matches_the_published_length(config, model):
    _, state, synthesis = synth(config, model, "pre_spine_check", day=6)
    ids = by_id(truth.expected_features(config, synthesis, state=state))
    stretch = synthesis.stream("stretch_spine")
    rest = config.member(A).baseline.cord_rest_length_mm
    measured = float(np.mean(stretch.channels["length_mm"])) - rest
    # Error: calibración medida (ruido de 0.5 mm por punto) + cuantización del ADC.
    assert measured == pytest.approx(ids["stretch.full_spine.elongation_mm"]["value"], abs=2.0)
    assert ids["stretch.full_spine.elongation_mm"]["value"] == pytest.approx(state.spine_elongation_mm, abs=1.0)  # micro: sin pérdida diurna
    assert ids["stretch.full_spine.elongation_pct"]["value"] == pytest.approx(100 * ids["stretch.full_spine.elongation_mm"]["value"] / rest)
    assert "imu.t9.sagittal.rom_deg" not in ids and "exercise.rep_count" not in ids


def test_gravity_changes_only_gravity_dependent_features(config, model):
    _, state, micro = synth(config, model, "lat_pulldown", day=6)
    _, _, earth = synth(config, model, "lat_pulldown", day=6, gravity=GravityEnvironment.EARTH_1G)
    a, b = by_id(truth.expected_features(config, micro, state=state)), by_id(truth.expected_features(config, earth, state=state))
    assert set(a) == set(b)
    for feature_id in a:
        if feature_id.startswith("stretch."):
            assert a[feature_id]["value"] != b[feature_id]["value"] or feature_id.endswith("range_mm")
        else:
            assert a[feature_id] == b[feature_id]
    # La pérdida diurna (solo con gravedad) acorta el cordón.
    assert b["stretch.full_spine.peak_elongation_mm"]["value"] < a["stretch.full_spine.peak_elongation_mm"]["value"]


def test_background_features_only_count_emitted_chunks(config, model):
    plan, state, synthesis = synth(config, model, "background")
    active = sess.active_chunks(plan)
    with_pause = by_id(truth.expected_features(config, synthesis, state=state, active=active))
    stretch = synthesis.stream("stretch_spine")
    emitted = sum(k1 - k0 for k0, k1 in active) * stretch.samples_per_chunk
    assert with_pause["stretch.full_spine.elongation_mm"]["n"] == emitted < stretch.channels["adc_counts"].size


def session_key(config):
    return truth.TruthSession(A, 0, "11111111-2222-4333-8444-555555555555", "lat_pulldown", "lat_pulldown_standing_v1", GravityEnvironment.EARTH_1G)


def test_quality_faults_record_lists_visible_episodes_with_times(config, model):
    plan, _, synthesis = synth(config, model, "background")
    stream = synthesis.stream("imu_t9")
    inside = FaultEpisode(QualityFlag.LOW_SNR, "imu_t9", 0, 5, 0.6, 1.0)
    paused_chunk = sess.active_chunks(plan)[0][1]
    hidden = FaultEpisode(QualityFlag.CLIPPING, "imu_t9", paused_chunk * stream.samples_per_chunk, (paused_chunk + 1) * stream.samples_per_chunk, 0.4, 0.6)
    patched = sess.StreamSynthesis(**{**{f: getattr(stream, f) for f in stream.__slots__}, "episodes": (inside, hidden)})
    record = truth.quality_faults_record(session_key(config), [patched], active=sess.active_chunks(plan))
    assert record["type"] == "quality_faults" and record["session_id"] == session_key(config).session_id
    (episode,) = record["streams"]["imu_t9"]
    assert episode["flag"] == "low_snr" and episode["start"] == truth.iso(stream.start)
    assert len(truth.quality_faults_record(session_key(config), [patched])["streams"]["imu_t9"]) == 2
    json.dumps(record)


def test_lost_chunks_record_keeps_only_lost_outcomes(config):
    at = datetime(2026, 11, 1, 10, tzinfo=UTC)
    lost = ChunkOutcome("imu_t9", StreamKind.IMU, 7, 12, 1200, at, None)
    record = truth.lost_chunks_record(session_key(config), {"imu_t9": [lost], "stretch_spine": []})
    assert record["streams"] == {"imu_t9": [{"boot_id": 7, "seq": 12, "t0_device_ms": 1200, "received_at": "2026-11-01T10:00:00Z"}], "stretch_spine": []}


def test_daily_records_are_json_ready(config, model):
    timeline, latents = model
    day = timeline.day(4)
    latent = truth.latent_record(latents.state(A, 4), day, latents.activity(A, 4))
    assert latent["type"] == "latent_state" and latent["mission_day"] == 1 and latent["gravity_environment"] == "microgravity"
    assert set(latent["latents"]) == {"spine_elongation_mm", "muscle_deconditioning", "rom_motor_control", "pain_stiffness"}
    device = truth.device_record(plan_device_day(config, config.seed, A, day), day)
    assert device["type"] == "device_events" and device["boots"][0]["boot_at"] == device["power_on"]
    json.dumps([latent, device])
