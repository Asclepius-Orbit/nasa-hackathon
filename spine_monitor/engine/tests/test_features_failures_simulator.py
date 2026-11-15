"""End-to-end failure cases (#49): deliberately degraded simulator sessions and what the extractor does.

Each case starts from a normal simulator session (all streams, ``CompositeFeatureExtractor(default_registry())``) and
removes or changes one thing; what comes out must be an omission with the right reason (``skipped``) or, when the engine
documents something else, that something else, and never an invented feature. Already covered against the simulator output in other
files (not repeated here):

- no MVC calibration (``no_calibration``) and expired calibration in EMG: ``test_features_emg_simulator.py``;
- EMG as envelope (``signal_not_raw``) in the spectral ones: ``test_features_emg_spectral_simulator.py`` (amplitude does not
  require raw signal: ``test_features_emg_simulator.py`` checks it gives the same);
- background IMU at 10 Hz without ``insufficient_sample_rate``: ``test_features_imu_simulator.py`` (here the 10 Hz exercise
  session is added against the truth).

What the engine really does, pinned here as is:

- the extractor never emits ``insufficient_sample_rate``: with the IMU at 10 Hz it computes the features, somewhat worse;
- with ``seq`` gaps (``lost_chunks``) the features are computed and quality says so (``packet_gap`` and score =
  coverage); they are not omitted for it.

The session helpers are loaded by path from ``test_features_stretch_simulator.py``: with ``--import-mode=importlib``
tests do not import each other by name.
"""

import importlib.util
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from spine_contracts import FeaturesRequest
from spine_engine.features.ordering import IncidentKind, order_chunks
from spine_engine.measurements import UNITS

_SPEC = importlib.util.spec_from_file_location("_stretch_simulator", Path(__file__).with_name("test_features_stretch_simulator.py"))
assert _SPEC is not None and _SPEC.loader is not None
_SIM: Any = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SIM)

Scenario = _SIM.Scenario
ALL_STREAMS: tuple[str, ...] = _SIM.ALL_STREAMS
SEEDS: list[int] = _SIM.SEEDS
GRAVITIES: list[str] = _SIM.GRAVITIES
EXERCISE: str = _SIM.EXERCISE
STRETCH: str = _SIM.STRETCH
PER_REP_REL: float = _SIM.PER_REP_REL
SYMMETRY = "emg.latissimus_dorsi.symmetry_index_pct"
IMU_ROM = "imu.t9.sagittal.rom_deg"
IMU_PEAK = "imu.t9.sagittal.peak_angular_velocity_dps"
STRETCH_PEAK = f"{_SIM.PREFIX}.peak_elongation_mm"

# IMU at 10 Hz (exercise session): the ROM comes from integrating the velocity and barely loses; observed +0.3 % (seed 7) and
# +0.1 % (20261113) over the truth; threshold 2 %. The gyroscope peak is underestimated when sampling 10x less:
# observado -1,7 % y -1,5 %; umbral 5 %.
IMU_10HZ_ROM_REL = 0.02
IMU_10HZ_PEAK_REL = 0.05
IMU_10HZ = 10.0
# Chunk loss: high probability so there are plenty of gaps (15 % of IMU and cord chunks in both
# seeds) versus 0.2 % in the example mission.
PACKET_LOSS = 0.05
# With gaps the quality score is coverage = chunks present / chunks expected between the first and last
# received (constant chunk size): it matches the chunk count up to rounding (observed 0 in the 4 combinations).
COVERAGE_ABS = 1e-6
# With 15 % gaps the IMU ROM drops (-5.3 % and -6.2 % observed) because it is integrated across gaps; threshold 15 %.
IMU_ROM_LOSS_REL = 0.15
GAP_STREAMS = ("imu_t9", STRETCH)


# The cases that only look at what is omitted and why do not depend on the gravity environment or the seed (the omission comes from
# the calibrations and the streams, not the signal) and are compared with the full session of the same configuration: a
# single seed and gravity, and 1 s chunks, because extracting a full session takes ~10 s. The cases that compare with
# the numeric truth (IMU at 10 Hz, seq gaps) keep both seeds, Earth gravity (the signal model
# does not change with it) and the mission's 100 ms.
STRUCTURAL_SEEDS = [7]
STRUCTURAL_GRAVITY = "earth_1g"
STRUCTURAL_CHUNK_MS = 1000
TRUTH_GRAVITY = "earth_1g"


def _simulate(*, seed: int, gravity: str, **scenario: Any) -> Any:
    streams = scenario.pop("streams", ALL_STREAMS)
    return _SIM._simulate(Scenario(streams=streams, gravity=gravity, **scenario), seed, EXERCISE)


@cache
def _full_features(seed: int) -> dict[str, dict[str, Any]]:
    """Features of the full, undegraded session (the reference the structural cases are compared with)."""
    return _SIM._by_id(_extract(_simulate(seed=seed, gravity=STRUCTURAL_GRAVITY, chunk_ms=STRUCTURAL_CHUNK_MS).payload))


def _extract(payload: dict[str, Any]) -> dict[str, Any]:
    return _SIM._extract(payload)


def _legacy_skipped(result: dict[str, Any]) -> dict[str, str]:
    return {key: value for key, value in _SIM._skipped(result).items() if key not in UNITS}


def _chunks_of(payload: dict[str, Any], stream_id: str) -> list[dict[str, Any]]:
    return [c for c in payload["chunks"] if c["stream_id"] == stream_id]


def _quality_of(features: dict[str, dict[str, Any]], feature_id: str) -> dict[str, Any]:
    return features[feature_id]["quality"]


# (calibration removed, ids omitted as no_calibration, ids of the family that are still computed)
_NO_CALIBRATION = {
    "stretch": (
        "stretch",
        {f"{_SIM.PREFIX}.{n}" for n in ("elongation_mm", "elongation_pct", "peak_elongation_mm", "range_mm", "recovery_time_s")},
        {f"{_SIM.PREFIX}.rest_resistance_ohm"},
    ),
    "imu": (
        "imu",
        {f"imu.t9.{p}.{n}" for p in ("sagittal", "frontal", "transverse") for n in ("rom_deg", "peak_angular_velocity_dps")} | {"imu.t9.accel_rms_g"},
        set(),
    ),
}


@pytest.mark.integration
@pytest.mark.parametrize("seed", STRUCTURAL_SEEDS)
@pytest.mark.parametrize("family", sorted(_NO_CALIBRATION))
def test_without_the_calibration_of_a_family_its_calibrated_features_are_skipped_and_nothing_is_invented(family: str, seed: int):
    calibration_type, skipped_ids, kept_ids = _NO_CALIBRATION[family]
    sim = _simulate(seed=seed, gravity=STRUCTURAL_GRAVITY, chunk_ms=STRUCTURAL_CHUNK_MS)
    full = _full_features(seed)
    assert calibration_type in {c["calibration_type"] for c in sim.payload["calibrations"]}
    payload = {**sim.payload, "calibrations": [c for c in sim.payload["calibrations"] if c["calibration_type"] != calibration_type]}

    result = _extract(payload)

    features = _SIM._by_id(result)
    assert _legacy_skipped(result) == dict.fromkeys(skipped_ids, "no_calibration")
    assert not skipped_ids & set(features)
    assert kept_ids <= set(features)
    # what does not depend on that calibration comes out identical to the full session
    unaffected = set(full) - skipped_ids
    assert set(features) == unaffected
    assert {i: features[i]["value"] for i in unaffected} == {i: full[i]["value"] for i in unaffected}


# (missing stream, ids that must be omitted as stream_missing)
_MISSING_STREAM: dict[str, Callable[[str], bool]] = {
    "emg_lat_left": lambda i: i.startswith("emg.") and (".left." in i or i == SYMMETRY),
    "emg_lat_right": lambda i: i.startswith("emg.") and (".right." in i or i == SYMMETRY),
    "imu_t9": lambda i: i.startswith("imu."),
    STRETCH: lambda i: i.startswith("stretch."),
}


@pytest.mark.integration
@pytest.mark.parametrize("seed", STRUCTURAL_SEEDS)
@pytest.mark.parametrize("stream_id", sorted(_MISSING_STREAM))
def test_an_absent_stream_skips_exactly_its_features_as_stream_missing_and_leaves_the_rest_untouched(stream_id: str, seed: int):
    sim = _simulate(seed=seed, gravity=STRUCTURAL_GRAVITY, chunk_ms=STRUCTURAL_CHUNK_MS)
    full = _full_features(seed)
    affected = {i for i in full if _MISSING_STREAM[stream_id](i)}
    assert affected  # the full session did compute them
    payload = {**sim.payload, "chunks": [c for c in sim.payload["chunks"] if c["stream_id"] != stream_id]}

    result = _extract(payload)

    features = _SIM._by_id(result)
    assert {i for i, r in _legacy_skipped(result).items() if r == "stream_missing"} == affected
    assert set(_legacy_skipped(result)) == affected
    assert not affected & set(features)
    assert set(features) == set(full) - affected
    # exercise comes from the session events (not from any stream) and the other streams are unaffected
    for feature_id in set(features):
        if feature_id.startswith(("exercise.", "stretch.", "imu.")) and not _MISSING_STREAM[stream_id](feature_id):
            assert features[feature_id]["value"] == full[feature_id]["value"]


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
def test_the_imu_at_10_hz_is_never_skipped_for_its_sample_rate_and_stays_close_to_the_truth(seed: int):
    gravity = TRUTH_GRAVITY
    # In practice the extractor has no insufficient_sample_rate reason: at 10 Hz it computes, and well.
    sim = _simulate(seed=seed, gravity=gravity, streams=("imu_t9",), imu_rate_hz=IMU_10HZ)
    assert {c["sample_rate_hz"] for c in _chunks_of(sim.payload, "imu_t9")} == {IMU_10HZ}

    result = _extract(sim.payload)

    features = _SIM._by_id(result)
    assert "insufficient_sample_rate" not in set(_SIM._skipped(result).values())
    assert not [i for i in _legacy_skipped(result) if i.startswith("imu.")]
    assert features[IMU_ROM]["value"] == pytest.approx(sim.truth[IMU_ROM], rel=IMU_10HZ_ROM_REL)
    assert features[IMU_PEAK]["value"] == pytest.approx(sim.truth[IMU_PEAK], rel=IMU_10HZ_PEAK_REL)
    assert features[IMU_ROM]["n"] == features[IMU_PEAK]["n"] == sim.truth["exercise.rep_count"]


def _lost_by_stream(sim: Any) -> dict[str, list[tuple[int, int]]]:
    streams = sim.records["lost_chunks"]["streams"]
    return {stream_id: [(x["boot_id"], x["seq"]) for x in lost] for stream_id, lost in streams.items()}


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
def test_the_lost_chunks_of_the_truth_are_the_seq_gaps_the_extractor_sees(seed: int):
    gravity = TRUTH_GRAVITY
    sim = _simulate(seed=seed, gravity=gravity, streams=GAP_STREAMS, packet_loss=PACKET_LOSS)
    lost = _lost_by_stream(sim)
    chunks = FeaturesRequest.model_validate(sim.payload).chunks

    ordered = order_chunks(chunks)

    assert lost["imu_t9"] and lost[STRETCH]
    for stream_id, truth_lost in lost.items():
        received = {(c["boot_id"], c["seq"]) for c in _chunks_of(sim.payload, stream_id)}
        assert not received & set(truth_lost)  # lo perdido no llega
        seqs = sorted(seq for _, seq in received)
        interior = {seq for _, seq in truth_lost if seqs[0] < seq < seqs[-1]}  # a gap before the first received one does not count
        gaps = [i for i in ordered.incidents if i.kind is IncidentKind.SEQ_GAP and i.stream_id == stream_id]
        assert sum(i.chunks for i in gaps) == len(interior)
        assert {seq for i in gaps for seq in range(i.seq, i.seq + i.chunks)} == interior


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
def test_with_seq_gaps_the_features_are_computed_and_flagged_with_the_coverage_as_score(seed: int):
    gravity = TRUTH_GRAVITY
    clean = _SIM._by_id(_extract(_simulate(seed=seed, gravity=gravity, streams=GAP_STREAMS, packet_loss=0.0).payload))
    sim = _simulate(seed=seed, gravity=gravity, streams=GAP_STREAMS, packet_loss=PACKET_LOSS)

    result = _extract(sim.payload)

    features = _SIM._by_id(result)
    assert _legacy_skipped(result) == {}
    assert set(features) == set(clean)  # nothing is omitted or invented because of the gaps
    assert not [i for i in clean if "packet_gap" in _quality_of(clean, i)["flags"]]
    coverage: dict[str, float] = {}
    for stream_id in ("imu_t9", STRETCH):
        seqs = sorted(c["seq"] for c in _chunks_of(sim.payload, stream_id))
        coverage[stream_id] = len(seqs) / (seqs[-1] - seqs[0] + 1)
        assert coverage[stream_id] < 1.0
    # what uses a single stream carries that stream's coverage
    assert _quality_of(features, STRETCH_PEAK) == {
        "score": pytest.approx(coverage[STRETCH], abs=COVERAGE_ABS),
        "flags": ["packet_gap"],
        "assessed_by": "engine",
    }
    assert _quality_of(features, IMU_ROM) == {"score": pytest.approx(coverage["imu_t9"], abs=COVERAGE_ABS), "flags": ["packet_gap"], "assessed_by": "engine"}
    # repetitions are still counted from the session events
    assert features["exercise.rep_count"]["value"] == sim.truth["exercise.rep_count"]
    assert features[IMU_ROM]["value"] == pytest.approx(sim.truth[IMU_ROM], rel=IMU_ROM_LOSS_REL)
    assert features[STRETCH_PEAK]["value"] == pytest.approx(sim.truth[STRETCH_PEAK], rel=PER_REP_REL)
