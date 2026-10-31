import copy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import yaml
from spine_contracts import GravityEnvironment, MissionPhase, QualityFlag, StreamKind, dump, validation

from spine_physiology.config import MissionConfig
from spine_physiology.device import BootEpoch, DevicePlan, SimDevice, plan_device_day, plan_device_live
from spine_physiology.faults import CLEAN, QualityState, RebootEvent, clean_quality
from spine_physiology.signals.imu import IMU_CHANNELS
from spine_physiology.state import DayInfo

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
ASTRONAUT = "AST-01"
CAL_ID = "0b1f5a7e-4a3c-4d2e-9c11-5f6a7b8c9d0e"
SESSION = "11111111-2222-4333-8444-555555555555"
DAY = DayInfo(
    index=3, date=date(2026, 11, 13), mission_day=3, phase=MissionPhase.INFLIGHT,
    gravity_environment=GravityEnvironment.MICROGRAVITY, segment_index=1, day_in_segment=0,
)  # fmt: skip
T0 = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


def make_config(edit=None) -> MissionConfig:
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    # Dispositivo limpio por defecto: sin pérdidas ni reinicios, para que cada test active lo que prueba.
    raw["faults"]["device"]["packet_loss"]["probability"] = 0.0
    raw["faults"]["device"]["reboot"]["probability_per_hour"] = 0.0
    if edit:
        edit(raw)
    return MissionConfig.model_validate(raw)


@pytest.fixture(scope="module")
def config() -> MissionConfig:
    return make_config()


def single_boot_plan(config: MissionConfig, *, drift_ppm: float = 0.0, boot_at: datetime = T0 - timedelta(hours=1)) -> DevicePlan:
    boot = BootEpoch(boot_id=1234, boot_at=boot_at, end_at=None, drift_ppm=drift_ppm)
    return DevicePlan(ASTRONAUT, "sim-01", boot_at, None, (boot,), ())


def channels_for(kind: StreamKind, n: int, *, length: bool = True) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(0)
    if kind is StreamKind.EMG:
        return {"emg": rng.normal(0, 0.1, n)}
    if kind is StreamKind.IMU:
        return {c: rng.normal(0, 1, n) for c in IMU_CHANNELS}
    out = {"adc_counts": rng.integers(1000, 3000, n), "resistance_ohm": rng.uniform(1000, 2000, n)}
    if length:
        out["length_mm"] = rng.uniform(100, 130, n)
    return out


STREAMS = {"emg_lat_left": StreamKind.EMG, "imu_l3": StreamKind.IMU, "stretch_lumbar": StreamKind.STRETCH}
SCHEMA = "telemetry/stream-chunk"


def emit(device, config, stream_id, *, n=None, start=T0, seed=0, channels=None, quality=None, session=SESSION, cal=CAL_ID):
    kind = STREAMS[stream_id]
    rate = config.signals.rate(kind)
    spc = config.signals.samples_per_chunk(rate)
    n = n if n is not None else spc * 10
    channels = channels or channels_for(kind, n)
    n_chunks = -(-n // spc)
    return list(
        device.chunks(
            np.random.default_rng(seed), stream_id=stream_id, session_id=session, start=start, rate_hz=rate, chunk_ms=config.signals.chunk_ms,
            channels=channels, quality=quality or clean_quality(kind, n_chunks), calibration_id=cal,
        )
    )  # fmt: skip


@pytest.mark.parametrize("stream_id", STREAMS)
def test_every_chunk_validates_against_the_contract(config, stream_id):
    device = SimDevice(config, single_boot_plan(config))
    outcomes = emit(device, config, stream_id)
    assert len(outcomes) == 10
    for o in outcomes:
        payload = dump(o.chunk)
        assert validation.errors(SCHEMA, payload) == []
        assert payload["source"] == "simulator"
        assert payload["boot_id"] == 1234
        assert payload["session_id"] == SESSION
        assert payload["quality"]


def test_chunk_content_and_metadata(config):
    device = SimDevice(config, single_boot_plan(config))
    emg = emit(device, config, "emg_lat_left")[0].chunk
    assert emg.n_samples == 100 and len(emg.data.emg) == 100
    assert emg.signal_kind.value == "raw" and emg.acquisition.adc_bits == config.signals.emg.acquisition.adc_bits
    assert emg.quality.emg.assessed_by.value == "gateway"

    imu = emit(device, config, "imu_l3")[0].chunk
    assert imu.config.accel_range_g == config.signals.imu.accel_range_g
    assert imu.units.gyro_x == "deg/s"

    stretch = emit(device, config, "stretch_lumbar")[0].chunk
    assert stretch.calibration_id == CAL_ID
    assert stretch.circuit.series_resistor_ohm == config.signals.stretch.circuit.series_resistor_ohm
    assert stretch.units.length_mm == "mm"


def test_stretch_without_length_needs_no_calibration_but_length_requires_it(config):
    device = SimDevice(config, single_boot_plan(config))
    plain = emit(device, config, "stretch_lumbar", channels=channels_for(StreamKind.STRETCH, 200, length=False), n=200, cal=None)
    for o in plain:
        payload = dump(o.chunk)
        assert "calibration_id" not in payload and "length_mm" not in payload["data"]
        assert validation.errors(SCHEMA, payload) == []
    with pytest.raises(ValueError, match="calibration_id"):
        device.chunks(
            np.random.default_rng(0), stream_id="stretch_lumbar", session_id=SESSION, start=T0, rate_hz=config.signals.stretch.sample_rate_hz,
            chunk_ms=config.signals.chunk_ms, channels=channels_for(StreamKind.STRETCH, 200), quality=clean_quality(StreamKind.STRETCH, 10), calibration_id=None,
        )  # fmt: skip


def test_timing_uses_the_drifting_clock_and_received_at_jitter(config):
    drift = 40.0
    plan = single_boot_plan(config, drift_ppm=drift)
    device = SimDevice(config, plan)
    outcomes = emit(device, config, "emg_lat_left")
    boot = plan.boots[0]
    clock = config.faults.device.clock
    for i, o in enumerate(outcomes):
        chunk_start = T0 + timedelta(milliseconds=100 * i)
        assert o.t0_device_ms == boot.device_ms(chunk_start)
        assert o.t0_device_ms == int(((chunk_start - boot.boot_at).total_seconds() * 1000) * (1 + drift * 1e-6))
        latency_ms = (o.received_at - (chunk_start + timedelta(milliseconds=100))).total_seconds() * 1000
        assert clock.latency_ms.min - 0.01 <= latency_ms <= clock.latency_ms.max + 0.01
    assert len({o.received_at - (T0 + timedelta(milliseconds=100 * (i + 1))) for i, o in enumerate(outcomes)}) > 1  # hay jitter


def test_boot_epoch_round_trip():
    boot = BootEpoch(boot_id=1, boot_at=T0, end_at=T0 + timedelta(hours=1), drift_ppm=-30.0)
    at = T0 + timedelta(minutes=10)
    assert boot.contains(at) and not boot.contains(T0 + timedelta(hours=1)) and not boot.contains(T0 - timedelta(seconds=1))
    assert abs(boot.real_at(boot.device_ms(at)) - at) < timedelta(milliseconds=1)
    assert boot.device_ms(T0 - timedelta(seconds=5)) == 0


def test_tail_partial_chunk_is_emitted_with_its_own_size(config):
    device = SimDevice(config, single_boot_plan(config))
    n = 1050
    outcomes = emit(device, config, "emg_lat_left", n=n)
    assert [o.chunk.n_samples for o in outcomes] == [100] * 10 + [50]
    assert sum(len(o.chunk.data.emg) for o in outcomes) == n
    assert [o.seq for o in outcomes] == list(range(11))
    for o in outcomes:
        assert validation.errors(SCHEMA, dump(o.chunk)) == []


def test_samples_are_sliced_in_order(config):
    device = SimDevice(config, single_boot_plan(config))
    channels = channels_for(StreamKind.EMG, 350)
    outcomes = emit(device, config, "emg_lat_left", n=350, channels=channels)
    joined = np.concatenate([o.chunk.data.emg for o in outcomes])
    np.testing.assert_array_equal(joined, channels["emg"])


def test_seq_is_continuous_across_calls_and_advance(config):
    device = SimDevice(config, single_boot_plan(config))
    first = emit(device, config, "emg_lat_left", n=1000)
    assert [o.seq for o in first] == list(range(10))
    device.advance("emg_lat_left", start=T0 + timedelta(seconds=1), n_samples=2000, rate_hz=1000.0, chunk_ms=100)
    assert device.next_seq(1234, "emg_lat_left") == 30
    after = emit(device, config, "emg_lat_left", n=300, start=T0 + timedelta(seconds=3))
    assert [o.seq for o in after] == [30, 31, 32]
    # El seq es por stream.
    assert [o.seq for o in emit(device, config, "imu_l3", n=30)] == [0, 1, 2]


def test_advance_matches_emitting_the_same_span(config):
    a, b = SimDevice(config, single_boot_plan(config)), SimDevice(config, single_boot_plan(config))
    emit(a, config, "emg_lat_left", n=2500)
    b.advance("emg_lat_left", start=T0, n_samples=2500, rate_hz=1000.0, chunk_ms=100)
    assert a.next_seq(1234, "emg_lat_left") == b.next_seq(1234, "emg_lat_left") == 25
    assert a.battery_pct(T0 + timedelta(hours=1)) == b.battery_pct(T0 + timedelta(hours=1))


def test_lost_chunks_jump_seq_and_flag_the_next_one(config):
    def edit(raw):
        raw["faults"]["device"]["packet_loss"].update(probability=0.15, burst_chunks={"min": 1, "max": 3})

    cfg = make_config(edit)
    device = SimDevice(cfg, single_boot_plan(cfg))
    outcomes = emit(device, cfg, "emg_lat_left", n=100 * 300)
    lost = [o for o in outcomes if o.lost]
    assert lost and len(lost) < len(outcomes)
    assert [o.seq for o in outcomes] == list(range(300))  # los perdidos también consumen seq
    emitted = [o for o in outcomes if not o.lost]
    seqs = [o.seq for o in emitted]
    assert any(b - a > 1 for a, b in zip(seqs, seqs[1:], strict=False))
    gap_score = cfg.faults.device.packet_loss.packet_gap_score
    by_seq = {o.seq: o for o in outcomes}
    for o in emitted:
        flagged = [f.value for f in o.chunk.quality.emg.flags]
        after_loss = o.seq > 0 and by_seq[o.seq - 1].lost
        assert ("packet_gap" in flagged) == after_loss
        if after_loss:
            assert o.chunk.quality.emg.score == min(1.0, gap_score)
        assert validation.errors(SCHEMA, dump(o.chunk)) == []
    assert all(o.received_at > T0 for o in lost)


def test_clock_uncertain_after_long_time_without_anchor(config):
    plan = single_boot_plan(config, drift_ppm=40.0, boot_at=T0 - timedelta(hours=1))
    device = SimDevice(config, plan)
    flagged = emit(device, config, "imu_l3", n=100)[0].chunk
    assert "clock_uncertain" in [f.value for f in flagged.quality.accel.flags]
    # Con un anclaje reciente el error acumulado vuelve a ser pequeño.
    device.clock_sync(np.random.default_rng(0), T0 - timedelta(seconds=1))
    clean = emit(device, config, "imu_l3", n=100)[0].chunk
    assert "clock_uncertain" not in [f.value for f in clean.quality.accel.flags]


def test_quality_per_chunk_is_forwarded(config):
    device = SimDevice(config, single_boot_plan(config))
    bad = QualityState(0.4, frozenset({QualityFlag.CLIPPING}))
    quality = clean_quality(StreamKind.EMG, 10)
    quality[4]["emg"] = bad
    outcomes = emit(device, config, "emg_lat_left", quality=quality)
    assert [o.chunk.quality.emg.score for o in outcomes] == [1.0] * 4 + [0.4] + [1.0] * 5
    assert [f.value for f in outcomes[4].chunk.quality.emg.flags] == ["clipping"]
    assert outcomes[0].chunk.quality.emg.flags == [] and CLEAN.score == 1.0


# --- planes y reinicios -------------------------------------------------------------------------


def test_day_plan_is_deterministic_per_astronaut_and_day(config):
    a = plan_device_day(config, 7, ASTRONAUT, DAY)
    b = plan_device_day(config, 7, ASTRONAUT, DAY)
    assert a == b
    assert plan_device_day(config, 8, ASTRONAUT, DAY).boots[0].boot_id != a.boots[0].boot_id
    assert plan_device_day(config, 7, "AST-02", DAY).device_id == config.member("AST-02").device_id
    assert a.boots[0].boot_at == datetime(2026, 11, 13, 6, 30, tzinfo=UTC)
    assert a.power_off == datetime(2026, 11, 13, 22, 30, tzinfo=UTC)
    assert a.boots[-1].end_at == a.power_off


def test_day_plan_with_reboots_has_distinct_boot_ids_and_offline_gaps():
    cfg = make_config(lambda raw: raw["faults"]["device"]["reboot"].update(probability_per_hour=3.0))
    plan = plan_device_day(cfg, 7, ASTRONAUT, DAY)
    assert plan.reboots and len(plan.boots) >= 2
    assert len({b.boot_id for b in plan.boots}) == len(plan.boots)
    first = plan.boots[0]
    assert first.end_at == plan.reboots[0].at
    offline = first.end_at + timedelta(seconds=plan.reboots[0].offline_s / 2)
    assert plan.epoch_at(offline) is None
    assert plan.epoch_at(first.boot_at + timedelta(seconds=1)) is first
    for prev, nxt in zip(plan.boots, plan.boots[1:], strict=False):
        assert nxt.boot_at >= prev.end_at


def test_live_plan_is_open_ended_and_deterministic(config):
    kwargs = dict(boot_at=T0, horizon=timedelta(hours=2))
    a = plan_device_live(config, 5, ASTRONAUT, **kwargs)
    assert a == plan_device_live(config, 5, ASTRONAUT, **kwargs)
    assert a.power_off is None and a.boots[-1].end_at is None and a.boots[0].boot_at == T0


def reboot_plan(config: MissionConfig) -> DevicePlan:
    cut = T0 + timedelta(seconds=2)
    back = cut + timedelta(seconds=10)
    boots = (
        BootEpoch(boot_id=111, boot_at=T0 - timedelta(minutes=5), end_at=cut, drift_ppm=10.0),
        BootEpoch(boot_id=222, boot_at=back, end_at=None, drift_ppm=-20.0),
    )
    return DevicePlan(ASTRONAUT, "sim-01", T0 - timedelta(minutes=5), None, boots, (RebootEvent(cut, 10.0),))


def test_reboot_resets_seq_and_t0_with_new_boot_id_and_skips_offline_chunks(config):
    device = SimDevice(config, reboot_plan(config))
    outcomes = emit(device, config, "emg_lat_left", n=100 * 200)  # 20 s
    boots = [o.boot_id for o in outcomes]
    assert set(boots) == {111, 222}
    first = [o for o in outcomes if o.boot_id == 111]
    second = [o for o in outcomes if o.boot_id == 222]
    assert [o.seq for o in first] == list(range(len(first))) and len(first) == 20
    assert [o.seq for o in second] == list(range(len(second)))
    assert second[0].t0_device_ms < 100  # el reloj vuelve a ~0 con el arranque nuevo
    assert first[-1].t0_device_ms > 300_000  # el anterior llevaba 5 min encendido
    # Los chunks con el dispositivo caído no existen: ni salen ni gastan seq.
    gap = second[0].t0_device_ms  # primer chunk tras volver
    assert len(first) + len(second) < 200 and gap >= 0
    for o in outcomes:
        assert validation.errors(SCHEMA, dump(o.chunk)) == []
    # Un chunk posterior continúa el seq del arranque vivo.
    more = emit(device, config, "emg_lat_left", n=300, start=T0 + timedelta(seconds=30))
    assert [o.seq for o in more] == [len(second), len(second) + 1, len(second) + 2]


def test_chunks_before_power_on_do_not_exist(config):
    plan = single_boot_plan(config, boot_at=T0 + timedelta(seconds=1))
    device = SimDevice(config, plan)
    outcomes = emit(device, config, "emg_lat_left", n=2000)
    assert len(outcomes) == 10 and outcomes[0].seq == 0 and outcomes[0].t0_device_ms == 0


# --- DeviceStatus y ClockSync -------------------------------------------------------------------


def test_status_ok_and_validates_against_contract(config):
    device = SimDevice(config, single_boot_plan(config))
    emit(device, config, "emg_lat_left", n=500)
    status = device.status(np.random.default_rng(0), T0 + timedelta(seconds=1))
    payload = dump(status)
    assert validation.errors("telemetry/device-status", payload) == []
    assert payload["status"] == "ok" and payload["source"] == "simulator" and payload["boot_id"] == 1234
    assert payload["link"]["type"] == config.device.link_type
    by_id = {s["stream_id"]: s for s in payload["streams"]}
    assert by_id["emg_lat_left"]["last_seq"] == 4 and "last_seq" not in by_id["imu_l3"]
    assert {s["state"] for s in payload["streams"]} == {"active"}


def test_status_reports_the_active_stream_rate(config):
    device = SimDevice(config, single_boot_plan(config))
    nominal = config.signals.rate(StreamKind.IMU)
    reduced = nominal / 2
    at = T0 + timedelta(seconds=5)
    rate = lambda: {s["stream_id"]: s["sample_rate_hz"] for s in dump(device.status(np.random.default_rng(0), at))["streams"]}
    assert rate()["imu_l3"] == nominal
    device.advance("imu_l3", start=T0, n_samples=100, rate_hz=reduced, chunk_ms=100)
    assert rate()["imu_l3"] == reduced and rate()["emg_lat_left"] == config.signals.rate(StreamKind.EMG)
    emit(device, config, "imu_l3", n=100, start=T0 + timedelta(seconds=2))
    assert rate()["imu_l3"] == nominal


def test_clock_anchors_stay_sorted_whatever_the_sync_order(config):
    device = SimDevice(config, single_boot_plan(config, drift_ppm=50.0))
    for minutes in (30, 10, 20):
        device.clock_sync(np.random.default_rng(0), T0 + timedelta(minutes=minutes))
    anchors = device._anchors[1234]
    assert anchors == sorted(anchors)
    outcomes = emit(device, config, "emg_lat_left", n=500, start=T0 + timedelta(minutes=25))
    assert outcomes


def test_battery_drains_and_drops_to_degraded(config):
    device = SimDevice(config, single_boot_plan(config, boot_at=T0))
    cfg = config.device.battery
    assert device.battery_pct(T0) == cfg.start_pct
    hour = T0 + timedelta(hours=1)
    assert device.battery_pct(hour) == pytest.approx(cfg.start_pct - cfg.drain_pct_per_hour_idle)
    device.advance("emg_lat_left", start=T0, n_samples=1_800_000, rate_hz=1000.0, chunk_ms=100)  # 30 min de streaming
    device.advance("imu_l3", start=T0, n_samples=180_000, rate_hz=100.0, chunk_ms=100)  # el mismo tramo no cuenta doble
    expected = cfg.start_pct - cfg.drain_pct_per_hour_idle - (cfg.drain_pct_per_hour_streaming - cfg.drain_pct_per_hour_idle) * 0.5
    assert device.battery_pct(hour) == pytest.approx(expected)
    assert device.battery_pct(T0 + timedelta(days=5)) == 0.0

    # Por debajo del umbral configurado el estado pasa a degraded.
    low_cfg = make_config(lambda raw: raw["device"]["battery"].update(degraded_below_pct=95.0))
    low_device = SimDevice(low_cfg, single_boot_plan(low_cfg, boot_at=T0))
    assert dump(low_device.status(np.random.default_rng(0), T0))["status"] == "ok"
    status = dump(low_device.status(np.random.default_rng(0), T0 + timedelta(hours=14)))
    assert status["status"] == "degraded" and validation.errors("telemetry/device-status", status) == []


def test_stalled_stream_degrades_status(config):
    def edit(raw):
        raw["faults"]["device"]["packet_loss"].update(probability=1.0, burst_chunks={"min": 5, "max": 5})

    cfg = make_config(edit)
    device = SimDevice(cfg, single_boot_plan(cfg))
    emit(device, cfg, "emg_lat_left", n=1000)  # 10 chunks, todos perdidos
    payload = dump(device.status(np.random.default_rng(0), T0 + timedelta(milliseconds=500)))
    assert payload["status"] == "degraded"
    states = {s["stream_id"]: s["state"] for s in payload["streams"]}
    assert states["emg_lat_left"] == "stalled" and states["imu_l3"] == "active"
    assert validation.errors("telemetry/device-status", payload) == []
    after = dump(device.status(np.random.default_rng(0), T0 + timedelta(seconds=5)))
    assert after["status"] == "ok"


def test_status_offline_when_device_is_off(config):
    plan = plan_device_day(config, 7, ASTRONAUT, DAY)
    device = SimDevice(config, plan)
    payload = dump(device.status(np.random.default_rng(0), datetime(2026, 11, 13, 3, 0, tzinfo=UTC)))
    assert payload["status"] == "offline" and payload["source"] == "simulator"
    assert validation.errors("telemetry/device-status", payload) == []
    assert device.clock_sync(np.random.default_rng(0), datetime(2026, 11, 13, 3, 0, tzinfo=UTC)) is None


def test_status_without_rssi_and_with_rssi():
    cfg = make_config(lambda raw: raw["device"].update(link_type="ble", rssi_dbm={"min": -70, "max": -50}))
    device = SimDevice(cfg, single_boot_plan(cfg))
    payload = dump(device.status(np.random.default_rng(0), T0))
    assert -70 <= payload["link"]["rssi_dbm"] <= -50 and validation.errors("telemetry/device-status", payload) == []
    plain = make_config()
    assert "rssi_dbm" not in dump(SimDevice(plain, single_boot_plan(plain)).status(np.random.default_rng(0), T0))["link"]


def test_clock_sync_validates_and_matches_the_boot_clock(config):
    plan = single_boot_plan(config, drift_ppm=25.0)
    device = SimDevice(config, plan)
    at = T0 + timedelta(minutes=3)
    sync = device.clock_sync(np.random.default_rng(0), at)
    payload = dump(sync)
    assert validation.errors("telemetry/clock-sync", payload) == []
    assert payload["method"] == "simulated" and payload["boot_id"] == 1234
    assert payload["device_ms"] == plan.boots[0].device_ms(at)
    unc = config.faults.device.clock.sync_uncertainty_ms
    assert unc.min <= payload["uncertainty_ms"] <= unc.max


def test_everything_emitted_over_a_day_validates():
    cfg = make_config(lambda raw: (
        raw["faults"]["device"]["packet_loss"].update(probability=0.05),
        raw["faults"]["device"]["reboot"].update(probability_per_hour=1.0),
    ))  # fmt: skip
    plan = plan_device_day(cfg, 11, ASTRONAUT, DAY)
    device = SimDevice(cfg, plan)
    start = plan.power_on
    n_clock = 0
    for minute in range(0, 16 * 60, 90):
        at = start + timedelta(minutes=minute)
        sync = device.clock_sync(np.random.default_rng(minute), at)
        if sync is not None:
            assert validation.errors("telemetry/clock-sync", dump(sync)) == []
            n_clock += 1
        for sid in STREAMS:
            for o in emit(device, cfg, sid, n=cfg.signals.samples_per_chunk(cfg.signals.rate(STREAMS[sid])) * 30, start=at, seed=minute):
                if not o.lost:
                    assert validation.errors(SCHEMA, dump(o.chunk)) == []
        assert validation.errors("telemetry/device-status", dump(device.status(np.random.default_rng(minute), at))) == []
    assert n_clock > 0
    assert copy.deepcopy(plan) == plan


def test_chunk_size_is_the_one_given_per_call(config):
    """Mismo tramo de muestras a 10 Hz: 1 chunk de 10 muestras por segundo con chunk_ms=1000 (fondo), y 1 de
    una sola muestra con 100 ms; seq y advance siguen el tamaño de chunk de cada llamada."""
    rate = config.schedule.background.sample_rates_hz.imu
    channels = channels_for(StreamKind.IMU, 100)
    quality = lambda n: clean_quality(StreamKind.IMU, n)
    device = SimDevice(config, single_boot_plan(config))
    heavy = list(device.chunks(np.random.default_rng(0), stream_id="imu_l3", session_id=SESSION, start=T0, rate_hz=rate, chunk_ms=100.0,
                               channels=channels, quality=quality(100), calibration_id=None))  # fmt: skip
    device = SimDevice(config, single_boot_plan(config))
    light = list(device.chunks(np.random.default_rng(0), stream_id="imu_l3", session_id=SESSION, start=T0, rate_hz=rate, chunk_ms=1000.0,
                               channels=channels, quality=quality(10), calibration_id=None))  # fmt: skip
    assert (len(heavy), len(light)) == (100, 10)
    assert {o.chunk.n_samples for o in heavy} == {1} and {o.chunk.n_samples for o in light} == {10}
    assert [o.seq for o in light] == list(range(10)) and device.next_seq(1234, "imu_l3") == 10
    other = SimDevice(config, single_boot_plan(config))
    other.advance("imu_l3", start=T0, n_samples=100, rate_hz=rate, chunk_ms=1000.0)
    assert other.next_seq(1234, "imu_l3") == 10


def test_status_last_seq_uses_published_when_given(config):
    device = SimDevice(config, single_boot_plan(config))
    emit(device, config, "emg_lat_left", n=1000)  # el dispositivo ya consumió seq 0..9
    at = T0 + timedelta(seconds=1)
    by_id = lambda **kw: {s["stream_id"]: s for s in dump(device.status(np.random.default_rng(0), at, **kw))["streams"]}
    assert by_id()["emg_lat_left"]["last_seq"] == 9  # sin published: next_seq - 1
    published = by_id(published={"emg_lat_left": 4})
    assert published["emg_lat_left"]["last_seq"] == 4 and "last_seq" not in published["imu_l3"]
    assert "last_seq" not in by_id(published={})["emg_lat_left"]  # nada publicado todavía: no declara seq
    payload = dump(device.status(np.random.default_rng(0), at, published={"emg_lat_left": 4}))
    assert validation.errors("telemetry/device-status", payload) == []
    # Mismos números aleatorios con o sin published.
    assert dump(device.status(np.random.default_rng(0), at))["temperature_c"] == payload["temperature_c"]
