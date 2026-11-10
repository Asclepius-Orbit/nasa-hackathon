"""RepDetector against the real simulator signal, with rep_schedule as the ground truth."""

import math
from collections.abc import Iterable, Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import spine_physiology
from hypothesis import given, settings
from hypothesis import strategies as st
from monitor.live.reps import RepDetector, RepDetectorParams
from spine_physiology.config import ExerciseProtocol, load_config
from spine_physiology.device import SimDevice, plan_device_live
from spine_physiology.envelope import EnvelopeKind
from spine_physiology.generator import MissionGenerator
from spine_physiology.signals import exercise_duration_s, rep_schedule

# From the installed simulator package, not this file: mutmut runs a copy of the tests in another directory.
CONFIG_PATH = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
PROTOCOL_ID = "lat_pulldown_standing_v1"
SESSION_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
PREFLIGHT_DAY = 1  # earth_1g segment of the example mission
INFLIGHT_DAY = 10  # last microgravity day: the strongest tremor of the inflight segment
DURATION_TOLERANCE_S = 0.5
GYRO_AXES = ("gyro_x", "gyro_y", "gyro_z")

Chunk = dict[str, Any]
Sample = tuple[float, float, float, float]


@cache
def _protocol() -> ExerciseProtocol:
    protocol = load_config(CONFIG_PATH).protocols[PROTOCOL_ID]
    assert isinstance(protocol, ExerciseProtocol)
    return protocol


@cache
def _cached_chunks(day: int, astronaut: str = "AST-01", session_id: str = SESSION_ID) -> tuple[Chunk, ...]:
    config = load_config(CONFIG_PATH)
    start = datetime(2030, 1, 1, 10, tzinfo=UTC)
    plan = plan_device_live(config, config.seed, astronaut, boot_at=start - timedelta(hours=1), horizon=timedelta(hours=1))
    stream = MissionGenerator(config).live_session(
        astronaut_id=astronaut, day=day, device=SimDevice(config, plan), session_id=session_id, session_type="exercise",
        protocol_id=PROTOCOL_ID, stream_ids=("imu_l3",), start=start,
    )  # fmt: skip
    chunks: list[Chunk] = []
    for envelope in stream:
        if envelope.kind is not EnvelopeKind.STREAM_CHUNK:
            continue
        chunks.append(envelope.payload)
        if _elapsed_s(envelope.payload, chunks[0]) > exercise_duration_s(_protocol()):
            return tuple(chunks)
    raise AssertionError("the live session iterator never ends")


def _elapsed_s(chunk: Mapping[str, Any], first: Mapping[str, Any]) -> float:
    return (chunk["t0_device_ms"] - first["t0_device_ms"]) / 1000.0


def _run(chunks: Iterable[Mapping[str, Any]]) -> RepDetector:
    detector = RepDetector(RepDetectorParams.default())
    for chunk in chunks:
        detector.feed(chunk)
    return detector


def _mean_accel_g(chunks: Iterable[Mapping[str, Any]]) -> float:
    """Mean |accel| over the given chunks: tells earth_1g and microgravity apart."""
    norms = [
        math.hypot(ax, ay, az)
        for chunk in chunks
        for ax, ay, az in zip(chunk["data"]["accel_x"], chunk["data"]["accel_y"], chunk["data"]["accel_z"], strict=True)
    ]
    return sum(norms) / len(norms)


def _lead_in(chunks: tuple[Chunk, ...]) -> list[Chunk]:
    return [c for c in chunks if _elapsed_s(c, chunks[0]) + c["n_samples"] / c["sample_rate_hz"] < _protocol().lead_in_s]


def _chunk_index_after(chunks: tuple[Chunk, ...], elapsed_s: float) -> int:
    return next(i for i, c in enumerate(chunks) if _elapsed_s(c, chunks[0]) > elapsed_s)


@pytest.mark.parametrize(("day", "gravity_g"), [(PREFLIGHT_DAY, 1.0), (INFLIGHT_DAY, 0.0)])
def test_detects_every_scheduled_rep_in_order_with_its_duration(day: int, gravity_g: float) -> None:
    chunks = _cached_chunks(day)
    assert _mean_accel_g(_lead_in(chunks)) == pytest.approx(gravity_g, abs=0.05)  # really this gravity environment

    protocol = _protocol()
    schedule = rep_schedule(protocol)
    detector = RepDetector(RepDetectorParams.default())
    trace: list[tuple[int, str]] = []
    onsets: dict[int, float] = {}
    durations: dict[int, float] = {}
    for chunk in chunks:
        detector.feed(chunk)
        snap = detector.snapshot()
        if not trace or trace[-1] != (snap["rep"], snap["phase"]):
            trace.append((snap["rep"], snap["phase"]))
        onsets.setdefault(snap["rep"], _elapsed_s(chunk, chunks[0]))
        if snap["last"] is not None:
            durations[snap["last"]["rep"]] = snap["last"]["duration_s"]

    expected = [(0, "resting")]
    for rep in schedule:
        expected += [(rep.index, "pulling"), (rep.index, "letting_up"), (rep.index, "resting")]
    assert trace == expected
    assert len(schedule) == protocol.planned_reps

    assert sorted(durations) == [rep.index for rep in schedule]
    for rep in schedule:
        assert durations[rep.index] == pytest.approx(protocol.rep_duration_s, abs=DURATION_TOLERANCE_S)
        assert rep.start_s <= onsets[rep.index] <= rep.start_s + protocol.rep_duration_s / 2


@pytest.mark.parametrize("day", [PREFLIGHT_DAY, INFLIGHT_DAY])
def test_lead_in_without_movement_never_invents_a_rep(day: int) -> None:
    detector = RepDetector(RepDetectorParams.default())
    changes = [detector.feed(chunk) for chunk in _lead_in(_cached_chunks(day))]
    assert detector.snapshot() == {"rep": 0, "phase": "resting", "progress_pct": None, "last": None}
    assert not any(changes)


def test_a_fault_during_a_rep_does_not_freeze_the_counter() -> None:
    """Real session where low_snr hits rep 7: its angle never gets back to 0, and the counter used to stay there."""
    chunks = _cached_chunks(0, "AST-02", "aaaaaaaa-bbbb-4ccc-8ddd-000000000000")
    rep7 = rep_schedule(_protocol())[6]
    assert any("low_snr" in c["quality"]["gyro"]["flags"] and rep7.start_s <= _elapsed_s(c, chunks[0]) <= rep7.end_s for c in chunks)
    detector = RepDetector(RepDetectorParams.default())
    completed: set[int] = set()
    for chunk in chunks:
        detector.feed(chunk)
        last = detector.snapshot()["last"]
        if last is not None:
            completed.add(last["rep"])
    assert (detector.snapshot()["rep"], detector.snapshot()["phase"]) == (_protocol().planned_reps, "resting")
    assert completed == set(range(1, _protocol().planned_reps + 1))


def test_progress_is_none_on_first_rep_and_resting_then_relative_to_previous_peak() -> None:
    detector = RepDetector(RepDetectorParams.default())
    peaks: dict[int, float] = {}
    for chunk in _cached_chunks(PREFLIGHT_DAY):
        detector.feed(chunk)
        snap = detector.snapshot()
        progress = snap["progress_pct"]
        if snap["rep"] <= 1 or snap["phase"] == "resting":
            assert progress is None
        else:
            assert progress is not None
            assert 0.0 <= progress <= 100.0
            peaks[snap["rep"]] = max(peaks.get(snap["rep"], 0.0), progress)
    assert sorted(peaks) == list(range(2, _protocol().planned_reps + 1))
    assert all(peak >= 90.0 for peak in peaks.values())  # same ROM every rep: each one reaches the previous peak


@pytest.mark.parametrize(("order", "sign"), [(("gyro_y", "gyro_z", "gyro_x"), 1), (GYRO_AXES, -1)])
def test_sensor_mount_does_not_matter(order: tuple[str, str, str], sign: int) -> None:
    def remount(chunk: Chunk) -> Chunk:
        data = dict(chunk["data"])
        for target, source in zip(GYRO_AXES, order, strict=True):
            data[target] = [sign * value for value in chunk["data"][source]]
        return {**chunk, "data": data}

    snapshot = _run(remount(c) for c in _cached_chunks(PREFLIGHT_DAY)).snapshot()
    assert snapshot["rep"] == _protocol().planned_reps
    assert snapshot["phase"] == "resting"


def test_redelivered_and_stale_chunks_are_ignored() -> None:
    chunks = _cached_chunks(PREFLIGHT_DAY)
    detector = RepDetector(RepDetectorParams.default())
    for index, chunk in enumerate(chunks):
        detector.feed(chunk)
        before = detector.snapshot()
        assert detector.feed(chunk) is False  # duplicate
        assert detector.feed(chunks[max(index - 3, 0)]) is False  # older chunk, late
        assert detector.snapshot() == before
    assert detector.snapshot() == _run(chunks).snapshot()


def test_swapped_chunks_equal_a_stream_without_the_late_one_and_keep_the_reps() -> None:
    chunks = _cached_chunks(PREFLIGHT_DAY)
    late = _chunk_index_after(chunks, rep_schedule(_protocol())[2].start_s + 1.0)  # in the middle of a rep
    swapped = (*chunks[:late], chunks[late + 1], chunks[late], *chunks[late + 2 :])
    without = (*chunks[:late], *chunks[late + 1 :])
    assert _run(swapped).snapshot() == _run(without).snapshot()
    assert _run(swapped).snapshot()["rep"] == _protocol().planned_reps


def test_new_boot_restarts_the_integration_and_keeps_the_counter() -> None:
    chunks = _cached_chunks(PREFLIGHT_DAY)
    schedule = rep_schedule(_protocol())
    cut = _chunk_index_after(chunks, schedule[2].start_s + 1.0)
    detector = _run(chunks[:cut])
    before = detector.snapshot()
    assert (before["rep"], before["phase"]) == (3, "pulling")

    # Reboot in the middle of rep 3; the device comes back in the rest before rep 4, with t0 and seq restarted.
    resume = _chunk_index_after(chunks, schedule[3].start_s - 2.0)
    rebooted = [
        {**c, "boot_id": chunks[0]["boot_id"] + 1, "t0_device_ms": c["t0_device_ms"] - chunks[resume]["t0_device_ms"] + 5, "seq": i}
        for i, c in enumerate(chunks[resume:])
    ]
    assert detector.feed(rebooted[0]) is True
    assert detector.snapshot() == {"rep": 3, "phase": "resting", "progress_pct": None, "last": before["last"]}

    assert detector.feed(chunks[cut]) is False  # a straggler of the old boot does not rewind to it
    assert detector.snapshot()["rep"] == 3

    for chunk in rebooted[1:]:
        detector.feed(chunk)
    final = detector.snapshot()
    assert final["rep"] == _protocol().planned_reps
    assert final["phase"] == "resting"
    assert final["last"] is not None
    assert final["last"]["duration_s"] == pytest.approx(_protocol().rep_duration_s, abs=DURATION_TOLERANCE_S)


def test_chunks_of_another_stream_kind_are_ignored() -> None:
    chunk = {"stream_kind": "emg", "boot_id": 1, "t0_device_ms": 0, "sample_rate_hz": 1000.0, "n_samples": 1, "data": {"emg": [0.0]}}
    assert RepDetector(RepDetectorParams.default()).feed(chunk) is False


def test_params_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        RepDetectorParams.default().on_dps = 1.0  # pyright: ignore[reportAttributeAccessIssue]  # the point of the test


def _samples(chunks: Iterable[Mapping[str, Any]]) -> list[Sample]:
    return [
        (chunk["t0_device_ms"] + i * 1000.0 / chunk["sample_rate_hz"], chunk["data"]["gyro_x"][i], chunk["data"]["gyro_y"][i], chunk["data"]["gyro_z"][i])
        for chunk in chunks
        for i in range(chunk["n_samples"])
    ]


def _rechunk(samples: list[Sample], sizes: list[int]) -> Iterable[Chunk]:
    """Split the samples using `sizes` cyclically, also cutting at every lost packet (a chunk has a constant rate)."""
    index, turn = 0, 0
    period_ms = samples[1][0] - samples[0][0]
    while index < len(samples):
        stop = min(index + sizes[turn % len(sizes)], len(samples))
        end = next((j for j in range(index + 1, stop) if samples[j][0] - samples[j - 1][0] != period_ms), stop)
        part = samples[index:end]
        yield {
            "stream_kind": "imu", "boot_id": 7, "t0_device_ms": int(part[0][0]), "sample_rate_hz": 1000.0 / period_ms, "n_samples": len(part), "seq": turn,
            "data": {"gyro_x": [s[1] for s in part], "gyro_y": [s[2] for s in part], "gyro_z": [s[3] for s in part]},
        }  # fmt: skip
        index, turn = end, turn + 1


@cache
def _three_reps() -> tuple[Sample, ...]:
    chunks = _cached_chunks(INFLIGHT_DAY)
    end = rep_schedule(_protocol())[2].end_s + 2.0
    return tuple(_samples(c for c in chunks if _elapsed_s(c, chunks[0]) < end))


@pytest.mark.property
@settings(max_examples=15, deadline=None)
@given(sizes=st.lists(st.integers(min_value=1, max_value=400), min_size=1, max_size=12))
def test_any_partition_into_chunks_gives_the_same_final_state(sizes: list[int]) -> None:
    samples = list(_three_reps())
    reference = _run(_rechunk(samples, [10])).snapshot()
    assert reference["rep"] == 3  # the property is about a stream with real reps in it
    assert _run(_rechunk(samples, sizes)).snapshot() == reference


# --- Noise-free signals: thresholds, holds and constants of RepDetectorParams.default() derived by hand ---

BIAS = (0.5, -0.3, 0.2)
PERIOD_MS = 10  # 100 Hz: the smoothing weight is dt / (tau + dt) = 1/6


def _signal(segments: list[tuple[float, float]], *, axis: int = 2, bias: tuple[float, float, float] = BIAS) -> list[Chunk]:
    """One single-sample chunk per sample, from (duration_s, rate_dps) segments on one axis, plus the bias."""
    rates = [rate for duration_s, rate in segments for _ in range(round(duration_s * 1000 / PERIOD_MS))]
    return [
        {
            "stream_kind": "imu", "boot_id": 1, "t0_device_ms": i * PERIOD_MS, "sample_rate_hz": 1000.0 / PERIOD_MS, "n_samples": 1,
            "data": {name: [bias[n] + (rate if n == axis else 0.0)] for n, name in enumerate(GYRO_AXES)},
        }  # fmt: skip
        for i, rate in enumerate(rates)
    ]


def _transitions(chunks: Iterable[Chunk]) -> list[tuple[float, int, str]]:
    detector = RepDetector(RepDetectorParams.default())
    trace: list[tuple[float, int, str]] = []
    for chunk in chunks:
        before = detector.snapshot()
        changed = detector.feed(chunk)
        after = detector.snapshot()
        assert changed is (after != before)
        if (after["rep"], after["phase"]) != (before["rep"], before["phase"]):
            trace.append((chunk["t0_device_ms"] / 1000.0, after["rep"], after["phase"]))
    return trace


TWO_REPS = [(2.0, 0.0), (1.5, 20.0), (0.5, 0.0), (1.5, -20.0), (2.0, 0.0), (1.5, 10.0), (1.5, -10.0), (2.0, 0.0)]


@pytest.mark.parametrize(("axis", "sign"), [(2, 1.0), (0, -1.0), (1, 1.0)])
def test_exact_transitions_on_any_axis_and_direction(axis: int, sign: float) -> None:
    chunks = _signal([(duration_s, sign * rate) for duration_s, rate in TWO_REPS], axis=axis)
    assert _transitions(chunks) == [
        # Smoothed 20 dps passes on_dps (6) on the 2nd sample (6.11, 2.01 s), plus a 0.1 s hold.
        (2.11, 1, "pulling"),
        # After the 0.5 s pause the first -20 dps sample (4.00 s) already reads -3.33 < -low_dps, plus a 0.1 s hold.
        (4.10, 1, "letting_up"),
        # The rate stops at 5.50 s; 20 * (5/6)^j < low_dps (1.5) from j = 15 (5.64 s), plus a 0.1 s settle.
        (5.74, 1, "resting"),
        # Smoothed 10 dps passes on_dps on the 6th sample (6.65, 7.55 s), plus the hold.
        (7.65, 2, "pulling"),
        # The sign flips at 9.00 s: -10 + 20 * (5/6)^k < -low_dps from k = 5 (9.04 s), plus the hold.
        (9.14, 2, "letting_up"),
        # 10 * (5/6)^j < 1.5 from j = 11 (10.60 s), plus the settle.
        (10.70, 2, "resting"),
    ]


def test_duration_and_progress_values() -> None:
    detector = RepDetector(RepDetectorParams.default())
    progress: list[float] = []
    last = None
    for chunk in _signal(TWO_REPS):
        detector.feed(chunk)
        snap = detector.snapshot()
        if snap["last"] is not None and last is None:
            last = snap["last"]
            assert last["rep"] == 1
            # From the last still sample (1.99 s) to the first settled one (5.64 s).
            assert last["duration_s"] == pytest.approx(3.65)
        if snap["progress_pct"] is not None:
            progress.append(snap["progress_pct"])
    assert last is not None
    assert detector.snapshot()["last"] == {"rep": 2, "duration_s": pytest.approx(10.60 - 7.49)}
    # Rep 1 peaks at 30 deg (20 dps * 1.5 s), so 10 dps * 0.15 s + half a sample = 1.55 deg at the first reading of rep 2.
    assert progress[0] == pytest.approx(1.55 / 30.0 * 100.0)
    # Rep 2 turns around with no pause, so its peak is half a sample short of 15 deg.
    assert max(progress) == pytest.approx(14.95 / 30.0 * 100.0)


def test_progress_is_capped_at_100_when_the_rep_exceeds_the_previous_peak() -> None:
    detector = RepDetector(RepDetectorParams.default())
    for chunk in _signal([(2.0, 0.0), (0.75, 20.0), (0.75, -20.0), (2.0, 0.0), (1.5, 20.0)]):  # 15 deg, then 30 deg
        detector.feed(chunk)
    assert detector.snapshot()["progress_pct"] == 100.0


@pytest.mark.parametrize(("back_s", "end_s"), [(1.36, 5.60), (1.34, 6.48)])
def test_rep_end_inside_and_outside_the_return_tolerance(back_s: float, end_s: float) -> None:
    # The return starts at 4.00 s. Back 27.2 deg (2.8 left, inside the 3 deg tolerance): the rate stops at 5.36 s, is low from 5.50 s, plus the settle.
    # Back 26.8 deg (3.2 left, outside): the rate stops at 5.34 s, is low from 5.48 s, plus the 1 s calm.
    trace = _transitions(_signal([(2.0, 0.0), (1.5, 20.0), (0.5, 0.0), (back_s, -20.0), (3.0, 0.0)]))
    assert trace[-1] == (end_s, 1, "resting")


def test_a_rep_that_stays_calm_away_from_zero_is_dated_at_the_start_of_the_calm() -> None:
    detector = RepDetector(RepDetectorParams.default())
    for chunk in _signal([(2.0, 0.0), (1.5, 20.0), (0.5, 0.0), (0.5, -20.0), (3.0, 0.0)]):  # 20 deg left when it stops
        detector.feed(chunk)
    # From the last still sample (1.99 s) to the first low rate of the calm: the rate stops at 4.50 s and is low from 4.64 s.
    assert detector.snapshot() == {"rep": 1, "phase": "resting", "progress_pct": None, "last": {"rep": 1, "duration_s": pytest.approx(4.64 - 1.99)}}


@pytest.mark.parametrize(("pause_s", "phase"), [(1.14, "letting_up"), (1.15, "resting")])
def test_the_calm_must_last_its_full_time_to_end_the_rep(pause_s: float, phase: str) -> None:
    # The rate stops at 4.50 s and is low from 4.64 s, so its last sample in the pause is 0.99 s or 1.00 s later.
    trace = _transitions(_signal([(2.0, 0.0), (1.5, 20.0), (0.5, 0.0), (0.5, -20.0), (pause_s, 0.0)]))
    assert trace[-1][1:] == (1, phase)


def test_a_calm_of_a_previous_rep_does_not_end_the_next_one() -> None:
    """Regression: rep 1 ends with a calm under way; a leftover of it would end rep 2 at its first low sample and invent a rep from the rest of its return."""
    segments = [*TWO_REPS[:5], (1.5, 20.0), (0.5, 0.0), (0.26, -1.6), (3.0, 0.0)]
    # -1.6 dps passes -low_dps on its 16th sample (9.65 s), plus the 0.1 s hold: letting_up at 9.75 s, the last sample of that segment.
    # The rate is low from 9.76 s, 29 deg away from the start, so only the full 1 s calm ends the rep.
    assert _transitions(_signal(segments))[-2:] == [(9.75, 2, "letting_up"), (10.76, 2, "resting")]


def _with_gap(chunks: list[Chunk], at_s: float, gap_s: float) -> list[Chunk]:
    """Delay the samples from `at_s` on, so that `gap_s` separates two consecutive samples."""
    extra_ms = round(gap_s * 1000) - PERIOD_MS
    at_ms = round(at_s * 1000)
    return [{**c, "t0_device_ms": c["t0_device_ms"] + extra_ms} if c["t0_device_ms"] >= at_ms else c for c in chunks]


GAP_AT_S = 8.5  # in the middle of the pull of rep 2 of the signal below
REP_THEN_PULL = [(2.0, 0.0), (1.5, 20.0), (0.5, 0.0), (1.5, -20.0), (2.0, 0.0), (1.5, 20.0), (2.0, 0.0)]


def test_a_gap_over_the_limit_cuts_the_rep_without_integrating_across_it() -> None:
    chunks = _signal(REP_THEN_PULL)
    before = _run(c for c in chunks if c["t0_device_ms"] < GAP_AT_S * 1000).snapshot()
    assert (before["rep"], before["phase"]) == (2, "pulling")

    detector = _run(_with_gap(chunks, GAP_AT_S, 2.01)[: round(GAP_AT_S * 100) + 1])
    assert detector.snapshot() == {"rep": 2, "phase": "resting", "progress_pct": None, "last": before["last"]}

    # The cut rep leaves no peak behind: a rep of 15 deg afterwards is still measured against the 30 deg of rep 1.
    after = _signal([(2.0, 0.0), (0.75, 20.0), (0.5, 0.0)])
    t0_ms = chunks[round(GAP_AT_S * 100)]["t0_device_ms"] + 2000
    readings: list[float] = []
    for chunk in after:
        detector.feed({**chunk, "t0_device_ms": t0_ms + chunk["t0_device_ms"]})
        progress = detector.snapshot()["progress_pct"]
        if progress is not None:
            readings.append(progress)
    assert detector.snapshot()["rep"] == 3
    assert max(readings) == pytest.approx(50.0)


def test_a_gap_at_the_limit_is_bridged() -> None:
    # The trapezoid across 2 s at 20 dps adds 40 deg to the 10 deg already pulled: over the 30 deg of rep 1, so the cap shows it was integrated.
    detector = _run(_with_gap(_signal(REP_THEN_PULL), GAP_AT_S, 2.0)[: round(GAP_AT_S * 100) + 1])
    assert detector.snapshot()["rep"] == 2
    assert detector.snapshot()["phase"] == "pulling"
    assert detector.snapshot()["progress_pct"] == 100.0


def test_no_rep_starts_before_the_bias_estimate_has_settled() -> None:
    assert _transitions(_signal([(0.5, 0.0), (0.3, 20.0), (0.2, 0.0)])) == []


def test_movement_under_the_on_threshold_is_not_a_rep() -> None:
    assert _transitions(_signal([(2.0, 0.0), (2.0, 5.9), (2.0, -5.9), (1.0, 0.0)])) == []


def test_a_short_burst_above_the_threshold_does_not_pass_the_hold() -> None:
    # One 100 dps sample smooths to 16.7 and decays below on_dps (6) in 6 samples, 0.05 s: shorter than the hold.
    assert _transitions(_signal([(2.0, 0.0), (0.01, 100.0), (2.0, 0.0)])) == []


def test_the_bias_follows_a_slow_shift_at_rest_with_its_time_constant() -> None:
    """A 0.7 dps shift held for 4 s leaves `residual` unestimated (0.995 per sample: weight dt/tau), integrated by rep 1 for 2.5 s."""
    segments = [(6.0, 0.0), (1.5, 20.0), (1.0, 0.0), (1.5, -20.0), (2.0, 0.0), (1.5, 20.0)]
    steady = _signal([(2.0, 0.0)])
    shifted = _signal(segments, bias=(BIAS[0], BIAS[1], BIAS[2] + 0.7))
    detector = RepDetector(RepDetectorParams.default())
    for chunk in (*steady, *shifted[len(steady) :]):
        detector.feed(chunk)
    residual = 0.7 * 0.995**400
    # The rest after rep 1 keeps converging for 186 still samples. Rep 2 is read at its last rising sample, half a sample short of 30 deg.
    rep2_angle = 29.9 + 1.5 * residual * 0.995**186
    rep1_peak = 30.0 + 2.5 * residual
    assert detector.snapshot()["progress_pct"] == pytest.approx(rep2_angle / rep1_peak * 100.0, abs=0.02)


# --- a boot that starts moving, and a bias that goes wrong --------------------------------------------


def _minimum_jerk_cycles(start_s: float, cycles: int) -> list[tuple[float, float]]:
    """Segments of `start_s` of stillness and then the simulated cycle: 0 -> 30 -> 0 deg in 4 s, then 4 s of rest."""
    half = [30.0 / 2.0 * 30.0 * u**2 * (1.0 - u) ** 2 for u in ((i + 0.5) / 200 for i in range(200))]
    cycle = [(0.01, rate) for rate in (*half, *(-rate for rate in half))] + [(4.0, 0.0)]
    return [(start_s, 0.0), *cycle * cycles]


def _durations(chunks: Iterable[Chunk]) -> dict[int, float]:
    detector = RepDetector(RepDetectorParams.default())
    durations: dict[int, float] = {}
    for chunk in chunks:
        detector.feed(chunk)
        last = detector.snapshot()["last"]
        if last is not None:
            durations[last["rep"]] = last["duration_s"]
    return durations


@pytest.mark.parametrize("start_s", [0.3, 0.5])
def test_a_boot_that_starts_moving_skips_that_rep_and_then_counts_every_other_one(start_s: float) -> None:
    chunks = _signal(_minimum_jerk_cycles(start_s, 6))
    # The first rest is the first second of stillness: nothing is counted before it ends, 1 s after the rep.
    assert all(time_s >= start_s + 4.0 + 1.0 for time_s, _, _ in _transitions(chunks))
    detector = _run(chunks)
    assert (detector.snapshot()["rep"], detector.snapshot()["phase"]) == (5, "resting")
    durations = _durations(chunks)
    assert sorted(durations) == [1, 2, 3, 4, 5]
    assert all(abs(duration - 4.0) <= DURATION_TOLERANCE_S for duration in durations.values())


def test_a_real_session_cut_in_the_middle_of_a_rep_counts_the_rest_of_them() -> None:
    chunks = _cached_chunks(PREFLIGHT_DAY)
    first_rep = rep_schedule(_protocol())[0]
    cut = chunks[_chunk_index_after(chunks, first_rep.start_s + 0.5) :]
    detector = _run(cut)
    expected = _protocol().planned_reps - 1
    assert (detector.snapshot()["rep"], detector.snapshot()["phase"]) == (expected, "resting")
    durations = _durations(cut)
    assert sorted(durations) == list(range(1, expected + 1))
    assert all(abs(duration - _protocol().rep_duration_s) <= DURATION_TOLERANCE_S for duration in durations.values())


def test_a_gap_is_not_rest_for_the_first_bias_estimate() -> None:
    # 0.6 s + 0.6 s still, 1 s apart: no second of real rest, so this rep is not counted.
    chunks = _with_gap(_signal(_minimum_jerk_cycles(1.2, 1)[:-1]), at_s=0.6, gap_s=1.0)
    assert _transitions(chunks) == []


def test_a_rep_that_never_ends_is_cut_at_max_rep_s_and_the_bias_is_estimated_again() -> None:
    """A lasting 8 dps shift of the sensor: the rate never reverses, so with no limit rep 1 would stay in `pulling` for good."""
    shift = 8.0
    segments = [(2.0, 0.0), (15.0, shift), (1.5, shift + 20.0), (0.5, shift), (1.5, shift - 20.0), (2.0, shift)]
    transitions = _transitions(_signal(segments))
    # 8 * (1 - (5/6)^j) passes on_dps from j = 8 (2.07 s), plus the hold. The rep starts at 2.00 s, its last still sample.
    assert transitions[:2] == [(pytest.approx(2.17), 1, "pulling"), (pytest.approx(14.01), 1, "resting")]
    assert [(rep, phase) for _, rep, phase in transitions[2:]] == [(2, "pulling"), (2, "letting_up"), (2, "resting")]
    detector = _run(_signal(segments))
    last = detector.snapshot()["last"]
    assert last is not None
    assert last["rep"] == 2
    assert last["duration_s"] == pytest.approx(3.5, abs=0.3)


# --- non-finite samples -------------------------------------------------------------------------------


def _with_nan_in(chunk: Chunk) -> Chunk:
    gyro = list(chunk["data"]["gyro_y"])
    gyro[len(gyro) // 2] = math.nan
    return {**chunk, "data": {**chunk["data"], "gyro_y": gyro}}


def test_a_chunk_with_a_non_finite_rate_is_dropped_whole_and_logged_once(caplog) -> None:
    chunks = _cached_chunks(PREFLIGHT_DAY)
    rep1 = rep_schedule(_protocol())[0]
    at = {_chunk_index_after(chunks, _protocol().lead_in_s / 2), _chunk_index_after(chunks, rep1.start_s + 1.0)}
    clean = _run(c for i, c in enumerate(chunks) if i not in at).snapshot()

    detector = RepDetector(RepDetectorParams.default())
    for index, chunk in enumerate(chunks):
        if index in at:
            before = detector.snapshot()
            assert detector.feed(_with_nan_in(chunk)) is False
            assert detector.snapshot() == before
        else:
            detector.feed(chunk)

    assert detector.snapshot() == clean
    records = [record for record in caplog.records if "non-finite" in record.getMessage()]
    assert len(records) == 1
    assert SESSION_ID in records[0].getMessage()
