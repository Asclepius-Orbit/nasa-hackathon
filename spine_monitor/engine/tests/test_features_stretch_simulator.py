"""End-to-end ``stretch`` family (#45): simulator sessions through ``CompositeFeatureExtractor(default_registry())``.

The truth is the simulator's ``expected_features``: ``stretch.{region}.peak_elongation_mm`` and ``range_mm`` (real length of the
cord during the repetitions, without noise or hysteresis) and ``elongation_mm`` / ``elongation_pct`` / ``rest_resistance_ohm``
(mean of the real length in a ``spine_check`` or in the background). The extraction starts from the noisy ADC counts and the
measured calibration (curve with ``measurement_noise_mm``), so it is compared with tolerance.

The truth does NOT carry ``recovery_time_s`` (the simulator does not model recovery as a feature): ``recovery_time_s`` is not
compared with any truth and it is only checked that it is finite and non-negative when it comes out.

This file also defines the full session helpers (all streams, faults, chunk loss...) that
``test_features_failures_simulator.py`` and ``test_baseline_simulator.py`` load by path: with ``--import-mode=importlib``
tests do not import each other by name.
"""

import copy
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import spine_physiology
from spine_contracts import FeaturesRequest, dump, validation
from spine_contracts.catalog import unit_for
from spine_engine.features.composite import CompositeFeatureExtractor
from spine_engine.features.registry import default_registry

# via the installed package, not via parents[]: under mutmut the tests are copied to another tree without physiology/
_MISSION = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
REGION = "full_spine"
PREFIX = f"stretch.{REGION}"
STRETCH = "stretch_spine"
ALL_STREAMS = ("emg_lat_left", "emg_lat_right", "imu_t9", STRETCH)
SEEDS = [7, 20261113]
GRAVITIES = ["earth_1g", "microgravity"]
EXERCISE, SPINE_CHECK, BACKGROUND = "lat_pulldown", "pre_spine_check", "background"


def _rest_length_mm() -> float:
    import yaml

    raw = yaml.safe_load(_MISSION.read_text(encoding="utf-8"))
    return next(a for a in raw["crew"] if a["astronaut_id"] == "AST-01")["baseline"]["cord_rest_length_mm"]


REST_LENGTH_MM = _rest_length_mm()  # baseline.cord_rest_length_mm of astronaut AST-01 in example-mission.yaml

# Tolerances against the truth; observed across the 4 seed x gravity combinations.
# Peak and range per repetition: the truth is the real length without noise or hysteresis, the extraction sees the ADC noise (the
# maximum of ~60 noisy samples biases the peak upwards and the range at both ends) and the hysteresis (only the peak):
# observed +21..22 % the peak and +18..22 % the range with the 12-bit ADC; the real firmware one is 10 bits (counts
# 1..1022, ~2.6 mm per count with 1 count of noise), so the maximum and minimum of ~60 samples deviate more:
# observed up to +61 % the range (adds ~8 mm of noise extremes to some 15 real mm), always above the
# truth; threshold 70 %.
PER_REP_REL = 0.70
# Passive elongation of a spine_check: only ADC and measured-curve noise over ~2400 samples: observed up to 0.03 mm;
# threshold 0.1 mm with the 12-bit ADC. With the firmware 10-bit one (~2.6 mm per count) quantization leaves a bias
# of ~0.15 counts in the mean: observed up to 0.45 mm (and -0.2 mm without noise, the truth is continuous); threshold 0.6 mm.
# In the background the extraction detects flexions as repetitions and excludes each one plus the recovery
# wait from the passive samples (the truth averages everything emitted): with seed 20261113, 305 of 1010
# samples remain and the error rises to 0.22 mm; threshold 0.5 mm. The % are equivalent to those mm over the rest length.
ELONGATION_MM_ABS = {SPINE_CHECK: 0.6, BACKGROUND: 0.5}
ELONGATION_PCT_ABS = {slot: 100.0 * mm / REST_LENGTH_MM for slot, mm in ELONGATION_MM_ABS.items()}
# Rest resistance: the ADC is inverted with the exact series resistance: observed < 0.04 % even in the background; threshold 0.1 %.
REST_RESISTANCE_REL = 1e-3
# n of the passive ones: the truth counts the emitted samples and the extraction discards saturated or zero ADC, lost
# chunks and (background) flexions; it can never exceed the truth. Minimum fraction observed: spine_check 99.7 %, background 30 %.
PASSIVE_N_MIN_FRACTION = {SPINE_CHECK: 0.95, BACKGROUND: 0.25}


@dataclass(frozen=True, slots=True)
class Scenario:
    """Variations on the example mission; hashable so the simulation can be cached."""

    streams: tuple[str, ...] = (STRETCH,)
    faults: bool = False
    # probability of losing a chunk: None keeps the example mission's
    packet_loss: float | None = None
    imu_rate_hz: float | None = None
    # None keeps the mission's 100 ms; longer chunks make extraction much cheaper (it walks the chunks per repetition)
    chunk_ms: int | None = None
    days: int = 1
    phase: str = "inflight"
    gravity: str = "microgravity"


class Simulated(NamedTuple):
    """Features request of a simulated session and its truth."""

    payload: dict[str, Any]
    truth: dict[str, float]
    truth_n: dict[str, int | None]
    records: dict[str, dict[str, Any]]


def _mission_config(scenario: Scenario) -> Any:
    import yaml
    from spine_physiology.config import MissionConfig

    raw = yaml.safe_load(_MISSION.read_text(encoding="utf-8"))
    raw["start_mission_day"] = 1
    raw["segments"] = [{"days": scenario.days, "phase": scenario.phase, "gravity_environment": scenario.gravity}]
    raw["device"].update(power_on_time_utc="09:55", power_off_time_utc="10:40")
    raw["protocols"]["lat_pulldown_standing_v1"].update(streams=list(scenario.streams), lead_out_s=10.0)
    # The dispersion between repeated readings is a per-session resistance offset that the truth cannot know (and that
    # the calibration curve does not correct): it is switched off to compare with the truth; the rest of the realism stays on.
    raw["signals"]["stretch"]["repeat_dispersion_ohm"] = 0.0
    if not scenario.faults:
        # injected artifacts (clipping, saturation...) alter the signal without touching the truth: they are compared without them
        for fault in raw["faults"]["quality"].values():
            fault["probability"] = 0.0
    if scenario.packet_loss is not None:
        raw["faults"]["device"]["packet_loss"]["probability"] = scenario.packet_loss
    if scenario.imu_rate_hz is not None:
        raw["signals"]["imu"]["sample_rate_hz"] = scenario.imu_rate_hz
    if scenario.chunk_ms is not None:
        raw["signals"]["chunk_ms"] = scenario.chunk_ms
    # sessions with `bracket` (spine_check) do not accept their own jitter
    raw["schedule"]["sessions"] = [s if "bracket" in s else {**s, "start_jitter_s": 60.0} for s in raw["schedule"]["sessions"]]
    raw["schedule"]["background"].update(start_time_utc="09:56", end_time_utc="09:58")
    return MissionConfig.model_validate(raw)


@cache
def _simulated(scenario: Scenario, seed: int, slot: str, day: int) -> Simulated:
    from spine_contracts.models.common_schema import GravityEnvironment
    from spine_physiology import MissionGenerator
    from spine_physiology.envelope import EnvelopeKind
    from spine_physiology.sessions import SessionRef

    config = _mission_config(scenario)
    generator = MissionGenerator(config, seed)
    ref = SessionRef(config.crew[0].astronaut_id, day, slot)
    messages = list(generator.session_messages(ref, gravity_environment=GravityEnvironment(scenario.gravity)))
    # the session helper does not bring the day's calibrations or the clock_sync: the day's generator emits them
    day_messages = list(generator.day_messages(day, ref.astronaut_id))
    payload = {
        "schema_version": "1.0.0",
        "session": next(m.payload for m in messages if m.kind is EnvelopeKind.SESSION),
        "chunks": [m.payload for m in messages if m.kind is EnvelopeKind.STREAM_CHUNK],
        "clock_syncs": [m.payload for m in day_messages if m.kind is EnvelopeKind.CLOCK_SYNC],
        "calibrations": [m.payload for m in [*messages, *day_messages] if m.kind is EnvelopeKind.CALIBRATION],
    }
    records = {m.payload["type"]: m.payload for m in messages if m.kind is EnvelopeKind.GROUND_TRUTH}
    features = records["expected_features"]["features"]
    return Simulated(payload, {f["feature_id"]: f["value"] for f in features}, {f["feature_id"]: f.get("n") for f in features}, records)


def _simulate(scenario: Scenario, seed: int, slot: str, day: int = 0) -> Simulated:
    """Own copy (the tests modify it) of :func:`_simulated`."""
    return copy.deepcopy(_simulated(scenario, seed, slot, day))


def _extract(payload: dict[str, Any]) -> dict[str, Any]:
    result = dump(CompositeFeatureExtractor(default_registry()).extract(FeaturesRequest.model_validate(payload)))
    assert validation.errors("domain/feature-set", result) == []
    return result


def _by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["feature_id"]: f for f in result["features"]}


def _skipped(result: dict[str, Any]) -> dict[str, str]:
    return {s["feature_id"]: s["reason"] for s in result.get("skipped", [])}


def _stretch(name: str) -> str:
    return f"{PREFIX}.{name}"


PER_REP = ("peak_elongation_mm", "range_mm")
PASSIVE = ("elongation_mm", "elongation_pct", "rest_resistance_ohm")


@pytest.mark.integration
@pytest.mark.parametrize("gravity", GRAVITIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_the_per_repetition_features_match_the_simulator_truth(seed: int, gravity: str):
    sim = _simulate(Scenario(gravity=gravity), seed, EXERCISE)

    result = _extract(sim.payload)

    features = _by_id(result)
    assert {f for f in features if f.startswith("stretch.")} >= {_stretch(n) for n in PER_REP}
    assert not [i for i in _skipped(result) if i.endswith(PER_REP)]
    for name in PER_REP:
        feature = features[_stretch(name)]
        assert feature["value"] == pytest.approx(sim.truth[_stretch(name)], rel=PER_REP_REL)
        assert feature["n"] == sim.truth_n[_stretch(name)] == sim.truth["exercise.rep_count"]
        assert feature["unit"] == unit_for(_stretch(name))
        assert feature["quality"]["assessed_by"] == "engine"
    assert features[_stretch("peak_elongation_mm")]["value"] > features[_stretch("range_mm")]["value"] > 0.0


@pytest.mark.integration
@pytest.mark.parametrize("gravity", GRAVITIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_the_recovery_time_has_no_truth_and_is_only_checked_for_being_plausible(seed: int, gravity: str):
    # The simulator does not emit recovery_time_s in expected_features: there is no value to compare it with.
    sim = _simulate(Scenario(gravity=gravity), seed, EXERCISE)
    assert _stretch("recovery_time_s") not in sim.truth

    features = _by_id(_extract(sim.payload))

    recovery = features.get(_stretch("recovery_time_s"))
    if recovery is not None:
        assert math.isfinite(recovery["value"]) and recovery["value"] >= 0.0
        assert 0 < recovery["n"] <= sim.truth["exercise.rep_count"]


@pytest.mark.integration
@pytest.mark.parametrize("slot", [SPINE_CHECK, BACKGROUND])
@pytest.mark.parametrize("gravity", GRAVITIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_the_passive_features_match_the_simulator_truth(seed: int, gravity: str, slot: str):
    sim = _simulate(Scenario(gravity=gravity), seed, slot)

    result = _extract(sim.payload)

    features = _by_id(result)
    assert {_stretch(n) for n in PASSIVE} <= set(features)
    # without repetitions there is no truth or per-repetition value: they are omitted with a reason and not invented
    assert {i: r for i, r in _skipped(result).items() if i.endswith(PER_REP)} == dict.fromkeys((_stretch(n) for n in PER_REP), "too_few_repetitions")
    assert features[_stretch("elongation_mm")]["value"] == pytest.approx(sim.truth[_stretch("elongation_mm")], abs=ELONGATION_MM_ABS[slot])
    assert features[_stretch("elongation_pct")]["value"] == pytest.approx(sim.truth[_stretch("elongation_pct")], abs=ELONGATION_PCT_ABS[slot])
    assert features[_stretch("rest_resistance_ohm")]["value"] == pytest.approx(sim.truth[_stretch("rest_resistance_ohm")], rel=REST_RESISTANCE_REL)
    for name in PASSIVE:
        feature, expected_n = features[_stretch(name)], sim.truth_n[_stretch(name)]
        assert expected_n is not None
        assert PASSIVE_N_MIN_FRACTION[slot] * expected_n <= feature["n"] <= expected_n
        assert feature["unit"] == unit_for(_stretch(name))
        assert feature["quality"]["assessed_by"] == "engine"


@pytest.mark.integration
def test_the_exercise_session_publishes_no_passive_truth_and_the_spine_check_no_per_repetition_truth():
    # the truth of each session type is disjoint: this way each test compares with what corresponds
    exercise = _simulate(Scenario(), 7, EXERCISE).truth
    check = _simulate(Scenario(), 7, SPINE_CHECK).truth

    assert {i for i in exercise if i.startswith("stretch.")} == {_stretch(n) for n in PER_REP}
    assert {i for i in check if i.startswith("stretch.")} == {_stretch(n) for n in PASSIVE}
