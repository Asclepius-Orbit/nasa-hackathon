"""Respaldo IMU de las repeticiones (#41): picos de |ω| del giróscopo, sin eventos del protocolo.

Las señales sintéticas reproducen el modelo del simulador (flexión minimum-jerk ida y vuelta por rep):
cada rep da un lóbulo de ω de un signo (ida) seguido de otro del signo contrario (vuelta).
"""

import json
import math
import random
import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import spine_physiology
from hypothesis import given, settings
from hypothesis import strategies as st
from spine_contracts import FeaturesRequest
from spine_engine.features.context import ExtractionContext, build_context
from spine_engine.features.repetitions import ImuDetector, RepetitionService
from spine_engine.features.repetitions_imu import detect_imu_windows

_EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "features-request.json"
T0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)
BOOT = 2893746120
RATE = 100.0
CHUNK = 10
# Bordes: el umbral bajo recorta el principio y el final de cada lóbulo (velocidad casi nula).
EDGE_TOLERANCE_S = 0.25


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _min_jerk(tau: float) -> float:
    t = min(max(tau, 0.0), 1.0)
    return 10 * t**3 - 15 * t**4 + 6 * t**5


def _bend(tau: float) -> float:
    return _min_jerk(2 * tau) if tau < 0.5 else _min_jerk(2 - 2 * tau)


def angle_profile(reps: Sequence[tuple[float, float]], n: int, *, rom_deg: float = 30.0, one_way: bool = False) -> list[float]:
    angle = [0.0] * n
    for start, end in reps:
        for i in range(n):
            t = i / RATE
            if start <= t < end:
                tau = (t - start) / (end - start)
                angle[i] = rom_deg * (_min_jerk(tau) if one_way else _bend(tau))
            elif one_way and t >= end:
                angle[i] = rom_deg
    return angle


def gyro_from_angle(angle: Sequence[float]) -> list[float]:
    """Derivada central (deg/s), como ``np.gradient`` del simulador."""
    n = len(angle)
    out = [0.0] * n
    for i in range(n):
        lo, hi = max(i - 1, 0), min(i + 1, n - 1)
        out[i] = (angle[hi] - angle[lo]) * RATE / (hi - lo) if hi > lo else 0.0
    return out


def schedule(n_reps: int, *, lead_in: float = 2.0, duration: float = 4.0, rest: float = 4.0) -> list[tuple[float, float]]:
    return [(lead_in + i * (duration + rest), lead_in + i * (duration + rest) + duration) for i in range(n_reps)]


def imu_chunks(
    axes: Mapping[str, Sequence[float]],
    *,
    stream_id: str = "imu_t9",
    boot_id: int = BOOT,
    t0_device_ms: int = 0,
    seq0: int = 0,
    rate: float = RATE,
    chunk: int = CHUNK,
) -> list[dict[str, Any]]:
    n = len(next(iter(axes.values())))
    channels = {name: list(axes.get(name, [0.0] * n)) for name in ("gyro_x", "gyro_y", "gyro_z")}
    chunks: list[dict[str, Any]] = []
    for k, i0 in enumerate(range(0, n, chunk)):
        i1 = min(i0 + chunk, n)
        size = i1 - i0
        t0 = t0_device_ms + round(i0 * 1000 / rate)
        chunks.append(
            {
                "schema_version": "1.0.0",
                "astronaut_id": "AST-01",
                "device_id": "wb-01",
                "stream_id": stream_id,
                "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01",
                "source": "device",
                "boot_id": boot_id,
                "seq": seq0 + k,
                "t0_device_ms": t0,
                "received_at": (at(t0 / 1000) + timedelta(milliseconds=50)).isoformat(),
                "sample_rate_hz": rate,
                "n_samples": size,
                "stream_kind": "imu",
                "units": {"accel_x": "g", "accel_y": "g", "accel_z": "g", "gyro_x": "deg/s", "gyro_y": "deg/s", "gyro_z": "deg/s"},
                "data": {
                    "accel_x": [0.0] * size,
                    "accel_y": [0.0] * size,
                    "accel_z": [0.0] * size,
                    **{name: values[i0:i1] for name, values in channels.items()},
                },
                "quality": {"accel": {"score": 1.0, "flags": [], "assessed_by": "gateway"}, "gyro": {"score": 1.0, "flags": [], "assessed_by": "gateway"}},
            }
        )
    return chunks


def sync(boot_id: int = BOOT, device_ms: int = 0, utc: datetime = T0) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "boot_id": boot_id,
        "device_ms": device_ms,
        "utc": utc.isoformat(),
        "uncertainty_ms": 5,
        "method": "round_trip",
    }


def make_context(chunks: list[dict[str, Any]], *, syncs: list[dict[str, Any]] | None = None, extra_streams: Sequence[str] = ()) -> ExtractionContext:
    raw = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    # se conservan los chunks EMG y stretch del ejemplo: la petición exige al menos uno
    raw["chunks"] = [c for c in raw["chunks"] if c["stream_kind"] != "imu"] + chunks
    raw["clock_syncs"] = [sync()] if syncs is None else syncs
    raw["session"]["events"] = []
    imu_decl = next(s for s in raw["session"]["streams"] if s["stream_id"] == "imu_t9")
    for stream_id in extra_streams:
        raw["session"]["streams"].append({**imu_decl, "stream_id": stream_id})
    return build_context(FeaturesRequest.model_validate(raw))


def rep_signal(reps: Sequence[tuple[float, float]], n: int, *, sign: float = 1.0, rom_deg: float = 30.0) -> list[float]:
    return [sign * w for w in gyro_from_angle(angle_profile(reps, n, rom_deg=rom_deg))]


def noise(n: int, sigma: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(0.0, sigma) for _ in range(n)]


def test_the_signature_matches_the_imu_detector_alias():
    detector: ImuDetector = detect_imu_windows

    assert detector(make_context([])) == ()


def test_three_clean_reps_give_three_windows_close_to_the_truth():
    reps = schedule(3)
    n = int(28 * RATE)
    windows = detect_imu_windows(make_context(imu_chunks({"gyro_y": rep_signal(reps, n)})))

    assert [w.index for w in windows] == [1, 2, 3]
    for window, (start, end) in zip(windows, reps, strict=True):
        assert abs((window.start - at(start)).total_seconds()) < EDGE_TOLERANCE_S
        assert abs((window.end - at(end)).total_seconds()) < EDGE_TOLERANCE_S
        assert window.duration_s == pytest.approx(end - start, rel=0.1)


def test_lobe_edges_extend_to_where_the_angular_velocity_stops_falling():
    # sin alargar los bordes, el umbral bajo recorta ~4-5 % de cada rep (sus extremos casi quietos)
    reps = schedule(3)
    windows = detect_imu_windows(make_context(imu_chunks({"gyro_y": rep_signal(reps, int(28 * RATE))})))

    for window, (start, end) in zip(windows, reps, strict=True):
        assert window.duration_s == pytest.approx(end - start, rel=0.04)


def test_rest_goes_from_each_end_to_the_next_start_and_the_last_rep_has_no_rest():
    reps = schedule(3)
    windows = detect_imu_windows(make_context(imu_chunks({"gyro_y": rep_signal(reps, int(28 * RATE))})))

    for current, following in zip(windows, windows[1:], strict=False):
        assert current.rest == (current.end, following.start)
    assert windows[-1].rest_start is None
    assert windows[-1].rest_end is None


@pytest.mark.parametrize("axis", ["gyro_x", "gyro_y", "gyro_z"])
@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_the_dominant_axis_and_either_flexion_sign_are_found(axis: str, sign: float):
    reps = schedule(4)
    n = int(36 * RATE)
    axes = {name: noise(n, 0.3, seed) for seed, name in enumerate(("gyro_x", "gyro_y", "gyro_z"))}
    axes[axis] = [a + b for a, b in zip(axes[axis], rep_signal(reps, n, sign=sign), strict=True)]

    windows = detect_imu_windows(make_context(imu_chunks(axes)))

    assert len(windows) == 4
    for window, (start, end) in zip(windows, reps, strict=True):
        assert abs((window.start - at(start)).total_seconds()) < EDGE_TOLERANCE_S
        assert abs((window.end - at(end)).total_seconds()) < EDGE_TOLERANCE_S


def test_a_constant_gyro_bias_does_not_change_the_windows():
    reps = schedule(3)
    n = int(28 * RATE)
    clean = detect_imu_windows(make_context(imu_chunks({"gyro_y": rep_signal(reps, n)})))
    biased = detect_imu_windows(make_context(imu_chunks({"gyro_y": [w + 0.3 for w in rep_signal(reps, n)], "gyro_x": [-0.2] * n})))

    assert [w.active for w in biased] == [w.active for w in clean]


def test_utc_comes_from_the_device_clock_of_the_segment():
    reps = schedule(2)
    n = int(20 * RATE)
    later = T0 + timedelta(hours=1)
    windows = detect_imu_windows(make_context(imu_chunks({"gyro_y": rep_signal(reps, n)}, t0_device_ms=500_000), syncs=[sync(device_ms=500_000, utc=later)]))

    assert len(windows) == 2
    assert abs((windows[0].start - (later + timedelta(seconds=reps[0][0]))).total_seconds()) < EDGE_TOLERANCE_S


def _hump(n: int, start: float, end: float, height: float) -> list[float]:
    return [height * math.sin(math.pi * (i / RATE - start) / (end - start)) if start <= i / RATE < end else 0.0 for i in range(n)]


def test_same_sign_peaks_closer_than_the_minimum_separation_are_one_lobe():
    # ida partida en dos jorobas (picos a 0,22 s) y una vuelta: una sola rep que empieza en la primera joroba
    n = int(6 * RATE)
    parts = (_hump(n, 2.0, 2.1, 20.0), _hump(n, 2.22, 2.32, 20.0), _hump(n, 2.5, 3.5, -20.0))
    signal = [sum(values) for values in zip(*parts, strict=True)]

    (window,) = detect_imu_windows(make_context(imu_chunks({"gyro_y": signal})))

    assert abs((window.start - at(2.0)).total_seconds()) < 0.1
    assert abs((window.end - at(3.5)).total_seconds()) < 0.1


def test_without_imu_chunks_there_are_no_windows():
    assert detect_imu_windows(make_context([])) == ()


def test_a_boot_without_clock_anchor_gives_no_windows():
    reps = schedule(2)
    chunks = imu_chunks({"gyro_y": rep_signal(reps, int(20 * RATE))})

    assert detect_imu_windows(make_context(chunks, syncs=[])) == ()


def test_pure_noise_gives_no_windows():
    n = int(30 * RATE)
    axes = {name: noise(n, 1.0, seed) for seed, name in enumerate(("gyro_x", "gyro_y", "gyro_z"))}

    assert detect_imu_windows(make_context(imu_chunks(axes))) == ()


def test_a_flat_signal_gives_no_windows():
    assert detect_imu_windows(make_context(imu_chunks({"gyro_y": [0.0] * 500}))) == ()


def test_a_single_one_way_bend_is_not_a_repetition():
    n = int(10 * RATE)
    one_way = gyro_from_angle(angle_profile([(2.0, 4.0)], n, one_way=True))

    assert detect_imu_windows(make_context(imu_chunks({"gyro_y": one_way}))) == ()


def test_isolated_non_finite_samples_do_not_break_detection():
    reps = schedule(3)
    signal = rep_signal(reps, int(28 * RATE))
    signal[50] = math.nan
    signal[700] = math.inf

    windows = detect_imu_windows(make_context(imu_chunks({"gyro_y": signal})))

    assert len(windows) == 3


def test_an_all_nan_stream_gives_no_windows():
    assert detect_imu_windows(make_context(imu_chunks({"gyro_x": [math.nan] * 300, "gyro_y": [math.nan] * 300, "gyro_z": [math.nan] * 300}))) == ()


def test_segments_of_different_boots_are_detected_apart_and_merged_in_time():
    other_boot = BOOT + 1
    n = int(20 * RATE)
    first = imu_chunks({"gyro_y": rep_signal(schedule(2), n)})
    # el segundo arranque reinicia el reloj del dispositivo: device_ms vuelve a 0 con otra ancla
    second = imu_chunks({"gyro_y": rep_signal(schedule(1), int(10 * RATE))}, boot_id=other_boot)
    later = T0 + timedelta(minutes=5)

    windows = detect_imu_windows(make_context(second + first, syncs=[sync(), sync(boot_id=other_boot, utc=later)]))

    assert [w.index for w in windows] == [1, 2, 3]
    assert [w.start for w in windows] == sorted(w.start for w in windows)
    assert windows[2].start > later
    assert windows[1].rest == (windows[1].end, windows[2].start)


def test_a_rep_split_across_two_boots_is_not_stitched_together():
    # la ida en un arranque y la vuelta en otro: no hay par dentro de ningún segmento
    other_boot = BOOT + 1
    n = int(8 * RATE)
    signal = rep_signal([(2.0, 6.0)], n)
    half = int(4 * RATE)
    chunks = imu_chunks({"gyro_y": signal[:half]}) + imu_chunks({"gyro_y": signal[half:]}, boot_id=other_boot, t0_device_ms=4000)

    assert detect_imu_windows(make_context(chunks, syncs=[sync(), sync(boot_id=other_boot)])) == ()


def test_with_several_imu_streams_the_first_by_stream_id_with_windows_wins():
    n = int(28 * RATE)
    two = imu_chunks({"gyro_y": rep_signal(schedule(2), n)}, stream_id="imu_u1")
    three = imu_chunks({"gyro_y": rep_signal(schedule(3), n)}, stream_id="imu_t9")
    flat = imu_chunks({"gyro_y": [0.0] * n}, stream_id="imu_a0")

    windows = detect_imu_windows(make_context(two + three + flat, extra_streams=("imu_u1", "imu_a0")))

    assert len(windows) == 3


def test_a_long_pause_between_groups_of_reps_is_a_set_rest_not_a_rep_rest():
    # 2 series de 3 reps: pausa habitual de 4 s entre reps y de 40 s entre series
    first = schedule(3)
    shift = first[-1][1] + 40.0 - first[0][0]
    reps = first + [(a + shift, b + shift) for a, b in first]
    n = int((reps[-1][1] + 4) * RATE)

    windows = detect_imu_windows(make_context(imu_chunks({"gyro_y": rep_signal(reps, n)})))

    assert len(windows) == 6
    assert [w.rest is None for w in windows] == [False, False, True, False, False, True]


def test_the_service_falls_back_to_the_imu_detector_without_events():
    context = make_context(imu_chunks({"gyro_y": rep_signal(schedule(2), int(20 * RATE))}))

    result = RepetitionService(detect_imu_windows).windows(context)

    assert result.source == "imu"
    assert result.count == 2


def test_detection_is_deterministic():
    reps = schedule(3)
    n = int(28 * RATE)
    axes = {"gyro_y": [a + b for a, b in zip(rep_signal(reps, n), noise(n, 1.0, 3), strict=True)]}

    assert detect_imu_windows(make_context(imu_chunks(axes))) == detect_imu_windows(make_context(imu_chunks(axes)))


def test_a_low_rate_stream_is_still_detected():
    rate = 10.0
    reps = schedule(3)
    n = int(28 * rate)
    angle = [30.0 * next((_bend((i / rate - s) / (e - s)) for s, e in reps if s <= i / rate < e), 0.0) for i in range(n)]
    gyro = [(angle[min(i + 1, n - 1)] - angle[max(i - 1, 0)]) * rate / (min(i + 1, n - 1) - max(i - 1, 0)) for i in range(n)]

    windows = detect_imu_windows(make_context(imu_chunks({"gyro_y": gyro}, rate=rate, chunk=5)))

    assert len(windows) == 3


# --- Propiedades -------------------------------------------------------------------------------------

_schedules = st.builds(
    lambda n, lead, duration, rest: schedule(n, lead_in=lead, duration=duration, rest=rest),
    st.integers(1, 5),
    st.floats(1.0, 3.0),
    st.floats(2.0, 6.0),
    st.floats(1.5, 5.0),
)


@pytest.mark.property
@settings(max_examples=40)
@given(_schedules, st.sampled_from(["gyro_x", "gyro_y", "gyro_z"]), st.sampled_from([1.0, -1.0]), st.floats(10.0, 60.0), st.integers(0, 2**16))
def test_every_simulated_rep_is_recovered_once_and_in_place(reps: list[tuple[float, float]], axis: str, sign: float, rom: float, seed: int):
    n = int((reps[-1][1] + 2.0) * RATE)
    axes = {name: noise(n, 0.2, seed + k) for k, name in enumerate(("gyro_x", "gyro_y", "gyro_z"))}
    axes[axis] = [a + b for a, b in zip(axes[axis], rep_signal(reps, n, sign=sign, rom_deg=rom), strict=True)]

    windows = detect_imu_windows(make_context(imu_chunks(axes)))

    assert [w.index for w in windows] == list(range(1, len(reps) + 1))
    for window, (start, end) in zip(windows, reps, strict=True):
        assert abs((window.start - at(start)).total_seconds()) < EDGE_TOLERANCE_S
        assert abs((window.end - at(end)).total_seconds()) < EDGE_TOLERANCE_S
    for current, following in zip(windows, windows[1:], strict=False):
        assert current.end < following.start
        assert current.rest == (current.end, following.start)


@pytest.mark.property
@settings(max_examples=25)
@given(_schedules, st.integers(0, 10**9), st.integers(-3600, 3600))
def test_shifting_the_device_clock_and_its_anchor_shifts_nothing(reps: list[tuple[float, float]], device_offset_ms: int, utc_offset_s: int):
    n = int((reps[-1][1] + 2.0) * RATE)
    signal = {"gyro_y": rep_signal(reps, n)}
    utc = T0 + timedelta(seconds=utc_offset_s)
    base = detect_imu_windows(make_context(imu_chunks(signal)))
    shifted = detect_imu_windows(make_context(imu_chunks(signal, t0_device_ms=device_offset_ms), syncs=[sync(device_ms=device_offset_ms, utc=utc)]))

    delta = timedelta(seconds=utc_offset_s)
    assert [(w.start - delta, w.end - delta) for w in shifted] == [w.active for w in base]


# --- Validación contra el simulador -----------------------------------------------------------------

# vía el paquete instalado, no vía parents[]: bajo mutmut los tests se copian a otro árbol sin physiology/
_MISSION = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
# Tolerancias frente a la verdad del simulador: nº de reps exacto; duración media dentro del 10 %
# (el umbral de borde recorta algo los extremos, de velocidad casi nula); cada inicio a < 0,3 s.
DURATION_REL_TOLERANCE = 0.10
START_TOLERANCE_S = 0.3
# Con 60 repeticiones el ruido deja alguna con el borde corrido (0,33 s observado, semilla 20261113): casi todas
# entran en START_TOLERANCE_S y ninguna pasa de este máximo.
START_OUTLIER_MAX_S = 0.5
START_WITHIN_FRACTION = 0.95


def _mission_config(noise_level: str) -> Any:
    import yaml
    from spine_physiology.config import MissionConfig

    raw = yaml.safe_load(_MISSION.read_text(encoding="utf-8"))
    raw["start_mission_day"] = 1
    raw["segments"] = [{"days": 1, "phase": "inflight", "gravity_environment": "microgravity"}]
    raw["device"].update(power_on_time_utc="09:55", power_off_time_utc="10:40")
    raw["protocols"]["lat_pulldown_standing_v1"].update(streams=["imu_t9"], lead_out_s=10.0)
    raw["schedule"]["sessions"] = [{**s, "start_jitter_s": 60.0} for s in raw["schedule"]["sessions"] if s["name"] == "lat_pulldown"]
    raw["schedule"]["background"].update(enabled=False, start_time_utc="09:56", end_time_utc="09:57")
    imu = raw["signals"]["imu"]
    if noise_level == "clean":
        imu.update(gyro_noise_dps=0.0, tremor_dps_per_unit=0.0)
        for fault in raw["faults"]["quality"].values():
            fault["probability"] = 0.0
        raw["faults"]["device"]["packet_loss"]["probability"] = 0.0
        raw["faults"]["device"]["reboot"]["probability_per_hour"] = 0.0
    elif noise_level == "noisy":
        imu.update(gyro_noise_dps=2.0, tremor_dps_per_unit=10.0)
    return MissionConfig.model_validate(raw)


def _simulated(noise_level: str, seed: int, gravity: str) -> tuple[ExtractionContext, list[tuple[datetime, datetime]], dict[str, float]]:
    from spine_contracts.models.common_schema import GravityEnvironment
    from spine_physiology import MissionGenerator
    from spine_physiology.envelope import EnvelopeKind
    from spine_physiology.sessions import SessionRef

    config = _mission_config(noise_level)
    generator = MissionGenerator(config, seed)
    ref = SessionRef(config.crew[0].astronaut_id, 0, "lat_pulldown")
    messages = list(generator.session_messages(ref, gravity_environment=GravityEnvironment(gravity)))
    syncs = [m.payload for m in generator.day_messages(0, ref.astronaut_id) if m.kind is EnvelopeKind.CLOCK_SYNC]
    session = next(m.payload for m in messages if m.kind is EnvelopeKind.SESSION)
    request = FeaturesRequest.model_validate(
        {
            "schema_version": "1.0.0",
            "session": {**session, "events": []},  # sin eventos: fuerza el respaldo IMU
            "chunks": [m.payload for m in messages if m.kind is EnvelopeKind.STREAM_CHUNK],
            "clock_syncs": syncs,
        }
    )
    plan = generator.session_plan(ref)
    truth_reps = [(plan.start + timedelta(seconds=r.start_s), plan.start + timedelta(seconds=r.end_s)) for r in plan.reps]
    expected = next(m.payload for m in messages if m.kind is EnvelopeKind.GROUND_TRUTH and m.payload["type"] == "expected_features")
    features = {f["feature_id"]: f["value"] for f in expected["features"]}
    return build_context(request), truth_reps, features


@pytest.mark.integration
@pytest.mark.parametrize("gravity", ["earth_1g", "microgravity"])
@pytest.mark.parametrize("noise_level", ["clean", "default", "noisy"])
@pytest.mark.parametrize("seed", [7, 20261113])
def test_the_detector_recovers_the_simulated_repetitions(noise_level: str, seed: int, gravity: str):
    context, truth_reps, features = _simulated(noise_level, seed, gravity)

    windows = detect_imu_windows(context)

    assert len(windows) == features["exercise.rep_count"] == len(truth_reps)
    mean_duration = statistics.fmean(w.duration_s for w in windows)
    assert mean_duration == pytest.approx(features["exercise.rep_duration_s_mean"], rel=DURATION_REL_TOLERANCE)
    errors = [abs((window.start - start).total_seconds()) for window, (start, _end) in zip(windows, truth_reps, strict=True)]
    assert max(errors) < START_OUTLIER_MAX_S
    assert sum(e < START_TOLERANCE_S for e in errors) >= START_WITHIN_FRACTION * len(errors)
