import itertools
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import yaml
from spine_contracts import GravityEnvironment, topics, validation
from spine_physiology import MissionGenerator, SessionRef
from spine_physiology import sessions as sess
from spine_physiology.config import MissionConfig, load_config
from spine_physiology.device import SimDevice, plan_device_live
from spine_physiology.envelope import Envelope, EnvelopeKind
from spine_physiology.generator import calibration_envelope

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


@pytest.fixture(scope="module")
def sparse_config() -> MissionConfig:
    """stretch/imu cada 7 días: el día 2 (segundo del tramo inflight) usa las calibraciones del día 1."""

    def edit(raw):
        raw["calibration"]["stretch"]["every_n_days"] = 7
        raw["calibration"]["imu"]["every_n_days"] = 7

    return tiny_config(edit)


@pytest.fixture(scope="module")
def sparse_mission(sparse_config) -> list[Envelope]:
    return list(MissionGenerator(sparse_config).iter_messages())


def session_of(envelope: Envelope) -> str | None:
    kinds = (EnvelopeKind.STREAM_CHUNK, EnvelopeKind.SESSION, EnvelopeKind.CALIBRATION, EnvelopeKind.GROUND_TRUTH)
    return envelope.payload.get("session_id") if envelope.kind in kinds else None


def first_emissions(mission: list[Envelope]) -> dict[tuple[str, str], Envelope]:
    """(astronauta, calibration_id) -> sobre de la calibración tal como la emitió la misión completa."""
    out: dict[tuple[str, str], Envelope] = {}
    for m in mission:
        if m.kind is EnvelopeKind.CALIBRATION:
            assert (m.astronaut_id, m.payload["calibration_id"]) not in out  # iter_messages no repite calibraciones
            out[(m.astronaut_id, m.payload["calibration_id"])] = m
    return out


def unresolved_citations(messages: list[Envelope]) -> tuple[int, list[str]]:
    """(chunks con calibration_id, ids sin resolver en `messages`). Un chunk necesita su calibración emitida
    antes; un registro Session, en algún punto de la salida (la que se registra en esa sesión sale justo
    después del Session, en el mismo instante)."""
    everything = {(m.astronaut_id, m.payload["calibration_id"]) for m in messages if m.kind is EnvelopeKind.CALIBRATION}
    emitted: set[tuple[str, str]] = set()
    cited, missing = 0, []
    for m in messages:
        if m.kind is EnvelopeKind.CALIBRATION:
            emitted.add((m.astronaut_id, m.payload["calibration_id"]))
        elif m.kind is EnvelopeKind.SESSION:
            missing += [s["calibration_id"] for s in m.payload["streams"] if "calibration_id" in s and (m.astronaut_id, s["calibration_id"]) not in everything]
        elif m.kind is EnvelopeKind.STREAM_CHUNK and "calibration_id" in m.payload:
            cited += 1
            if (m.astronaut_id, m.payload["calibration_id"]) not in emitted:
                missing.append(m.payload["calibration_id"])
    return cited, missing


# --- Reproducibilidad -------------------------------------------------------------------------------


def test_same_seed_gives_identical_output_and_another_seed_differs(config, mission):
    assert list(MissionGenerator(config).day_messages(1, include_carried_calibrations=False)) == [m for m in mission if m.day == 1]
    other = list(MissionGenerator(config, seed=config.seed + 1).day_messages(1, include_carried_calibrations=False))
    assert [m.payload for m in other] != [m.payload for m in mission if m.day == 1]
    assert MissionGenerator(config).seed == config.seed and MissionGenerator(config, seed=7).seed == 7


def test_a_day_equals_its_slice_of_the_mission_whatever_the_consumption_order(sparse_config, sparse_mission):
    generator = MissionGenerator(sparse_config)
    mission = sparse_mission
    late, early = generator.day_messages(3, include_carried_calibrations=False), generator.day_messages(2, "AST-02", include_carried_calibrations=False)
    # Consumo intercalado de dos días y de un astronauta suelto en el mismo generador.
    got3, got0 = [], []
    for a, b in itertools.zip_longest(late, early):
        if a is not None:
            got3.append(a)
        if b is not None:
            got0.append(b)
    assert got3 == [m for m in mission if m.day == 3]
    assert got0 == [m for m in mission if m.day == 2 and m.astronaut_id == "AST-02"]


def test_every_session_equals_its_slice_of_the_day(sparse_config, sparse_mission):
    generator = MissionGenerator(sparse_config)
    for day in (0, 2):
        for astronaut in ("AST-01", "AST-02"):
            day_msgs = [m for m in sparse_mission if m.day == day and m.astronaut_id == astronaut]
            for plan in reversed(generator.sessions(day, astronaut)):  # orden inverso a propósito
                alone = list(generator.session_messages(plan.ref, include_carried_calibrations=False))
                assert alone == [m for m in day_msgs if session_of(m) == plan.session_id], plan.ref
                assert Counter(m.kind for m in alone)[EnvelopeKind.SESSION] == 1


def test_a_day_regenerated_alone_carries_the_calibrations_still_in_force_from_earlier_days(sparse_config, sparse_mission):
    generator = MissionGenerator(sparse_config)
    emitted = first_emissions(sparse_mission)
    streams = [s.stream_id for s in sparse_config.device.streams]
    carried_days, carried_types = set(), set()
    for day in generator.iter_days():
        for astronaut in (None, "AST-01"):
            crew = [astronaut] if astronaut else [m.astronaut_id for m in sparse_config.crew]
            alone = list(generator.day_messages(day.index, astronaut))
            exact = list(generator.day_messages(day.index, astronaut, include_carried_calibrations=False))
            assert exact == [m for m in sparse_mission if m.day == day.index and m.astronaut_id in crew]
            n = len(alone) - len(exact)
            carried = alone[:n]
            assert alone[n:] == exact  # primero las arrastradas, luego el día exacto
            in_force = [(a, r) for a in crew for s in streams if (r := generator.calibration_in_force(a, s, day.index)) is not None]
            assert {(m.astronaut_id, m.payload["calibration_id"]) for m in carried} == {(a, r.calibration_id) for a, r in in_force if r.day < day.index}
            assert len(carried) == len({(m.astronaut_id, m.payload["calibration_id"]) for m in carried})
            for m in carried:
                # Bit a bit el sobre que la misión emitió el día en que se registró (ids, created_at, at, day).
                assert m.kind is EnvelopeKind.CALIBRATION and m.day < day.index
                assert m == emitted[(m.astronaut_id, m.payload["calibration_id"])]
                carried_types.add(m.payload["calibration_type"])
            cited, missing = unresolved_citations(alone)
            assert cited > 0 and missing == []  # el día suelto se resuelve solo
            if carried:
                carried_days.add(day.index)
                assert unresolved_citations(exact)[1]  # sin las arrastradas, no
    assert carried_days == {2}
    assert carried_types == {"stretch", "imu"}  # las emg_mvc se registran en su sesión: nunca se arrastran


def test_a_session_regenerated_alone_carries_the_calibrations_recorded_outside_it(sparse_config, sparse_mission):
    generator = MissionGenerator(sparse_config)
    emitted = first_emissions(sparse_mission)
    origins = set()
    for day in (1, 2):
        for plan in generator.sessions(day, "AST-01"):
            alone = list(generator.session_messages(plan.ref))
            exact = list(generator.session_messages(plan.ref, include_carried_calibrations=False))
            n = len(alone) - len(exact)
            carried = alone[:n]
            assert alone[n:] == exact
            (session,) = [m for m in exact if m.kind is EnvelopeKind.SESSION]
            declared = {s["calibration_id"] for s in session.payload["streams"] if "calibration_id" in s}
            recorded = {m.payload["calibration_id"] for m in exact if m.kind is EnvelopeKind.CALIBRATION}
            from_streams = [s.get("calibration_id") for s in session.payload["streams"]]
            assert [m.payload["calibration_id"] for m in carried] == [i for i in from_streams if i in declared - recorded]
            for m in carried:
                assert m == emitted[("AST-01", m.payload["calibration_id"])] and m.payload.get("session_id") != plan.session_id
                origins.add("earlier day" if m.day < day else "same day")
            cited, missing = unresolved_citations(alone)
            assert cited > 0 and missing == [], plan.ref
    assert origins == {"earlier day", "same day"}  # día 2: las del día 1; y otras sesiones del mismo día


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
        elif envelope.kind is EnvelopeKind.CALIBRATION:
            assert envelope.topic == topics.calibration(p["astronaut_id"], p["device_id"], p["stream_id"]) and envelope.retained
            assert envelope.at == datetime.fromisoformat(p["created_at"])
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
    for p in imu:
        if p["session_id"] == background.session_id:
            end = datetime.fromisoformat(p["received_at"]) - timedelta(milliseconds=config.faults.device.clock.latency_ms.max)
            assert not exercise.start < end < exercise.end


def test_every_stretch_chunk_has_its_retained_calibration_emitted_earlier(config, mission):
    """Las calibraciones viajan por MQTT (retained) y salen antes de los chunks que las referencian."""
    emitted: dict[tuple[str, str], Envelope] = {}  # (astronauta, calibration_id) -> sobre
    checked = 0
    for m in mission:
        if m.kind is EnvelopeKind.CALIBRATION:
            p = m.payload
            assert m.retained and m.topic == topics.calibration(p["astronaut_id"], p["device_id"], p["stream_id"])
            assert validation.errors("domain/calibration", p) == []
            emitted[(m.astronaut_id, p["calibration_id"])] = m
        elif m.kind is EnvelopeKind.STREAM_CHUNK and m.payload["stream_kind"] == "stretch":
            p = m.payload
            earlier = emitted[(m.astronaut_id, p["calibration_id"])]  # KeyError: aún no emitida
            assert earlier.payload["stream_id"] == p["stream_id"] and earlier.retained and earlier.at <= m.at
            checked += 1
    assert checked > 0 and {m.topic.rsplit("/", 2)[-2] for m in emitted.values()} == {"calibration"}


def test_calibration_envelope_helper_matches_what_the_mission_emits(config, mission):
    original = next(m for m in mission if m.kind is EnvelopeKind.CALIBRATION and m.payload["calibration_type"] == "stretch")
    generator = MissionGenerator(config)
    day = generator.timeline.day(original.day)
    record = sess.scheduled_calibrations(generator.config, generator.seed, generator.timeline, original.astronaut_id, day)[original.payload["stream_id"]]
    assert calibration_envelope(original.astronaut_id, original.payload["device_id"], record, original.at) == original


def test_background_chunks_are_about_ten_times_fewer_than_at_100_ms_and_slices_still_match():
    def edit(chunk_ms: float):
        def apply(raw):
            raw["signals"]["chunk_ms"] = 100
            raw["schedule"]["background"].update(chunk_ms=chunk_ms)
            raw["faults"]["device"]["packet_loss"]["probability"] = 0.0
            raw["faults"]["device"]["reboot"]["probability_per_hour"] = 0.0

        return apply

    def background_imu(config) -> list[Envelope]:
        generator = MissionGenerator(config)
        bg = next(p for p in generator.sessions(1, "AST-01") if p.background)
        return [m for m in generator.session_messages(bg.ref) if m.kind is EnvelopeKind.STREAM_CHUNK and m.payload["stream_id"] == "imu_l3"]

    heavy, light = background_imu(tiny_config(edit(100.0))), background_imu(tiny_config(edit(1000.0)))
    assert {m.payload["n_samples"] for m in heavy} == {1} and {m.payload["n_samples"] for m in light} == {10}
    assert 9 < len(heavy) / len(light) < 11
    config = tiny_config(edit(1000.0))
    generator = MissionGenerator(config)
    bg = next(p for p in generator.sessions(1, "AST-01") if p.background)
    day = [m for m in generator.day_messages(1, "AST-01") if session_of(m) == bg.session_id]
    assert list(generator.session_messages(bg.ref, include_carried_calibrations=False)) == day  # la sesión de fondo sola = su rebanada del día (seq incluido)
    seqs = [m.payload["seq"] for m in day if m.kind is EnvelopeKind.STREAM_CHUNK and m.payload["stream_id"] == "imu_l3"]
    assert seqs == sorted(seqs) and seqs[0] >= 0


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
    def imu(msgs):
        return np.array(
            [
                [m.payload["data"][c][0] for c in ("accel_x", "accel_y", "accel_z")]
                for m in msgs
                if m.kind is EnvelopeKind.STREAM_CHUNK and m.payload["stream_kind"] == "imu"
            ]
        )
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
    background = take_chunks(
        generator.live_background(astronaut_id="AST-01", day=1, device=device, session_id=LIVE_BACKGROUND, start=T0 + timedelta(seconds=1)), 30
    )
    session = generator.live_session(
        astronaut_id="AST-01", day=1, device=device, session_id=LIVE_SESSION, session_type="exercise",
        protocol_id="lat_pulldown_standing_v1", stream_ids=[], start=T0 + timedelta(seconds=20),
    )  # fmt: skip
    first = next(session)
    assert first.kind is EnvelopeKind.GROUND_TRUTH and first.payload["type"] == "expected_features"
    in_session = take_chunks(session, 200)  # más que el protocolo: sigue en reposo
    resumed = take_chunks(
        generator.live_background(astronaut_id="AST-01", day=1, device=device, session_id=LIVE_BACKGROUND, start=T0 + timedelta(seconds=200)), 10
    )
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
    envelopes = generator.live_calibration(
        astronaut_id="AST-01", day=1, session_id=LIVE_SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"], at=at
    )
    (calibration,) = envelopes
    assert calibration.kind is EnvelopeKind.CALIBRATION and calibration.retained
    assert calibration.topic == topics.calibration("AST-01", config.member("AST-01").device_id, "stretch_lumbar")
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
    mvc = generator.live_calibration(
        astronaut_id="AST-01", day=1, session_id=LIVE_SESSION, calibration_type="emg_mvc", stream_ids=["emg_lat_left", "emg_lat_right"], at=at
    )
    assert [validation.errors("domain/calibration", m.payload) for m in mvc] == [[], []]
    assert [m.topic for m in mvc] == [topics.calibration("AST-01", config.member("AST-01").device_id, s) for s in ("emg_lat_left", "emg_lat_right")]
    with pytest.raises(ValueError, match="does not apply"):
        generator.live_calibration(astronaut_id="AST-01", day=1, session_id=LIVE_SESSION, calibration_type="imu", stream_ids=["emg_lat_left"], at=at)
    with pytest.raises(ValueError):
        generator.live_calibration(astronaut_id="AST-01", day=1, session_id=LIVE_SESSION, calibration_type="imu", stream_ids=[], at=at)


def test_calibration_in_force_is_the_live_one_or_else_the_scheduled_one_of_that_day(sparse_config):
    generator = MissionGenerator(sparse_config)
    timeline = generator.timeline
    scheduled = [sess.scheduled_calibrations(sparse_config, sparse_config.seed, timeline, "AST-01", d)["stretch_lumbar"] for d in timeline]
    assert [generator.calibration_in_force("AST-01", "stretch_lumbar", d.index) for d in timeline] == scheduled
    assert scheduled[2] == scheduled[1] and generator.calibration_in_force("AST-01", "imu_l3", 2).day == 1
    assert generator.calibration_in_force("AST-01", "emg_lat_left", 2) is None  # la MVC solo existe en vivo o en su sesión
    (first,) = generator.live_calibration(
        astronaut_id="AST-01", day=2, session_id=LIVE_SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"], at=T0
    )
    (second,) = generator.live_calibration(
        astronaut_id="AST-01", day=2, session_id=LIVE_SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"], at=T0 + timedelta(seconds=1)
    )
    assert {generator.calibration_in_force("AST-01", "stretch_lumbar", d.index).calibration_id for d in timeline} == {second.payload["calibration_id"]}
    assert generator.calibration_in_force("AST-02", "stretch_lumbar", 2).calibration_id != second.payload["calibration_id"]
    # Por id: también una en vivo ya sustituida, o una programada vigente ese día.
    assert (
        generator.calibration("AST-01", first.payload["calibration_id"], 2).model
        == generator.calibration("AST-01", first.payload["calibration_id"], 0).model
    )
    assert (
        calibration_envelope("AST-01", sparse_config.member("AST-01").device_id, generator.calibration("AST-01", first.payload["calibration_id"], 2), first.at)
        == first
    )
    assert generator.calibration("AST-01", scheduled[2].calibration_id, 2) == scheduled[2]
    assert generator.calibration("AST-01", scheduled[0].calibration_id, 2) is None
    with pytest.raises(KeyError):
        generator.calibration_in_force("AST-01", "nope_stream", 0)


def test_live_blocks_pick_up_a_calibration_made_while_they_run():
    def edit(raw):
        raw["faults"]["device"]["packet_loss"]["probability"] = 0.0
        raw["faults"]["device"]["reboot"]["probability_per_hour"] = 0.0

    config = tiny_config(edit)
    generator = MissionGenerator(config)
    old = generator.calibration_in_force("AST-01", "stretch_lumbar", 1).calibration_id
    sources = {
        "background": (
            generator.live_background(astronaut_id="AST-01", day=1, device=live_device(config), session_id=LIVE_BACKGROUND, start=T0),
            config.schedule.background.chunk_ms,
        ),
        "session": (
            generator.live_session(
                astronaut_id="AST-01", day=1, device=live_device(config), session_id=LIVE_SESSION, session_type="spine_check", protocol_id="free_rest_v1",
                stream_ids=["stretch_lumbar"], start=T0,
            ),
            config.signals.chunk_ms,
        ),
    }  # fmt: skip
    for name, (iterator, chunk_ms) in sources.items():
        block = round(config.device.status_interval_s * 1000.0 / chunk_ms)

        def stretch(n: int, iterator=iterator) -> list[dict]:
            return [m.payload for m in take_chunks(iterator, n) if m.payload["stream_id"] == "stretch_lumbar"]

        before = stretch(4)
        (calibration,) = generator.live_calibration(
            astronaut_id="AST-01", day=1, session_id=LIVE_SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"], at=T0
        )
        new = calibration.payload["calibration_id"]
        after = stretch(4 * block)
        first_seq = before[0]["seq"]
        assert {p["calibration_id"] for p in before} == {old}, name
        # El bloque en curso sigue con la anterior; desde el siguiente, la nueva (búsqueda por bloque).
        assert all(p["calibration_id"] == (old if p["seq"] - first_seq < block else new) for p in after), name
        assert any(p["calibration_id"] == new for p in after), name
        old = new


def test_live_blocks_after_midnight_keep_the_awake_window_of_the_mission_day(config):
    generator = MissionGenerator(config)
    info = generator._timeline.day(1)
    after_midnight = datetime.combine(info.date + timedelta(days=1), datetime.min.time(), tzinfo=UTC) + timedelta(minutes=5)
    for start in (T0, after_midnight):
        spec = generator._live_spec(
            ("k",), start, 1, config.schedule.background.chunk_ms, ("stretch_lumbar",), info, background=True, reps=(), protocol=None, residual={}
        )
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


def test_calibrations_made_at_power_on_outside_any_session_are_emitted_that_day():
    def edit(raw):
        for s in raw["schedule"]["sessions"]:
            s["phases"] = ["postflight"]
        raw["schedule"]["background"]["enabled"] = False

    config = tiny_config(edit)
    generator = MissionGenerator(config)
    day0 = [m for m in generator.day_messages(0, "AST-01", include_carried_calibrations=False) if m.kind is EnvelopeKind.CALIBRATION]
    device_id = config.member("AST-01").device_id
    expected = []
    for stream in ("imu_l3", "stretch_lumbar"):
        record = generator.calibration_in_force("AST-01", stream, 0)
        assert record is not None and record.session_id is None and record.day == 0
        assert record.created_at == generator.timeline.day(0).at(config.device.power_on_time_utc)
        expected.append(calibration_envelope("AST-01", device_id, record, record.created_at))
    assert sorted(day0, key=lambda m: m.topic) == sorted(expected, key=lambda m: m.topic)
    assert [m.day for m in day0] == [0, 0]


# La identidad de las cachés interna es lo único observable de que se calculan una vez (la salida es idéntica con o sin caché).
def test_plans_and_scheduled_calibrations_are_computed_once_and_reused(config):
    generator = MissionGenerator(config)
    assert generator.sessions(1, "AST-01") is generator.sessions(1, "AST-01")
    assert generator.sessions(1, "AST-01") is not generator.sessions(1, "AST-02")
    assert generator._scheduled_calibrations("AST-01", 1) is generator._scheduled_calibrations("AST-01", 1)


def test_session_plan_of_a_missing_slot_names_it(config):
    generator = MissionGenerator(config)
    with pytest.raises(KeyError) as excinfo:
        generator.session_plan(SessionRef("AST-01", 0, "nope"))
    assert excinfo.value.args == ("no session 'nope' for AST-01 on day 0",)


# Helper privado puro: sus casos (redondeo al microsegundo, k>0) no se pueden aislar en la salida publica de una mision.
def test_chunk_start_is_the_real_instant_of_the_chunk():
    from spine_physiology.generator import _chunk_start

    start = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
    assert _chunk_start(start, 0, 100, 200.0) == start
    assert _chunk_start(start, 3, 100, 200.0) == start + timedelta(seconds=1.5)
    assert _chunk_start(start, 1, 10, 3.0) == start + timedelta(microseconds=3_333_333)  # redondeado al microsegundo


def _plan_with_boots(*boots):
    from spine_physiology.device import BootEpoch, DevicePlan

    t0 = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
    epochs = tuple(
        BootEpoch(boot_id=i, boot_at=t0 + timedelta(seconds=a), end_at=None if b is None else t0 + timedelta(seconds=b), drift_ppm=0.0)
        for i, (a, b) in enumerate(boots)
    )
    return t0, DevicePlan("AST-01", "DEV-01", t0, t0 + timedelta(seconds=30), epochs, ())


# Helper privado puro: los solapes y bordes de arranque que prueba no los produce un plan real
# (arranques disjuntos), asi que no se alcanzan desde la salida publica.
def test_existing_chunk_ends_cover_only_chunks_that_start_while_a_boot_is_up():
    from spine_physiology.generator import _existing_chunk_ends

    # 30 chunks de 1 s desde t0; arranque 1 en [2, 10) y arranque 2 desde el 20, abierto.
    t0, plan = _plan_with_boots((2, 10), (20, None))
    ends = _existing_chunk_ends(plan, t0, 30, 10, 10.0)
    seconds = [(e - t0).total_seconds() for e in ends]
    # Empiezan en 2..9 y 20..29 (inicio == arranque incluido, inicio == caida excluido): acaban en 3..10 y 21..30.
    assert seconds == [*range(3, 11), *range(21, 31)]


def test_existing_chunk_ends_never_count_a_chunk_twice_and_follow_the_chunk_grid():
    from spine_physiology.generator import _existing_chunk_ends

    t0, plan = _plan_with_boots((0, 4), (2, None))  # solapados (no ocurre en la practica): el primero se queda el chunk
    ends = _existing_chunk_ends(plan, t0, 6, 5, 4.0)  # chunks de 1.25 s
    assert [(e - t0).total_seconds() for e in ends] == [1.25, 2.5, 3.75, 5.0, 6.25, 7.5]
    t0, plan = _plan_with_boots((0, None))
    assert _existing_chunk_ends(plan, t0, 0, 5, 4.0) == []


# Helper privado puro: el empate exacto con la marca solo se da con instantes construidos a mano.
def test_reorder_releases_only_what_is_strictly_before_the_mark():
    from spine_physiology.generator import _Reorder

    t0 = datetime(2030, 1, 1, tzinfo=UTC)
    reorder = _Reorder()
    for second, kind in ((2, EnvelopeKind.SESSION), (1, EnvelopeKind.SELF_REPORT), (2, EnvelopeKind.DAILY_CONTEXT), (3, EnvelopeKind.GROUND_TRUTH)):
        reorder.push(Envelope(kind=kind, topic=None, payload={}, at=t0 + timedelta(seconds=second), retained=False, astronaut_id="AST-01", day=0))
    assert [e.kind for e in reorder.release(t0 + timedelta(seconds=2))] == [EnvelopeKind.SELF_REPORT]
    assert [e.kind for e in reorder.release(t0 + timedelta(seconds=3))] == [EnvelopeKind.SESSION, EnvelopeKind.DAILY_CONTEXT]  # empate: orden de llegada
    assert [e.kind for e in reorder.release(None)] == [EnvelopeKind.GROUND_TRUTH]


def test_live_calibration_errors_name_the_problem_and_imu_calibrations_work(config):
    generator = MissionGenerator(config)
    common = {"astronaut_id": "AST-01", "day": 1, "session_id": LIVE_SESSION, "at": T0}
    with pytest.raises(ValueError, match=r"^unknown calibration_type 'gyro'$"):
        generator.live_calibration(calibration_type="gyro", stream_ids=["imu_l3"], **common)
    with pytest.raises(ValueError, match=r"^calibrate needs at least one stream_id$"):
        generator.live_calibration(calibration_type="imu", stream_ids=[], **common)
    (imu,) = generator.live_calibration(calibration_type="imu", stream_ids=["imu_l3"], **common)
    assert imu.payload["calibration_type"] == "imu" and validation.errors("domain/calibration", imu.payload) == []
    assert generator.calibration_in_force("AST-01", "imu_l3", 1).day == 1
    assert generator.calibration("AST-01", imu.payload["calibration_id"], 0).calibration_id == imu.payload["calibration_id"]


def test_live_session_without_a_known_protocol_or_streams_uses_every_device_stream(config):
    generator = MissionGenerator(config)
    chunks = take_chunks(
        generator.live_session(
            astronaut_id="AST-01", day=1, device=live_device(config), session_id=LIVE_SESSION, session_type="spine_check", protocol_id="free_rest_v1",
            stream_ids=[], start=T0,
        ),
        40,
    )  # fmt: skip
    assert {m.payload["stream_id"] for m in chunks} == {s.stream_id for s in config.device.streams}


def test_live_exercise_session_announces_its_features_then_streams_contiguous_chunks_across_blocks():
    config = tiny_config(lambda raw: raw["faults"]["device"]["packet_loss"].update(probability=0.0))
    generator = MissionGenerator(config)
    messages = list(
        itertools.islice(
            generator.live_session(
                astronaut_id="AST-01", day=1, device=live_device(config), session_id=LIVE_SESSION, session_type="exercise",
                protocol_id="lat_pulldown_standing_v1", stream_ids=[], start=T0,
            ),
            400,
        )
    )  # fmt: skip
    first = messages[0]
    assert (first.kind, first.retained, first.topic, first.astronaut_id, first.day, first.at) == (EnvelopeKind.GROUND_TRUTH, False, None, "AST-01", 1, T0)
    assert {k: v for k, v in first.payload.items() if k != "features"} == {
        "type": "expected_features", "astronaut_id": "AST-01", "day": 1, "session_id": LIVE_SESSION, "slot": None,
        "protocol_id": "lat_pulldown_standing_v1", "gravity_environment": "microgravity",
    }  # fmt: skip
    reps = next(f for f in first.payload["features"] if f["feature_id"] == "exercise.rep_count")
    assert reps["value"] == 3.0  # las 3 repeticiones del protocolo: va como ejercicio, con sus reps
    for stream_id in ("imu_l3", "emg_lat_left"):
        chunks = [m.payload for m in messages if m.kind is EnvelopeKind.STREAM_CHUNK and m.payload["stream_id"] == stream_id]
        assert len(chunks) > 40  # el protocolo (32 chunks) y al menos un bloque de reposo despues
        steps = {b["t0_device_ms"] - a["t0_device_ms"] for a, b in itertools.pairwise(chunks)}
        assert steps <= {round(config.signals.chunk_ms) + d for d in (-1, 0, 1)}
    block_ends = [m for m in messages[1:] if m.kind is EnvelopeKind.GROUND_TRUTH]
    for m in block_ends:
        assert (m.retained, m.topic, m.astronaut_id, m.day) == (False, None, "AST-01", 1)
        assert m.payload["session_id"] == LIVE_SESSION and m.payload["type"] in {"quality_faults", "lost_chunks"}
    protocol_end = T0 + 32 * timedelta(milliseconds=config.signals.chunk_ms)
    assert block_ends and block_ends[0].at == protocol_end
    assert all((m.at - protocol_end) % timedelta(seconds=config.device.status_interval_s) == timedelta(0) for m in block_ends)


def test_live_lost_chunks_are_reported_at_the_end_of_their_block_and_only_the_following_chunk_is_marked_as_gap():
    def edit(raw):
        raw["faults"]["device"]["packet_loss"].update(probability=0.4, burst_chunks={"min": 1, "max": 1})

    config = tiny_config(edit)
    generator = MissionGenerator(config)
    messages = list(
        itertools.islice(generator.live_background(astronaut_id="AST-01", day=1, device=live_device(config), session_id=LIVE_BACKGROUND, start=T0), 300)
    )
    lost = [m for m in messages if m.kind is EnvelopeKind.GROUND_TRUTH and m.payload["type"] == "lost_chunks"]
    assert lost
    block = timedelta(seconds=config.device.status_interval_s)
    for m in lost:
        assert (m.retained, m.astronaut_id, m.day, m.payload["session_id"]) == (False, "AST-01", 1, LIVE_BACKGROUND)
        assert (m.at - T0) % block == timedelta(0)
    previous: dict[str, int] = {}
    for m in messages:
        if m.kind is not EnvelopeKind.STREAM_CHUNK:
            continue
        p = m.payload
        gap = any("packet_gap" in q["flags"] for q in p["quality"].values())
        follows_loss = p["seq"] > previous.get(p["stream_id"], -1) + 1
        assert gap == follows_loss, (p["stream_id"], p["seq"])
        previous[p["stream_id"]] = p["seq"]


def _rounded(value, digits=4):
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {k: _rounded(v, digits) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v, digits) for v in value]
    return value


def _live_digest(messages) -> str:
    import hashlib

    line = json.dumps([[m.kind.value, m.at.isoformat(), _rounded(m.payload)] for m in messages], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(line.encode()).hexdigest()[:16]


def test_live_output_for_a_fixed_seed_is_pinned():
    """Pin de los valores en vivo (las etiquetas de RNG `live`/`rest`/`protocol`/`device`/`calibration` solo se notan aqui)."""
    config = tiny_config(lambda raw: raw["faults"]["device"]["packet_loss"].update(probability=0.0))
    generator = MissionGenerator(config)
    session = list(
        itertools.islice(
            generator.live_session(
                astronaut_id="AST-01", day=1, device=live_device(config), session_id=LIVE_SESSION, session_type="exercise",
                protocol_id="lat_pulldown_standing_v1", stream_ids=[], start=T0,
            ),
            150,
        )
    )  # fmt: skip
    live = generator.live_background(astronaut_id="AST-01", day=1, device=live_device(config), session_id=LIVE_BACKGROUND, start=T0)
    background = list(itertools.islice(live, 60))
    calibration = generator.live_calibration(astronaut_id="AST-01", day=1, session_id=LIVE_SESSION, calibration_type="imu", stream_ids=["imu_l3"], at=T0)
    digests = (_live_digest(session), _live_digest(background), _live_digest(calibration))
    assert digests == ("9ecc1d0b633328d5", "9d8254cb72273c50", "644f19204615b2fa")
