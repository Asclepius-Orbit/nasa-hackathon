"""End-to-end ``emg_spectral`` family (#43): simulator sessions through ``CompositeFeatureExtractor(default_registry())``.

The truth is ``expected_features``: ``median_freq_hz`` is the mean of the model's band median at the midpoint of
each repetition and the slope is the linear fit against ``RepWindow.index``. The extraction measures the spectrum median
(Welch + Hann) over the whole active phase of the noisy raw signal, so there is a bias relative to the midpoint.
Sessions and seeds are those of #42 (``test_features_emg_simulator``), whose helpers are loaded by path: with
``--import-mode=importlib`` tests do not import each other by name.
"""

import copy
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from spine_contracts import FeaturesRequest, dump, validation
from spine_contracts.catalog import unit_for
from spine_engine.features.composite import CompositeFeatureExtractor
from spine_engine.features.registry import default_registry

_SPEC = importlib.util.spec_from_file_location("_emg_amplitude_simulator", Path(__file__).with_name("test_features_emg_simulator.py"))
assert _SPEC is not None and _SPEC.loader is not None
_AMPLITUDE: Any = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_AMPLITUDE)

MUSCLE: str = _AMPLITUDE.MUSCLE
SIDES: dict[str, str] = _AMPLITUDE.SIDES
SEEDS: list[int] = _AMPLITUDE.SEEDS
GRAVITIES: list[str] = _AMPLITUDE.GRAVITIES
NAMES = ("median_freq_hz", "median_freq_slope_hz_per_rep")

# Median: Welch over the whole active phase against the repetition midpoint, plus sensor noise: observed
# up to 1.2 % across the 4 seed x gravity combinations and both sides (truth ~220 Hz); threshold 3 %.
MEDIAN_REL = 0.03
# Slope (Hz/rep): with 10 noisy medians (each with ~1 Hz of error) the slope is the least stable quantity;
# the truth is ~-2.4 Hz/rep and -1.3 to -4.3 is observed, i.e. up to 1.9 Hz/rep of absolute error; threshold 3.
# What is required separately is the sign: with simulated fatigue the slope is always negative.
SLOPE_ABS_HZ_PER_REP = 3.0


def _simulate(seed: int, gravity: str) -> tuple[dict[str, Any], dict[str, float]]:
    return _AMPLITUDE._simulate(seed, gravity)


def _extract(payload: dict[str, Any]) -> dict[str, Any]:
    result = dump(CompositeFeatureExtractor(default_registry()).extract(FeaturesRequest.model_validate(payload)))
    assert validation.errors("domain/feature-set", result) == []
    return result


def _by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["feature_id"]: f for f in result["features"]}


def _ids() -> set[str]:
    return {f"emg.{MUSCLE}.{side}.{name}" for side in SIDES.values() for name in NAMES}


@pytest.mark.integration
@pytest.mark.parametrize("gravity", GRAVITIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_the_spectral_features_match_the_simulator_truth(seed: int, gravity: str):
    payload, truth = _simulate(seed, gravity)

    result = _extract(payload)

    features = _by_id(result)
    spectral = {f for f in features if f.endswith(NAMES)}
    assert spectral == _ids()
    assert not {s["feature_id"] for s in result.get("skipped", [])} & _ids()
    for side in SIDES.values():
        prefix = f"emg.{MUSCLE}.{side}"
        median, slope = features[f"{prefix}.median_freq_hz"], features[f"{prefix}.median_freq_slope_hz_per_rep"]
        assert median["value"] == pytest.approx(truth[f"{prefix}.median_freq_hz"], rel=MEDIAN_REL)
        assert slope["value"] < 0.0
        assert slope["value"] == pytest.approx(truth[f"{prefix}.median_freq_slope_hz_per_rep"], abs=SLOPE_ABS_HZ_PER_REP)
        assert median["n"] == slope["n"] == truth["exercise.rep_count"]
    for feature_id in spectral:
        assert features[feature_id]["unit"] == unit_for(feature_id)
        assert features[feature_id]["quality"]["assessed_by"] == "engine"


@pytest.mark.integration
def test_relabelling_the_signal_as_envelope_skips_both_spectral_features_of_each_side_as_not_raw():
    payload, _truth = _simulate(7, "earth_1g")
    relabelled = copy.deepcopy(payload)
    for chunk in relabelled["chunks"]:
        if chunk["stream_id"] in SIDES:
            chunk["signal_kind"] = "envelope"

    result = _extract(relabelled)

    assert not _ids() & set(_by_id(result))
    assert {s["feature_id"]: s["reason"] for s in result["skipped"] if s["feature_id"] in _ids()} == dict.fromkeys(_ids(), "signal_not_raw")
