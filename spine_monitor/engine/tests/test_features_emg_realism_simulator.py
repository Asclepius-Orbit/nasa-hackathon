"""Familia ``emg`` con el realismo del módulo encendido (#131): cuantización del ADC, red a 50 Hz, deriva de línea base y fuga de ECG.

Mismas sesiones y helpers que ``test_features_emg_simulator`` (cargados por ruta: con ``--import-mode=importlib`` los tests no
se importan entre sí por nombre), pero con ``signals.emg`` modificado. Los valores de interferencia son **hipótesis** sin cifra
verificada (``docs/sensor-sources.md``): el módulo comprado es defectuoso y no hay EMG medido, así que esto comprueba que el
motor no se rompe con esos artefactos y cuánto se desvía de la verdad del simulador, no que reproduzca el hardware.
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

# RMS activo: la interferencia (~0,07 mV rms) es una fracción pequeña del RMS de esfuerzo (~0,5 mV rms), que suma en cuadratura:
# observado 1,6 % (semilla 7, ambos lados); umbral 5 % (el mismo que sin artefactos, ver test_features_emg_simulator).
RMS_MV_REL = 0.05
# RMS en reposo: sin artefactos es ~0,04 mV; con ellos domina la interferencia. Teórico sqrt(verdad^2 + interferencia^2);
# observado 2-3 % de desvío de eso; umbral 10 % (ventanas de reposo cortas: ~1 s entre reps).
REST_REL = 0.10
# %MVC: añade el error de la MVC medida (sigma 5 %) como sin artefactos: umbral 12 %.
PCT_MVC_REL = 0.12
# Mediana espectral: la red a 50 Hz está dentro de la banda y tira de la mediana (~220 Hz) hacia abajo una fracción mínima;
# observado 1,3 %; umbral 4 % (sin artefactos es 3 %).
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
    assert result.get("skipped", []) == []
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
        assert features[f"{prefix}.rest_rms_mv"]["value"] > truth[f"{prefix}.rest_rms_mv"]  # los artefactos solo suman
        assert features[f"{prefix}.median_freq_hz"]["value"] == pytest.approx(truth[f"{prefix}.median_freq_hz"], rel=MEDIAN_REL)
        assert features[f"{prefix}.median_freq_slope_hz_per_rep"]["value"] < 0.0  # la fatiga sigue viéndose
        assert features[f"{prefix}.rms_mv"]["n"] == truth["exercise.rep_count"]


@pytest.mark.integration
def test_the_quantized_session_is_made_of_whole_adc_steps_and_inside_the_adc_range():
    payload, _truth = _simulated()
    acq = next(c["acquisition"] for c in payload["chunks"] if c["stream_kind"] == "emg")
    lsb = acq["vref_v"] * 1000.0 / acq["gain"] / 2 ** acq["adc_bits"]
    values = [v for c in payload["chunks"] if c["stream_kind"] == "emg" for v in c["data"]["emg"]]
    assert values
    assert max(abs(v / lsb - round(v / lsb)) for v in values) < 1e-3  # el JSON redondea; los pasos siguen siendo enteros
    assert min(values) >= -(2 ** (acq["adc_bits"] - 1)) * lsb - 1e-9 and max(values) <= (2 ** (acq["adc_bits"] - 1) - 1) * lsb + 1e-9
