"""Familia ``emg_spectral`` (#43) de extremo a extremo: sesiones del simulador por ``CompositeFeatureExtractor(default_registry())``.

La verdad es ``expected_features``: ``median_freq_hz`` es la media de la mediana de banda del modelo en el punto medio de
cada repetición y la pendiente es el ajuste lineal contra ``RepWindow.index``. La extracción mide la mediana del espectro
(Welch + Hann) sobre toda la fase activa de la señal cruda con ruido, así que hay un sesgo respecto al punto medio.
Sesiones y semillas son las de #42 (``test_features_emg_simulator``), cuyos helpers se cargan por ruta: con
``--import-mode=importlib`` los tests no se importan entre sí por nombre.
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

# Mediana: Welch sobre toda la fase activa frente al punto medio de la repetición, más el ruido de sensor: observado
# hasta 1,2 % en las 4 combinaciones semilla x gravedad y los dos lados (verdad ~220 Hz); umbral 3 %.
MEDIAN_REL = 0.03
# Pendiente (Hz/rep): con 10 medianas ruidosas (cada una con ~1 Hz de error) la pendiente es la magnitud menos estable;
# la verdad es ~-2,4 Hz/rep y se observa entre -1,3 y -4,3, es decir, hasta 1,9 Hz/rep de error absoluto; umbral 3.
# Lo que sí se exige aparte es el signo: con la fatiga simulada la pendiente es siempre negativa.
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
