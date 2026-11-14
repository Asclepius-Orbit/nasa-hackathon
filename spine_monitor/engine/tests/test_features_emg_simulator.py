"""Familia ``emg`` (#42) de extremo a extremo: sesiones del simulador por ``CompositeFeatureExtractor(default_registry())``.

La verdad es ``expected_features`` del simulador, calculada con la MVC real; la petición lleva la MVC *medida* (la
calibración ``emg_mvc`` con ±``measurement_noise_rel`` de ruido) y la señal cruda con ruido de sensor, así que los %MVC
y la simetría se comparan con tolerancia. Las calibraciones salen de los sobres ``EnvelopeKind.CALIBRATION``.
"""

import copy
import math
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import spine_physiology
from spine_contracts import FeaturesRequest, dump, validation
from spine_contracts.catalog import unit_for
from spine_engine.features.composite import CompositeFeatureExtractor
from spine_engine.features.registry import default_registry

# vía el paquete instalado, no vía parents[]: bajo mutmut los tests se copian a otro árbol sin physiology/
_MISSION = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
MUSCLE = "latissimus_dorsi"
LEFT, RIGHT = "emg_lat_left", "emg_lat_right"
SIDES = {LEFT: "left", RIGHT: "right"}
SYMMETRY = f"emg.{MUSCLE}.symmetry_index_pct"
SEEDS = [7, 20261113]
GRAVITIES = ["earth_1g", "microgravity"]

# Tolerancias frente a la verdad (MVC real, señal sin fallos inyectados); observado en las 4 combinaciones semilla x gravedad.
# RMS activo y reposo: solo ruido de sensor sobre 10 repeticiones: observado < 1 %.
RMS_MV_REL = 0.05
REST_REL = 0.05
# %MVC: añade el error de la MVC medida (sigma 5 %): observado hasta 6,6 %.
PCT_MVC_REL = 0.12
# Pico: el RMS en ventana de la señal con ruido supera al máximo instantáneo de la envolvente limpia: observado hasta +17 %.
PEAK_REL = 0.30
# Simetría: la verdad es 0 (mismo %MVC en ambos lados); la MVC medida de cada lado aporta ~5 % de sigma: observado hasta 13 %.
SYMMETRY_ABS_PCT = 20.0


def _is_amplitude(feature_id: str) -> bool:
    """Los ids ``emg.*`` de #42; los espectrales de #43 (``emg_spectral``) se prueban en su propio fichero."""
    return feature_id.startswith("emg.") and not feature_id.endswith(("median_freq_hz", "median_freq_slope_hz_per_rep"))


def _mission_config(*, faults: bool) -> Any:
    import yaml
    from spine_physiology.config import MissionConfig

    raw = yaml.safe_load(_MISSION.read_text(encoding="utf-8"))
    raw["start_mission_day"] = 1
    raw["segments"] = [{"days": 1, "phase": "inflight", "gravity_environment": "microgravity"}]
    raw["device"].update(power_on_time_utc="09:55", power_off_time_utc="10:40")
    raw["protocols"]["lat_pulldown_standing_v1"].update(streams=[LEFT, RIGHT], lead_out_s=10.0)
    if not faults:
        # los artefactos inyectados (clipping, lead-off...) inflan el RMS crudo sin tocar la verdad: se comparan sin ellos
        for fault in raw["faults"]["quality"].values():
            fault["probability"] = 0.0
    raw["schedule"]["sessions"] = [{**s, "start_jitter_s": 60.0} for s in raw["schedule"]["sessions"] if s["name"] == "lat_pulldown"]
    raw["schedule"]["background"].update(enabled=False, start_time_utc="09:56", end_time_utc="09:57")
    return MissionConfig.model_validate(raw)


def _simulate(seed: int, gravity: str, *, faults: bool = False) -> tuple[dict[str, Any], dict[str, float]]:
    """Copia propia (los tests la modifican) de :func:`_simulated`."""
    return copy.deepcopy(_simulated(seed, gravity, faults))


@cache
def _simulated(seed: int, gravity: str, faults: bool) -> tuple[dict[str, Any], dict[str, float]]:
    """Carga útil de una petición de features (con las calibraciones MVC) y la verdad ``expected_features``."""
    from spine_contracts.models.common_schema import GravityEnvironment
    from spine_physiology import MissionGenerator
    from spine_physiology.envelope import EnvelopeKind
    from spine_physiology.sessions import SessionRef

    config = _mission_config(faults=faults)
    generator = MissionGenerator(config, seed)
    ref = SessionRef(config.crew[0].astronaut_id, 0, "lat_pulldown")
    messages = list(generator.session_messages(ref, gravity_environment=GravityEnvironment(gravity)))
    # el helper de sesión no trae las calibraciones: las emite el generador del día (y las de la sesión, si las hay)
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


def _extract(payload: dict[str, Any]) -> dict[str, Any]:
    result = dump(CompositeFeatureExtractor(default_registry()).extract(FeaturesRequest.model_validate(payload)))
    assert validation.errors("domain/feature-set", result) == []
    return result


def _by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["feature_id"]: f for f in result["features"]}


def _mvc_of(payload: dict[str, Any], stream_id: str) -> float:
    return next(c["mvc_rms_mv"] for c in payload["calibrations"] if c["calibration_type"] == "emg_mvc" and c["stream_id"] == stream_id)


@pytest.mark.integration
@pytest.mark.parametrize("gravity", GRAVITIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_the_amplitude_features_match_the_simulator_truth(seed: int, gravity: str):
    payload, truth = _simulate(seed, gravity)

    result = _extract(payload)

    features = _by_id(result)
    emg_ids = {f for f in features if _is_amplitude(f)}
    assert emg_ids == {f"emg.{MUSCLE}.{side}.{name}" for side in SIDES.values() for name in ("rms_mv", "rest_rms_mv", "rms_pct_mvc", "peak_pct_mvc")} | {
        SYMMETRY
    }
    assert result.get("skipped", []) == []
    for side in SIDES.values():
        prefix = f"emg.{MUSCLE}.{side}"
        assert features[f"{prefix}.rms_mv"]["value"] == pytest.approx(truth[f"{prefix}.rms_mv"], rel=RMS_MV_REL)
        assert features[f"{prefix}.rest_rms_mv"]["value"] == pytest.approx(truth[f"{prefix}.rest_rms_mv"], rel=REST_REL)
        assert features[f"{prefix}.rms_pct_mvc"]["value"] == pytest.approx(truth[f"{prefix}.rms_pct_mvc"], rel=PCT_MVC_REL)
        assert features[f"{prefix}.peak_pct_mvc"]["value"] == pytest.approx(truth[f"{prefix}.peak_pct_mvc"], rel=PEAK_REL)
        assert features[f"{prefix}.peak_pct_mvc"]["value"] >= features[f"{prefix}.rms_pct_mvc"]["value"]
        assert features[f"{prefix}.rms_mv"]["n"] == truth["exercise.rep_count"]
    assert truth[SYMMETRY] == 0.0
    assert features[SYMMETRY]["value"] == pytest.approx(truth[SYMMETRY], abs=SYMMETRY_ABS_PCT)
    for feature_id in emg_ids:
        assert features[feature_id]["unit"] == unit_for(feature_id)
        assert features[feature_id]["quality"]["assessed_by"] == "engine"


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
def test_the_pct_mvc_is_exactly_the_rms_over_the_measured_mvc_of_the_request(seed: int):
    payload, _truth = _simulate(seed, "earth_1g")

    features = _by_id(_extract(payload))

    for stream_id, side in SIDES.items():
        prefix = f"emg.{MUSCLE}.{side}"
        rms = features[f"{prefix}.rms_mv"]["value"]
        assert features[f"{prefix}.rms_pct_mvc"]["value"] == pytest.approx(100.0 * rms / _mvc_of(payload, stream_id), rel=1e-9)


@pytest.mark.integration
def test_without_the_calibrations_the_amplitude_stays_and_the_mvc_features_are_skipped():
    payload, _truth = _simulate(7, "earth_1g")
    payload["calibrations"] = [c for c in payload["calibrations"] if c["calibration_type"] != "emg_mvc"]

    result = _extract(payload)

    emg = {f for f in _by_id(result) if _is_amplitude(f)}
    assert emg == {f"emg.{MUSCLE}.{side}.{name}" for side in SIDES.values() for name in ("rms_mv", "rest_rms_mv")}
    skipped = {s["feature_id"]: s["reason"] for s in result["skipped"]}
    assert set(skipped.values()) == {"no_calibration"}
    assert set(skipped) == {f"emg.{MUSCLE}.{side}.{name}" for side in SIDES.values() for name in ("rms_pct_mvc", "peak_pct_mvc")} | {SYMMETRY}


@pytest.mark.integration
def test_expired_calibrations_skip_the_mvc_features_as_calibration_expired():
    payload, _truth = _simulate(7, "earth_1g")
    long_ago = (FeaturesRequest.model_validate(payload).session.started_at - timedelta(days=30)).isoformat()
    payload["calibrations"] = [{**c, "valid_until": long_ago} if c["calibration_type"] == "emg_mvc" else c for c in payload["calibrations"]]

    result = _extract(payload)

    assert {s["reason"] for s in result["skipped"]} == {"calibration_expired"}
    assert SYMMETRY in {s["feature_id"] for s in result["skipped"]}


@pytest.mark.integration
def test_a_single_calibrated_side_gives_its_own_pct_mvc_but_no_symmetry():
    payload, _truth = _simulate(7, "earth_1g")
    payload["calibrations"] = [c for c in payload["calibrations"] if c.get("stream_id") != RIGHT or c["calibration_type"] != "emg_mvc"]

    result = _extract(payload)

    features = _by_id(result)
    assert f"emg.{MUSCLE}.left.rms_pct_mvc" in features
    assert f"emg.{MUSCLE}.right.rms_pct_mvc" not in features
    assert {s["feature_id"]: s["reason"] for s in result["skipped"]}[SYMMETRY] == "no_calibration"


@pytest.mark.integration
def test_relabelling_the_signal_and_its_mvc_as_envelope_gives_the_same_amplitude_features():
    payload, _truth = _simulate(7, "earth_1g")
    # la MVC solo vale para el mismo tipo de señal: se reetiquetan los chunks y sus calibraciones
    calibrations = [{**c, "signal_kind": "envelope"} if c["calibration_type"] == "emg_mvc" else c for c in payload["calibrations"]]
    relabelled = {**payload, "chunks": [{**c, "signal_kind": "envelope"} for c in payload["chunks"]], "calibrations": calibrations}

    straight = {k: v["value"] for k, v in _by_id(_extract(payload)).items() if _is_amplitude(k)}
    other = {k: v["value"] for k, v in _by_id(_extract(relabelled)).items() if _is_amplitude(k)}

    assert other == straight


@pytest.mark.integration
def test_with_the_injected_signal_faults_the_features_are_still_finite_and_ordered():
    # semilla 20261113 con fallos: el clipping inflaría el RMS crudo (no se compara con la verdad), pero nada se rompe
    payload, _truth = _simulate(20261113, "earth_1g", faults=True)

    features = _by_id(_extract(payload))

    for side in SIDES.values():
        prefix = f"emg.{MUSCLE}.{side}"
        assert all(math.isfinite(features[f"{prefix}.{n}"]["value"]) for n in ("rms_mv", "rest_rms_mv", "rms_pct_mvc", "peak_pct_mvc"))
        assert features[f"{prefix}.peak_pct_mvc"]["value"] >= features[f"{prefix}.rms_pct_mvc"]["value"]
    assert 0.0 <= features[SYMMETRY]["value"] <= 200.0
