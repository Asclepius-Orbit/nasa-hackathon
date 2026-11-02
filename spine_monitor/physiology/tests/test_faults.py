import copy
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import yaml
from spine_contracts import QualityFlag, StreamKind
from spine_physiology.config import MissionConfig
from spine_physiology.faults import (
    CLEAN,
    QUALITY_KEYS,
    FaultEpisode,
    QualityState,
    apply_quality_faults,
    clean_quality,
    mask_quality,
    plan_packet_loss,
    plan_quality_faults,
    plan_reboots,
    sample_drift_ppm,
    sample_latency_ms,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
FLAGS = [f.value for f in QualityFlag if f.value in {"clipping", "lead_off", "motion_artifact", "powerline_noise", "low_snr",
                                                        "saturated_adc", "out_of_calibration", "sensor_warmup"}]  # fmt: skip
SPC = 100


def make_config(**flag_probabilities: float) -> MissionConfig:
    """Config de ejemplo con todos los flags a probabilidad 0 salvo los indicados; kinds = los tres."""
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    for name in FLAGS:
        quality = raw["faults"]["quality"][name]
        quality["probability"] = flag_probabilities.get(name, 0.0)
        quality["kinds"] = ["emg", "imu", "stretch"]
    return MissionConfig.model_validate(raw)


def signal(kind: StreamKind, n: int = 2000, seed: int = 3) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    if kind is StreamKind.EMG:
        return {"emg": rng.normal(0, 0.2, n)}
    if kind is StreamKind.IMU:
        return {c: rng.normal(0.1 if c.startswith("accel") else 5.0, 0.05, n) for c in ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z")}
    return {"adc_counts": np.rint(2000 + 30 * np.sin(np.arange(n) / 50) + rng.normal(0, 2, n)).astype(np.int64)}


def run(config: MissionConfig, kind: StreamKind, channels, rate_hz: float = 1000.0, seed: int = 1):
    faults = config.faults.quality
    n = len(next(iter(channels.values())))
    episodes = plan_quality_faults(np.random.default_rng(seed), faults, stream_id="s_01", kind=kind, n_samples=n, rate_hz=rate_hz, samples_per_chunk=SPC)
    altered, quality = apply_quality_faults(
        np.random.default_rng(seed + 1), episodes, kind=kind, channels=channels, rate_hz=rate_hz,
        samples_per_chunk=SPC, signals=config.signals, faults=faults,
    )  # fmt: skip
    return episodes, altered, quality


@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("kind", list(StreamKind))
def test_probability_one_flags_and_alters_signal(flag, kind):
    config = make_config(**{flag: 1.0})
    channels = signal(kind)
    originals = {k: v.copy() for k, v in channels.items()}
    episodes, altered, quality = run(config, kind, channels)

    assert episodes and episodes[0].start_sample == 0 and all(e.flag.value == flag for e in episodes)
    assert any(not np.array_equal(altered[k], channels[k]) for k in channels)
    flagged = [q for q in quality if any(QualityFlag(flag) in s.flags for s in q.values())]
    assert flagged
    for q in flagged:
        for state in q.values():
            assert state.score < 1.0 and QualityFlag(flag) in state.flags
    # Nunca in place.
    for k in channels:
        np.testing.assert_array_equal(channels[k], originals[k])


@pytest.mark.parametrize("kind", list(StreamKind))
def test_probability_zero_leaves_signal_untouched(kind):
    config = make_config()
    channels = signal(kind)
    episodes, altered, quality = run(config, kind, channels)
    assert episodes == ()
    for k in channels:
        np.testing.assert_array_equal(altered[k], channels[k])
        assert altered[k] is not channels[k]
    assert quality == clean_quality(kind, 20)


def test_quality_has_all_keys_and_chunk_count():
    config = make_config(low_snr=1.0)
    for kind in StreamKind:
        _, _, quality = run(config, kind, signal(kind, n=1050))
        assert len(quality) == 11  # el último chunk es parcial
        assert all(set(q) == set(QUALITY_KEYS[kind]) for q in quality)


def test_stretch_counts_stay_integer_and_inside_adc():
    config = make_config(lead_off=1.0, low_snr=1.0, out_of_calibration=1.0)
    _, altered, _ = run(config, StreamKind.STRETCH, signal(StreamKind.STRETCH), rate_hz=20.0)
    counts = altered["adc_counts"]
    assert counts.dtype == np.int64
    assert counts.min() >= 0 and counts.max() <= (1 << config.signals.stretch.circuit.adc_bits) - 1


def test_stretch_length_quality_inherits_adc_quality():
    config = make_config(saturated_adc=1.0)
    _, altered, quality = run(config, StreamKind.STRETCH, signal(StreamKind.STRETCH))
    assert (altered["adc_counts"] == (1 << config.signals.stretch.circuit.adc_bits) - 1).any()
    assert all(q["adc_counts"] == q["length_mm"] for q in quality)


def test_clipping_limits_to_amplitude_times_peak():
    config = make_config(clipping=1.0)
    channels = signal(StreamKind.EMG)
    _, altered, _ = run(config, StreamKind.EMG, channels)
    amp = config.faults.quality.clipping.amplitude
    first_chunk_peak = np.abs(channels["emg"][:SPC]).max()
    assert np.abs(altered["emg"][:SPC]).max() <= amp * first_chunk_peak + 1e-12


def test_powerline_noise_has_the_configured_frequency():
    config = make_config(powerline_noise=1.0)
    flat = {"emg": np.zeros(4000)}
    episode = FaultEpisode(QualityFlag.POWERLINE_NOISE, "s_01", 0, 4000, 0.6, 0.5)
    altered, _ = apply_quality_faults(
        np.random.default_rng(0), [episode], kind=StreamKind.EMG, channels=flat, rate_hz=1000.0,
        samples_per_chunk=SPC, signals=config.signals, faults=config.faults.quality,
    )  # fmt: skip
    spectrum = np.abs(np.fft.rfft(altered["emg"]))
    freqs = np.fft.rfftfreq(4000, 1 / 1000.0)
    assert freqs[spectrum.argmax()] == config.faults.quality.powerline_noise.frequency_hz


def test_episodes_are_ordered_and_do_not_overlap_per_flag():
    config = make_config(low_snr=0.5, clipping=0.5)
    episodes, _, _ = run(config, StreamKind.EMG, signal(StreamKind.EMG, n=20000))
    assert [e.start_sample for e in episodes] == sorted(e.start_sample for e in episodes)
    for flag in (QualityFlag.LOW_SNR, QualityFlag.CLIPPING):
        mine = [e for e in episodes if e.flag is flag]
        assert all(a.end_sample <= b.start_sample for a, b in zip(mine, mine[1:], strict=False))
    assert all(0 <= e.start_sample < e.end_sample <= 20000 for e in episodes)


def test_planning_is_deterministic_and_respects_kinds():
    config = make_config(low_snr=0.2)
    raw = copy.deepcopy(config.model_dump(mode="json"))
    raw["faults"]["quality"]["low_snr"]["kinds"] = ["imu"]
    narrowed = MissionConfig.model_validate(raw)
    args = dict(stream_id="s_01", n_samples=5000, rate_hz=1000.0, samples_per_chunk=SPC)
    a = plan_quality_faults(np.random.default_rng(5), config.faults.quality, kind=StreamKind.EMG, **args)
    b = plan_quality_faults(np.random.default_rng(5), config.faults.quality, kind=StreamKind.EMG, **args)
    assert a == b and a
    assert plan_quality_faults(np.random.default_rng(5), narrowed.faults.quality, kind=StreamKind.EMG, **args) == ()


def test_quality_state_merge_and_contract():
    a = QualityState(0.6, frozenset({QualityFlag.LOW_SNR}))
    b = QualityState(0.8, frozenset({QualityFlag.PACKET_GAP}))
    merged = a.merge(b).to_contract()
    assert merged.score == 0.6 and merged.assessed_by.value == "gateway"
    assert {f.value for f in merged.flags} == {"low_snr", "packet_gap"}
    assert CLEAN.merge(a) == a


def test_mask_quality_marks_chunks_touched_by_the_mask():
    mask = np.zeros(450, dtype=bool)
    mask[150:160] = True
    mask[440] = True  # chunk parcial final
    quality = mask_quality(mask, kind=StreamKind.STRETCH, key="length_mm", flag=QualityFlag.BAND_RECOVERING, score=0.7, samples_per_chunk=100)
    assert len(quality) == 5
    assert [q["length_mm"] != CLEAN for q in quality] == [False, True, False, False, True]
    assert all(q["adc_counts"] == CLEAN for q in quality)
    assert quality[1]["length_mm"] == QualityState(0.7, frozenset({QualityFlag.BAND_RECOVERING}))
    with pytest.raises(ValueError):
        mask_quality(mask, kind=StreamKind.EMG, key="accel", flag=QualityFlag.LOW_SNR, score=0.5, samples_per_chunk=100)


# --- fallos de dispositivo ----------------------------------------------------------------------


def test_packet_loss_probability_extremes_and_bursts():
    config = make_config()
    cfg = config.faults.device.packet_loss
    assert not plan_packet_loss(np.random.default_rng(0), cfg.model_copy(update={"probability": 0.0}), 500).any()
    assert plan_packet_loss(np.random.default_rng(0), cfg.model_copy(update={"probability": 1.0}), 500).all()
    lost = plan_packet_loss(np.random.default_rng(1), cfg.model_copy(update={"probability": 0.05}), 5000)
    assert lost.any() and not lost.all()
    edges = np.flatnonzero(np.diff(np.concatenate(([0], lost.astype(int), [0]))))
    runs = edges[1::2] - edges[::2]
    assert runs.min() >= cfg.burst_chunks.min
    # Las ráfagas pueden encadenarse (suman), pero ninguna empieza ya dentro de otra: el tamaño es múltiplo acotado.
    assert (runs <= cfg.burst_chunks.max * 50).all()


def test_reboots_are_ordered_non_overlapping_and_in_window():
    config = make_config()
    cfg = config.faults.device.reboot.model_copy(update={"probability_per_hour": 2.0})
    start, end = datetime(2026, 1, 1, 6, tzinfo=UTC), datetime(2026, 1, 1, 22, tzinfo=UTC)
    events = plan_reboots(np.random.default_rng(2), cfg, start=start, end=end)
    assert events
    assert all(start <= e.at < end for e in events)
    for a, b in zip(events, events[1:], strict=False):
        assert (b.at - a.at).total_seconds() >= a.offline_s
    assert all(cfg.offline_s.min <= e.offline_s <= cfg.offline_s.max for e in events)
    assert plan_reboots(np.random.default_rng(2), cfg.model_copy(update={"probability_per_hour": 0.0}), start=start, end=end) == ()


def test_drift_and_latency_within_configured_ranges():
    clock = make_config().faults.device.clock
    rng = np.random.default_rng(4)
    drifts = [sample_drift_ppm(rng, clock) for _ in range(50)]
    assert all(clock.drift_ppm.min <= d <= clock.drift_ppm.max for d in drifts)
    latency = sample_latency_ms(rng, clock, 500)
    assert latency.dtype == np.float64 and latency.shape == (500,)
    assert clock.latency_ms.min <= latency.min() and latency.max() <= clock.latency_ms.max


def test_imu_is_reclipped_to_sensor_range_after_faults():
    config = make_config(out_of_calibration=1.0, low_snr=1.0, motion_artifact=1.0)
    imu = config.signals.imu
    channels = {c: np.full(2000, 0.9 * (imu.accel_range_g if c.startswith("accel") else imu.gyro_range_dps)) for c in signal(StreamKind.IMU)}
    _, altered, _ = run(config, StreamKind.IMU, channels)
    for name, arr in altered.items():
        limit = imu.accel_range_g if name.startswith("accel") else imu.gyro_range_dps
        assert np.abs(arr).max() <= limit
        assert np.abs(arr).max() == limit  # el fallo sí llegó a saturar


def test_motion_artifact_band_and_warmup_decay_come_from_config():
    base = make_config(motion_artifact=1.0, sensor_warmup=1.0)
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw = copy.deepcopy(raw)
    for name in FLAGS:
        raw["faults"]["quality"][name].update(probability=1.0 if name in {"motion_artifact", "sensor_warmup"} else 0.0, kinds=["emg", "imu", "stretch"])
    raw["faults"]["quality"]["motion_artifact"].update(band_low_hz=2.0, band_high_hz=3.0, max_fraction_of_rate=0.4)
    raw["faults"]["quality"]["sensor_warmup"]["decay_fraction"] = 0.05
    other = MissionConfig.model_validate(raw)
    channels = signal(StreamKind.EMG)
    _, a, _ = run(base, StreamKind.EMG, channels)
    _, b, _ = run(other, StreamKind.EMG, channels)
    assert not np.array_equal(a["emg"], b["emg"])
