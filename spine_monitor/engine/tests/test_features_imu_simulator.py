"""End-to-end ``imu`` family (#44): simulator sessions through ``CompositeFeatureExtractor(default_registry())``.

The truth is the simulator mean (``expected_features``) for the ROM and the speed of each repetition,
only in the protocol plane (sagittal). There is no truth
for ``accel_rms_g``: it is only checked that it comes out and its sign. No simulated quality faults.
"""

from pathlib import Path
from typing import Any

import pytest
import spine_physiology
from spine_contracts import FeaturesRequest, dump, validation
from spine_contracts.catalog import unit_for
from spine_engine.features.composite import CompositeFeatureExtractor
from spine_engine.features.registry import default_registry
from spine_engine.measurements import UNITS

# via the installed package, not via parents[]: under mutmut the tests are copied to another tree without physiology/
_MISSION = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
ROM = "imu.t9.sagittal.rom_deg"
PEAK = "imu.t9.sagittal.peak_angular_velocity_dps"
ACCEL = "imu.t9.accel_rms_g"
PLANES = ("sagittal", "frontal", "transverse")
ALL_IDS = (*(f"imu.t9.{p}.{n}" for p in PLANES for n in ("rom_deg", "peak_angular_velocity_dps")), ACCEL)

# The residual calibration bias is integrated during each repetition; the longer ones
# due to accumulated fatigue gather more error than the old fixed-duration schedule.
ROM_REL_TOLERANCE = 0.02
# Peak: gyroscope noise and tremor add to the maximum of |ω|. Observed error 0.6 % and 1.2 %; threshold > 2x.
PEAK_REL_TOLERANCE = 0.03

SEEDS = [7, 20261113]
GRAVITIES = ["earth_1g", "microgravity"]


def _mission_config(*, background: bool) -> Any:
    import yaml
    from spine_physiology.config import MissionConfig

    raw = yaml.safe_load(_MISSION.read_text(encoding="utf-8"))
    raw["start_mission_day"] = 1
    raw["segments"] = [{"days": 1, "phase": "inflight", "gravity_environment": "microgravity"}]
    raw["device"].update(power_on_time_utc="09:55", power_off_time_utc="10:40")
    raw["protocols"]["lat_pulldown_standing_v1"].update(streams=["imu_t9"], lead_out_s=10.0)
    raw["schedule"]["sessions"] = [{**s, "start_jitter_s": 60.0} for s in raw["schedule"]["sessions"] if s["name"] == "lat_pulldown"]
    # the truth is the model without quality faults (artifacts, low SNR...): those are reflected by the common quality
    for fault in raw["faults"]["quality"].values():
        fault["probability"] = 0.0
    raw["schedule"]["background"].update(enabled=background, start_time_utc="09:56", end_time_utc="09:58")
    return MissionConfig.model_validate(raw)


def _simulate(seed: int, gravity: str, *, slot: str = "lat_pulldown") -> tuple[FeaturesRequest, dict[str, float]]:
    """Features request of a simulated session (with its calibrations) and the true features."""
    from spine_contracts.models.common_schema import GravityEnvironment
    from spine_physiology import MissionGenerator
    from spine_physiology.envelope import EnvelopeKind
    from spine_physiology.sessions import SessionRef

    config = _mission_config(background=slot != "lat_pulldown")
    generator = MissionGenerator(config, seed)
    ref = SessionRef(config.crew[0].astronaut_id, 0, slot)
    messages = list(generator.session_messages(ref, gravity_environment=GravityEnvironment(gravity)))
    syncs = [m.payload for m in generator.day_messages(0, ref.astronaut_id) if m.kind is EnvelopeKind.CLOCK_SYNC]
    request = FeaturesRequest.model_validate(
        {
            "schema_version": "1.0.0",
            "session": next(m.payload for m in messages if m.kind is EnvelopeKind.SESSION),
            "chunks": [m.payload for m in messages if m.kind is EnvelopeKind.STREAM_CHUNK],
            # the #41 helper does not collect them: without them everything would come out no_calibration
            "calibrations": [m.payload for m in messages if m.kind is EnvelopeKind.CALIBRATION],
            "clock_syncs": syncs,
        }
    )
    expected = next(m.payload for m in messages if m.kind is EnvelopeKind.GROUND_TRUTH and m.payload["type"] == "expected_features")
    return request, {f["feature_id"]: f["value"] for f in expected["features"]}


def _extract(request: FeaturesRequest) -> dict[str, Any]:
    result = dump(CompositeFeatureExtractor(default_registry()).extract(request))
    assert validation.errors("domain/feature-set", result) == []
    return result


@pytest.mark.integration
@pytest.mark.parametrize("gravity", GRAVITIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_sagittal_rom_and_peak_angular_velocity_match_the_simulator_truth(seed: int, gravity: str):
    request, truth = _simulate(seed, gravity)

    result = _extract(request)

    features = {f["feature_id"]: f for f in result["features"]}
    assert set(ALL_IDS) <= set(features)
    assert [s for s in result.get("skipped", []) if s["feature_id"].startswith("imu.") and s["feature_id"] not in UNITS] == []
    assert features[ROM]["value"] == pytest.approx(truth[ROM], rel=ROM_REL_TOLERANCE)
    assert features[PEAK]["value"] == pytest.approx(truth[PEAK], rel=PEAK_REL_TOLERANCE)
    assert features[ROM]["n"] == features[PEAK]["n"] == truth["exercise.rep_count"]
    assert not [i for i in features if i.startswith("imu.") and "sparc" in i]
    assert features[ACCEL]["value"] >= 0.0
    for feature_id in ALL_IDS:
        assert features[feature_id]["unit"] == unit_for(feature_id)


@pytest.mark.integration
def test_the_10_hz_background_imu_no_longer_skips_anything_for_its_sample_rate():
    request, _truth = _simulate(7, "microgravity", slot="background")
    assert {c.sample_rate_hz for c in request.chunks if c.stream_kind == "imu"} == {10.0}

    result = _extract(request)

    assert not [s for s in result.get("skipped", []) if s["reason"] == "insufficient_sample_rate"]
    assert not [s for s in result.get("skipped", []) if "sparc" in s["feature_id"]]
