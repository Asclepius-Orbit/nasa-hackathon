"""``emg`` family with the module realism switched on (#131): ADC quantization, 50 Hz mains, baseline drift and ECG leakage.

Same sessions and helpers as ``test_features_emg_simulator`` (loaded by path: with ``--import-mode=importlib`` the tests do not
import each other by name), but with ``signals.emg`` modified. The interference values are **hypotheses** with no
verified figure (``docs/sensor-sources.md``): the purchased module is defective and there is no measured EMG, so this checks that the
engine does not break with those artifacts and how far it deviates from the simulator truth, not that it reproduces the hardware.
"""

import importlib.util
import math
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from spine_contracts import FeaturesRequest, dump, validation
from spine_engine.features.composite import CompositeFeatureExtractor
from spine_engine.features.registry import default_registry
from spine_engine.measurements import UNITS

_SPEC = importlib.util.spec_from_file_location("_emg_amplitude_simulator", Path(__file__).with_name("test_features_emg_simulator.py"))
assert _SPEC is not None and _SPEC.loader is not None
_AMPLITUDE: Any = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_AMPLITUDE)

MUSCLE: str = _AMPLITUDE.MUSCLE
SIDES: dict[str, str] = _AMPLITUDE.SIDES
SEED = 7
# Artefactos aditivos (hip.): red 0,05 mV pico (0,035 mV rms), deriva 0,05 mV rms, fuga de ECG 0,1 mV pico a 70 lpm.
MAINS_MV, WANDER_MV, ECG_MV = 0.05, 0.05, 0.1
# ECG: pulsos gaussianos de sigma 10 ms y pico ECG_MV, uno por latido (periodo 60/70 s): rms = ECG_MV x sqrt(sigma x sqrt(pi) / T).
_ECG_RMS_MV = ECG_MV * math.sqrt(0.01 * math.sqrt(math.pi) * 70.0 / 60.0)
INTERFERENCE_RMS_MV = math.sqrt(MAINS_MV**2 / 2 + WANDER_MV**2 + _ECG_RMS_MV**2)

# Active RMS: the truth does not include saturation. With the ±1.65 mV bipolar ADC and the mission MVC,
# the signal tails are clipped: observed 6.3 % and 5.6 % below the truth (seed 7).
# The 8 % threshold includes that bias, plus the additive interference (~0.07 mV rms).
RMS_MV_REL = 0.08
# Rest RMS: without artifacts it is ~0.04 mV; with them the interference dominates. Theoretical sqrt(truth^2 + interference^2);
# observed 2-3 % deviation from that; threshold 10 % (short rest windows: ~1 s between reps).
REST_REL = 0.10
# %MVC: adds the error of the measured MVC (sigma 5 %) as without artifacts: threshold 12 %.
PCT_MVC_REL = 0.12
# Spectral median: the 50 Hz mains is inside the band and pulls the median (~220 Hz) down by a minimal fraction;
# observed 1.3 %; threshold 4 % (without artifacts it is 3 %).
MEDIAN_REL = 0.04


@cache
def _simulated() -> tuple[dict[str, Any], dict[str, float]]:
    from spine_contracts.models.common_schema import GravityEnvironment
    from spine_physiology import MissionGenerator
    from spine_physiology.envelope import EnvelopeKind
    from spine_physiology.sessions import SessionRef

    base = _AMPLITUDE._mission_config(faults=False)
    emg = base.signals.emg.model_copy(update={"quantize_adc": True, "mains_amplitude_mv": MAINS_MV, "baseline_wander_mv": WANDER_MV, "ecg_leak_mv": ECG_MV})
    config = base.model_copy(update={"signals": base.signals.model_copy(update={"emg": emg})})
    generator = MissionGenerator(config, SEED)
    ref = SessionRef(config.crew[0].astronaut_id, 0, "lat_pulldown")
    messages = list(generator.session_messages(ref, gravity_environment=GravityEnvironment.EARTH_1G))
    day = list(generator.day_messages(0, ref.astronaut_id))
    payload = {
        "schema_version": "1.0.0",
        "session": next(m.payload for m in messages if m.kind is EnvelopeKind.SESSION),
        "chunks": [m.payload for m in messages if m.kind is EnvelopeKind.STREAM_CHUNK],
        "clock_syncs": [m.payload for m in day if m.kind is EnvelopeKind.CLOCK_SYNC],
        "calibrations": [m.payload for m in [*messages, *day] if m.kind is EnvelopeKind.CALIBRATION],
    }
    expected = next(m.payload for m in messages if m.kind is EnvelopeKind.GROUND_TRUTH and m.payload["type"] == "expected_features")
    return payload, {f["feature_id"]: f["value"] for f in expected["features"]}


def _extract(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = dump(CompositeFeatureExtractor(default_registry()).extract(FeaturesRequest.model_validate(payload)))
    assert validation.errors("domain/feature-set", result) == []
    assert not [s for s in result.get("skipped", []) if s["feature_id"] not in UNITS]
    return {f["feature_id"]: f for f in result["features"]}


@pytest.mark.integration
def test_the_amplitude_and_spectral_features_stay_close_to_the_truth_with_quantization_mains_wander_and_ecg_on():
    payload, truth = _simulated()
    assert {c["stream_id"] for c in payload["chunks"]} >= set(SIDES)

    features = _extract(payload)

    for side in SIDES.values():
        prefix = f"emg.{MUSCLE}.{side}"
        assert features[f"{prefix}.rms_mv"]["value"] == pytest.approx(truth[f"{prefix}.rms_mv"], rel=RMS_MV_REL)
        assert features[f"{prefix}.rms_pct_mvc"]["value"] == pytest.approx(truth[f"{prefix}.rms_pct_mvc"], rel=PCT_MVC_REL)
        rest_expected = math.hypot(truth[f"{prefix}.rest_rms_mv"], INTERFERENCE_RMS_MV)
        assert features[f"{prefix}.rest_rms_mv"]["value"] == pytest.approx(rest_expected, rel=REST_REL)
        assert features[f"{prefix}.rest_rms_mv"]["value"] > truth[f"{prefix}.rest_rms_mv"]  # the artifacts only add
        assert features[f"{prefix}.median_freq_hz"]["value"] == pytest.approx(truth[f"{prefix}.median_freq_hz"], rel=MEDIAN_REL)
        assert features[f"{prefix}.median_freq_slope_hz_per_rep"]["value"] < 0.0  # fatigue is still visible
        assert features[f"{prefix}.rms_mv"]["n"] == truth["exercise.rep_count"]


@pytest.mark.integration
def test_the_quantized_session_is_made_of_whole_adc_steps_and_inside_the_adc_range():
    payload, _truth = _simulated()
    acq = next(c["acquisition"] for c in payload["chunks"] if c["stream_kind"] == "emg")
    lsb = acq["vref_v"] * 1000.0 / acq["gain"] / 2 ** acq["adc_bits"]
    values = [v for c in payload["chunks"] if c["stream_kind"] == "emg" for v in c["data"]["emg"]]
    assert values
    assert max(abs(v / lsb - round(v / lsb)) for v in values) < 1e-3  # the JSON rounds; the steps are still whole numbers
    assert min(values) >= -(2 ** (acq["adc_bits"] - 1)) * lsb - 1e-9 and max(values) <= (2 ** (acq["adc_bits"] - 1) - 1) * lsb + 1e-9
    # Comparing against the unclipped truth requires this session to actually reach both ADC limits.
    for stream_id in SIDES:
        stream_values = [v for c in payload["chunks"] if c["stream_id"] == stream_id for v in c["data"]["emg"]]
        assert min(stream_values) == pytest.approx(-(2 ** (acq["adc_bits"] - 1)) * lsb, abs=1e-6)
        assert max(stream_values) == pytest.approx((2 ** (acq["adc_bits"] - 1) - 1) * lsb, abs=1e-6)
