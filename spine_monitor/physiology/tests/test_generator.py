import itertools
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import yaml
from spine_contracts import GravityEnvironment, topics, validation
from spine_physiology import MissionGenerator, SessionRef
from spine_physiology.config import MissionConfig, load_config
from spine_physiology.device import SimDevice, plan_device_live
from spine_physiology.envelope import Envelope, EnvelopeKind

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"

SCHEMAS = {
    EnvelopeKind.STREAM_CHUNK: "telemetry/stream-chunk",
    EnvelopeKind.DEVICE_STATUS: "telemetry/device-status",
    EnvelopeKind.CLOCK_SYNC: "telemetry/clock-sync",
    EnvelopeKind.SESSION: "domain/session",
    EnvelopeKind.CALIBRATION: "domain/calibration",
    EnvelopeKind.SELF_REPORT: "domain/self-report",
    EnvelopeKind.DAILY_CONTEXT: "domain/daily-context",
    EnvelopeKind.PROFILE: "domain/astronaut-profile",
}


def tiny_raw() -> dict:
    """Misión de ejemplo encogida para tests: 4 días en 3 tramos, encendido 40 min, sesiones y fondo cortos,
    chunks de 500 ms y fallos frecuentes (la validación JSON Schema cuesta ~2 ms por chunk)."""
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw["start_mission_day"] = -1
    raw["segments"] = [
        {"days": 1, "phase": "preflight", "gravity_environment": "earth_1g"},
        {"days": 2, "phase": "inflight", "gravity_environment": "microgravity"},
        {"days": 1, "phase": "postflight", "gravity_environment": "earth_1g"},
    ]
    raw["device"].update(power_on_time_utc="09:00", power_off_time_utc="09:40")
    raw["protocols"]["lat_pulldown_standing_v1"].update(lead_in_s=2.0, planned_reps=3, rep_duration_s=2.0, rest_between_reps_s=1.0, lead_out_s=6.0)
    raw["protocols"]["spine_length_rest_v1"]["duration_s"] = 5.0
    times = {"am_spine_check": "09:05", "lat_pulldown": "09:20", "pm_spine_check": "09:35"}
    for s in raw["schedule"]["sessions"]:
        s.update(time_utc=times[s["name"]], start_jitter_s=20.0)
    raw["schedule"]["background"].update(start_time_utc="09:19", end_time_utc="09:22")
    raw["signals"]["chunk_ms"] = 500
    raw["calibration"]["stretch"]["every_n_days"] = 2
    raw["calibration"]["imu"]["every_n_days"] = 2
    for fault in raw["faults"]["quality"].values():
        fault["probability"] = min(1.0, fault["probability"] * 50)
    raw["faults"]["device"]["packet_loss"]["probability"] = 0.03
    raw["faults"]["device"]["reboot"]["probability_per_hour"] = 1.0
    return raw


def tiny_config(edit=None) -> MissionConfig:
    raw = tiny_raw()
    if edit:
        edit(raw)
    return MissionConfig.model_validate(raw)


@pytest.fixture(scope="module")
def config() -> MissionConfig:
    return tiny_config()


@pytest.fixture(scope="module")
def mission(config) -> list[Envelope]:
    return list(MissionGenerator(config).iter_messages())


def session_of(envelope: Envelope) -> str | None:
    return envelope.payload.get("session_id") if envelope.kind in (EnvelopeKind.STREAM_CHUNK, EnvelopeKind.SESSION, EnvelopeKind.CALIBRATION, EnvelopeKind.GROUND_TRUTH) else None


# --- Reproducibilidad -------------------------------------------------------------------------------


def test_same_seed_gives_identical_output_and_another_seed_differs(config, mission):
    assert list(MissionGenerator(config).day_messages(1)) == [m for m in mission if m.day == 1]
    other = list(MissionGenerator(config, seed=config.seed + 1).day_messages(1))
    assert [m.payload for m in other] != [m.payload for m in mission if m.day == 1]
    assert MissionGenerator(config).seed == config.seed and MissionGenerator(config, seed=7).seed == 7


def test_a_day_equals_its_slice_of_the_mission_whatever_the_consumption_order(config, mission):
    generator = MissionGenerator(config)
    late, early = generator.day_messages(3), generator.day_messages(0, "AST-02")
    # Consumo intercalado de dos días y de un astronauta suelto en el mismo generador.
    got3, got0 = [], []
    for a, b in itertools.zip_longest(late, early):
        if a is not None:
            got3.append(a)
        if b is not None:
            got0.append(b)
    assert got3 == [m for m in mission if m.day == 3]
    assert got0 == [m for m in mission if m.day == 0 and m.astronaut_id == "AST-02"]


def test_every_session_equals_its_slice_of_the_day(config, mission):
    generator = MissionGenerator(config)
    for day in (0, 2):
        for astronaut in ("AST-01", "AST-02"):
            day_msgs = [m for m in mission if m.day == day and m.astronaut_id == astronaut]
            for plan in reversed(generator.sessions(day, astronaut)):  # orden inverso a propósito
                alone = list(generator.session_messages(plan.ref))
                assert alone == [m for m in day_msgs if session_of(m) == plan.session_id], plan.ref
                assert Counter(m.kind for m in alone)[EnvelopeKind.SESSION] == 1


def test_unknown_session_or_day_fails_eagerly(config):
    generator = MissionGenerator(config)
    with pytest.raises(KeyError):
        generator.session_messages(SessionRef("AST-01", 0, "nope"))
    with pytest.raises(IndexError):
        generator.day_messages(99)
    with pytest.raises(KeyError):
        generator.day_messages(0, "AST-99")


# --- Misión: días, orden, validación ---------------------------------------------------------------


def test_days_follow_the_segments(config, mission):
    days = list(MissionGenerator(config).iter_days())
    assert [(d.index, d.mission_day, d.phase.value, d.gravity_environment.value) for d in days] == [
        (0, -1, "preflight", "earth_1g"), (1, 0, "inflight", "microgravity"), (2, 1, "inflight", "microgravity"), (3, 2, "postflight", "earth_1g"),
    ]  # fmt: skip
    for envelope in mission:
        if envelope.kind is EnvelopeKind.DAILY_CONTEXT:
            day = days[envelope.day]
            assert envelope.payload["date"] == day.date.isoformat() and envelope.payload["mission_day"] == day.mission_day
            assert envelope.payload["gravity_environment"] == day.gravity_environment.value
        if envelope.kind is EnvelopeKind.SESSION:
            context = envelope.payload["mission_context"]
            assert context["phase"] == days[envelope.day].phase.value and context["mission_day"] == days[envelope.day].mission_day
    assert Counter(m.kind for m in mission)[EnvelopeKind.DAILY_CONTEXT] == len(days) * len(config.crew)


def test_messages_are_chronological_and_profiles_come_first(config, mission):
    assert [m.kind for m in mission[:2]] == [EnvelopeKind.PROFILE, EnvelopeKind.PROFILE]
    assert all(a.at <= b.at for a, b in itertools.pairwise(mission))
    for envelope in mission:
        if envelope.day is not None:
            assert envelope.at.date() == config.start_date + timedelta(days=envelope.day)


def test_everything_emitted_validates_and_comes_from_the_simulator(mission):
    kinds = Counter(m.kind for m in mission)
    assert set(kinds) == set(SCHEMAS) | {EnvelopeKind.GROUND_TRUTH}
    for envelope in mission:
        if envelope.kind in SCHEMAS:
            assert validation.errors(SCHEMAS[envelope.kind], envelope.payload) == [], envelope
        if "source" in envelope.payload:
            assert envelope.payload["source"] == "simulator"
    kinds_of_chunks = Counter(m.payload["stream_kind"] for m in mission if m.kind is EnvelopeKind.STREAM_CHUNK)
    assert set(kinds_of_chunks) == {"emg", "imu", "stretch"}


def test_topics_and_retained_flags(config, mission):
    for envelope in mission:
        p = envelope.payload
        if envelope.kind is EnvelopeKind.STREAM_CHUNK:
            assert envelope.topic == topics.stream(p["astronaut_id"], p["device_id"], p["stream_id"]) and not envelope.retained
            assert envelope.at == datetime.fromisoformat(p["received_at"])
        elif envelope.kind is EnvelopeKind.DEVICE_STATUS:
            assert envelope.topic == topics.status(p["astronaut_id"], p["device_id"]) and envelope.retained
        elif envelope.kind is EnvelopeKind.CLOCK_SYNC:
            assert envelope.topic == topics.clock(p["astronaut_id"], p["device_id"]) and envelope.retained
        else:
            assert envelope.topic is None


def test_device_status_and_clock_cover_the_power_window(config, mission):
    statuses = [m for m in mission if m.kind is EnvelopeKind.DEVICE_STATUS and m.astronaut_id == "AST-01" and m.day == 0]
    window = 40 * 60 / config.device.status_interval_s
    assert len(statuses) == window + 1
    assert statuses[-1].payload["status"] == "offline"
    clocks = [m for m in mission if m.kind is EnvelopeKind.CLOCK_SYNC and m.astronaut_id == "AST-01" and m.day == 0]
    assert len(clocks) >= 40 * 60 / config.device.clock_sync_interval_s


def test_status_last_seq_never_runs_ahead_of_the_chunks(config, mission):
    sent: dict[tuple[str, int, str], list[tuple[datetime, int]]] = defaultdict(list)
    lost = {}
    for m in mission:
        if m.kind is EnvelopeKind.STREAM_CHUNK:
            sent[(m.astronaut_id, m.payload["boot_id"], m.payload["stream_id"])].append((m.at, m.payload["seq"]))
        if m.kind is EnvelopeKind.GROUND_TRUTH and m.payload["type"] == "lost_chunks":
            for stream_id, items in m.payload["streams"].items():
                for item in items:
                    lost[(m.astronaut_id, item["boot_id"], stream_id, item["seq"])] = datetime.fromisoformat(item["received_at"])
    latency = timedelta(milliseconds=config.faults.device.clock.latency_ms.max)
    checked = 0
    for m in mission:
        if m.kind is not EnvelopeKind.DEVICE_STATUS or "streams" not in m.payload:
            continue
        for entry in m.payload["streams"]:
            if "last_seq" not in entry:
                continue
            key = (m.astronaut_id, m.payload["boot_id"], entry["stream_id"])
            seq = entry["last_seq"]
            arrival = next((at for at, s in sent[key] if s == seq), None) or lost[(*key, seq)]
            assert arrival <= m.at + latency
            later = [s for at, s in sent[key] if at > m.at + latency]
            assert all(s > seq for s in later)
            checked += 1
    assert checked > 0


# --- Fondo, calibraciones, ground truth -------------------------------------------------------------


def test_background_pauses_during_a_session_and_seq_continues():
    def edit(raw):
        raw["faults"]["device"]["packet_loss"]["probability"] = 0.0
        raw["faults"]["device"]["reboot"]["probability_per_hour"] = 0.0

    config = tiny_config(edit)
    generator = MissionGenerator(config)
    plans = {p.ref.slot: p for p in generator.sessions(1, "AST-01")}
    background, exercise = plans["background"], plans["lat_pulldown"]
    assert background.start < exercise.start and exercise.end < background.end
    imu = [m.payload for m in generator.day_messages(1, "AST-01") if m.kind is EnvelopeKind.STREAM_CHUNK and m.payload["stream_id"] == "imu_l3"]
    ids = [p["session_id"] for p in imu]
    runs = [key for key, _ in itertools.groupby(ids)]
    assert runs[-4:] == [background.session_id, exercise.session_id, background.session_id, plans["pm_spine_check"].session_id]
    assert [p["seq"] for p in imu] == list(range(len(imu)))  # mismo stream_id, el seq sigue a través de las sesiones
    session_rates = {p["session_id"]: p["sample_rate_hz"] for p in imu}
    assert session_rates[background.session_id] == config.schedule.background.sample_rates_hz.imu
    assert session_rates[exercise.session_id] == config.signals.imu.sample_rate_hz
    # Ningún chunk del fondo cae dentro de la sesión.
    chunk = timedelta(milliseconds=config.signals.chunk_ms)
    for p in imu:
        if p["session_id"] == background.session_id:
            end = datetime.fromisoformat(p["received_at"]) - timedelta(milliseconds=config.faults.device.clock.latency_ms.max)
            assert not (exercise.start < end and end - chunk < exercise.end - chunk)


def test_length_mm_always_carries_the_calibration_in_force(config, mission):
    calibrations = {m.payload["calibration_id"]: m.payload for m in mission if m.kind is EnvelopeKind.CALIBRATION}
    declared = {}
    for m in mission:
        if m.kind is EnvelopeKind.SESSION:
            for stream in m.payload["streams"]:
                declared[(m.payload["session_id"], stream["stream_id"])] = stream.get("calibration_id")
    seen = 0
    for m in mission:
        if m.kind is EnvelopeKind.STREAM_CHUNK and m.payload["stream_kind"] == "stretch":
            p = m.payload
            assert "length_mm" in p["data"]
            calibration = calibrations[p["calibration_id"]]  # emitida antes en la misión
            assert calibration["calibration_type"] == "stretch" and calibration["created_at"] <= p["received_at"]
            assert declared[(p["session_id"], p["stream_id"])] == p["calibration_id"]
            seen += 1
    assert seen > 0


def test_calibration_schedule(config, mission):
    cals = [m for m in mission if m.kind is EnvelopeKind.CALIBRATION and m.astronaut_id == "AST-01"]
    days = {t: sorted({m.day for m in cals if m.payload["calibration_type"] == t}) for t in ("stretch", "imu", "emg_mvc")}
    assert days["stretch"] == days["imu"] == [0, 1, 2, 3]  # every 2 days + inicio de cada tramo
    assert days["emg_mvc"] == [0, 1, 2, 3]
    assert Counter(m.payload["calibration_type"] for m in cals)["emg_mvc"] == 2 * 4  # dos EMG por sesión de ejercicio


def test_ground_truth_records_are_present_and_consistent(config, mission):
    truth = [m for m in mission if m.kind is EnvelopeKind.GROUND_TRUTH]
    by_type = Counter(m.payload["type"] for m in truth)
    n_days, n_crew, n_sessions = 4, 2, 4
    assert by_type["latent_state"] == by_type["device_events"] == n_days * n_crew
    assert by_type["quality_faults"] == by_type["lost_chunks"] == by_type["expected_features"] == n_days * n_crew * n_sessions
    # Seq emitidos + perdidos = rango continuo por (astronauta, día, arranque, stream).
    seqs: dict[tuple, set[int]] = defaultdict(set)
    n_lost = 0
    for m in mission:
        if m.kind is EnvelopeKind.STREAM_CHUNK:
            seqs[(m.astronaut_id, m.day, m.payload["boot_id"], m.payload["stream_id"])].add(m.payload["seq"])
        elif m.kind is EnvelopeKind.GROUND_TRUTH and m.payload["type"] == "lost_chunks":
            for stream_id, items in m.payload["streams"].items():
                for item in items:
                    key = (m.astronaut_id, m.day, item["boot_id"], stream_id)
                    assert item["seq"] not in seqs[key]
                    seqs[key].add(item["seq"])
                    n_lost += 1
    assert n_lost > 0
    for key, values in seqs.items():
        assert values == set(range(len(values))), key
    faults = sum(len(e) for m in truth if m.payload["type"] == "quality_faults" for e in m.payload["streams"].values())
    assert faults > 0
    reboots = sum(len(m.payload["reboots"]) for m in truth if m.payload["type"] == "device_events")
    boots = {(m.astronaut_id, m.day, m.payload["boot_id"]) for m in mission if m.kind is EnvelopeKind.STREAM_CHUNK}
    assert len({(a, d) for a, d, _ in boots}) <= len(boots) <= n_days * n_crew + reboots


# --- Re-simulación A/B ------------------------------------------------------------------------------


def test_resimulate_changes_only_what_depends_on_gravity(config):
    generator = MissionGenerator(config)
    ref = next(p.ref for p in generator.sessions(1, "AST-01") if p.ref.slot == "lat_pulldown")
    micro = list(generator.session_messages(ref))
    earth = generator.resimulate(ref, GravityEnvironment.EARTH_1G)
    assert micro != earth
    assert [(m.kind, m.at, m.topic) for m in micro] == [(m.kind, m.at, m.topic) for m in earth]
    accel_changed = False
    for a, b in zip(micro, earth, strict=True):
        pa, pb = a.payload, b.payload
        if a.kind is EnvelopeKind.SESSION:
            assert pa["mission_context"]["gravity_environment"] == "microgravity" and pb["mission_context"]["gravity_environment"] == "earth_1g"
            assert {**pa, "mission_context": None} == {**pb, "mission_context": None}
        elif a.kind is EnvelopeKind.STREAM_CHUNK:
            same_header = {k: v for k, v in pa.items() if k not in ("data", "quality")}
            assert same_header == {k: v for k, v in pb.items() if k not in ("data", "quality")}
            if pa["stream_kind"] == "emg":
                assert pa == pb
            elif pa["stream_kind"] == "imu":
                for axis in ("gyro_x", "gyro_y", "gyro_z"):
                    assert pa["data"][axis] == pb["data"][axis]
                accel_changed |= pa["data"]["accel_x"] != pb["data"]["accel_x"]
        elif a.kind is EnvelopeKind.GROUND_TRUTH and pa["type"] == "expected_features":
            fa, fb = ({f["feature_id"]: f for f in p["features"]} for p in (pa, pb))
            assert {k: v for k, v in fa.items() if not k.startswith("stretch.")} == {k: v for k, v in fb.items() if not k.startswith("stretch.")}
            assert fa["stretch.lumbar.peak_elongation_mm"] != fb["stretch.lumbar.peak_elongation_mm"]
        elif a.kind is EnvelopeKind.GROUND_TRUTH and pa["type"] == "lost_chunks" or a.kind is EnvelopeKind.CALIBRATION:
            assert pa == pb
    assert accel_changed
    imu = lambda msgs: np.array([[m.payload["data"][c][0] for c in ("accel_x", "accel_y", "accel_z")] for m in msgs if m.kind is EnvelopeKind.STREAM_CHUNK and m.payload["stream_kind"] == "imu"])
    assert np.linalg.norm(imu(earth), axis=1).mean() > 0.8
    assert np.linalg.norm(imu(micro), axis=1).mean() < 0.3
    assert generator.resimulate(ref, GravityEnvironment.MICROGRAVITY) == micro


# --- API en vivo ------------------------------------------------------------------------------------

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
LIVE_SESSION = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
LIVE_BACKGROUND = "aaaaaaaa-bbbb-4ccc-8ddd-ffffffffffff"


def live_device(config) -> SimDevice:
    return SimDevice(config, plan_device_live(config, config.seed, "AST-01", boot_at=T0, horizon=timedelta(hours=1)))


def take_chunks(iterator, n: int) -> list[Envelope]:
    return list(itertools.islice((m for m in iterator if m.kind is EnvelopeKind.STREAM_CHUNK), n))


def test_live_background_then_session_then_background_keeps_seq():
    config = tiny_config(lambda raw: raw["faults"]["device"]["packet_loss"].update(probability=0.0))
    generator = MissionGenerator(config)
    device = live_device(config)
    background = take_chunks(generator.live_background(astronaut_id="AST-01", day=1, device=device, session_id=LIVE_BACKGROUND, start=T0 + timedelta(seconds=1)), 30)
    session = generator.live_session(
        astronaut_id="AST-01", day=1, device=device, session_id=LIVE_SESSION, session_type="exercise",
        protocol_id="lat_pulldown_standing_v1", stream_ids=[], start=T0 + timedelta(seconds=20),
    )  # fmt: skip
    first = next(session)
    assert first.kind is EnvelopeKind.GROUND_TRUTH and first.payload["type"] == "expected_features"
    in_session = take_chunks(session, 200)  # más que el protocolo: sigue en reposo
    resumed = take_chunks(generator.live_background(astronaut_id="AST-01", day=1, device=device, session_id=LIVE_BACKGROUND, start=T0 + timedelta(seconds=200)), 10)
    imu = [m.payload for m in background + in_session + resumed if m.payload["stream_id"] == "imu_l3"]
    assert [p["seq"] for p in imu] == list(range(len(imu)))
    assert {m.payload["session_id"] for m in in_session} == {LIVE_SESSION}
    assert {m.payload["stream_id"] for m in in_session} == set(config.protocol("lat_pulldown_standing_v1").streams)
    for m in background + in_session + resumed:
        assert validation.errors("telemetry/stream-chunk", m.payload) == []


def test_live_session_with_unknown_protocol_streams_at_rest_on_requested_streams(config):
    generator = MissionGenerator(config)
    chunks = take_chunks(
        generator.live_session(
            astronaut_id="AST-02", day=0, device=SimDevice(config, plan_device_live(config, config.seed, "AST-02", boot_at=T0, horizon=timedelta(hours=1))),
            session_id=LIVE_SESSION, session_type="spine_check", protocol_id="free_rest_v1", stream_ids=["stretch_lumbar"], start=T0,
        ),
        5,
    )  # fmt: skip
    assert {m.payload["stream_id"] for m in chunks} == {"stretch_lumbar"}
    with pytest.raises(KeyError):
        generator.live_session(
            astronaut_id="AST-01", day=0, device=live_device(config), session_id=LIVE_SESSION, session_type="spine_check",
            protocol_id="spine_length_rest_v1", stream_ids=["nope_stream"], start=T0,
        )  # fmt: skip


def test_live_calibration_validates_and_becomes_the_one_in_force(config):
    generator = MissionGenerator(config)
    at = T0 + timedelta(seconds=5)
    envelopes = generator.live_calibration(astronaut_id="AST-01", day=1, session_id=LIVE_SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"], at=at)
    (calibration,) = envelopes
    assert calibration.kind is EnvelopeKind.CALIBRATION and calibration.topic is None
    assert validation.errors("domain/calibration", calibration.payload) == []
    assert calibration.payload["session_id"] == LIVE_SESSION and calibration.payload["created_at"] == "2026-11-02T09:00:05Z"
    chunks = take_chunks(
        generator.live_session(
            astronaut_id="AST-01", day=1, device=live_device(config), session_id=LIVE_SESSION, session_type="spine_check",
            protocol_id="spine_length_rest_v1", stream_ids=[], start=at,
        ),
        6,
    )  # fmt: skip
    assert {m.payload["calibration_id"] for m in chunks if m.payload["stream_kind"] == "stretch"} == {calibration.payload["calibration_id"]}
    mvc = generator.live_calibration(astronaut_id="AST-01", day=1, session_id=LIVE_SESSION, calibration_type="emg_mvc", stream_ids=["emg_lat_left", "emg_lat_right"], at=at)
    assert [validation.errors("domain/calibration", m.payload) for m in mvc] == [[], []]
    with pytest.raises(ValueError, match="does not apply"):
        generator.live_calibration(astronaut_id="AST-01", day=1, session_id=LIVE_SESSION, calibration_type="imu", stream_ids=["emg_lat_left"], at=at)
    with pytest.raises(ValueError):
        generator.live_calibration(astronaut_id="AST-01", day=1, session_id=LIVE_SESSION, calibration_type="imu", stream_ids=[], at=at)


def test_live_blocks_after_midnight_keep_the_awake_window_of_the_mission_day(config):
    generator = MissionGenerator(config)
    info = generator._timeline.day(1)
    after_midnight = datetime.combine(info.date + timedelta(days=1), datetime.min.time(), tzinfo=UTC) + timedelta(minutes=5)
    for start in (T0, after_midnight):
        spec = generator._live_spec(("k",), start, 1, ("stretch_lumbar",), info, background=True, reps=(), protocol=None, residual={})
        assert spec.awake_from == info.at(config.device.power_on_time_utc)
        assert spec.awake_to == info.at(config.device.power_off_time_utc)


def test_live_packet_gap_survives_block_boundaries():
    def edit(raw):
        raw["device"]["status_interval_s"] = 0.5  # bloques de un chunk: cada chunk es frontera de bloque
        raw["faults"]["device"]["packet_loss"].update(probability=0.3, burst_chunks={"min": 1, "max": 1})

    config = tiny_config(edit)
    generator = MissionGenerator(config)
    chunks = take_chunks(generator.live_background(astronaut_id="AST-01", day=1, device=live_device(config), session_id=LIVE_BACKGROUND, start=T0), 150)
    last: dict[str, int] = {}
    jumps = 0
    for m in chunks:
        p = m.payload
        prev = last.get(p["stream_id"])
        if prev is not None and p["seq"] > prev + 1:
            jumps += 1
            states = p["quality"].values()
            assert any("packet_gap" in q["flags"] for q in states)
        last[p["stream_id"]] = p["seq"]
    assert jumps


def test_live_output_is_deterministic(config):
    def run():
        generator = MissionGenerator(config)
        return take_chunks(generator.live_background(astronaut_id="AST-01", day=2, device=live_device(config), session_id=LIVE_BACKGROUND, start=T0), 20)

    assert run() == run()


def test_example_config_profiles():
    generator = MissionGenerator(load_config(EXAMPLE))
    profiles = list(generator.profiles())
    assert [p.astronaut_id for p in profiles] == ["AST-01", "AST-02"]
    assert all(validation.errors("domain/astronaut-profile", p.payload) == [] and p.day is None for p in profiles)
    assert profiles[0].at == datetime(2026, 11, 1, tzinfo=UTC)
