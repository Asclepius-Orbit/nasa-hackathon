"""Baseline real de extremo a extremo (#49): sesiones normales del simulador -> ``POST /features`` -> ``POST /baseline`` ->
``POST /assess`` de una sesión normal más: no debe desviarse.

Nada se toca a mano entre pasos: el feature set de cada sesión es la respuesta de ``/features`` tal cual, la baseline es la
respuesta de ``/baseline`` construida con esos feature sets y ``/assess`` recibe esa baseline. Es la prueba de que las
features extraídas de señal simulada son comparables entre sesiones del mismo protocolo y entorno de gravedad.

Los ayudantes de sesión se cargan por ruta desde ``test_features_stretch_simulator.py``: con ``--import-mode=importlib``
los tests no se importan entre sí por nombre.
"""

import copy
import importlib.util
import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from spine_contracts import validation
from spine_engine.api import create_default_app

_SPEC = importlib.util.spec_from_file_location("_stretch_simulator", Path(__file__).with_name("test_features_stretch_simulator.py"))
assert _SPEC is not None and _SPEC.loader is not None
_SIM: Any = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SIM)

Scenario = _SIM.Scenario
# Una semilla y la gravedad de la Tierra: la otra semilla y la microgravedad ya las cubren los tests de cada familia, y cada
# sesión de ejercicio pasa por HTTP (~8 s con chunks de 1 s; el global ``--timeout=120`` cubre la primera llamada).
SEED = 7
GRAVITY = "earth_1g"
CHUNK_MS = 1000
_SIM_EXERCISE_PROTOCOL = "lat_pulldown_standing_v1"
SLOTS = (_SIM.EXERCISE, _SIM.SPINE_CHECK)
# IMU y cordón: sin EMG a 1 kHz la petición de cada sesión pesa varias veces menos (validar y extraer 5 sesiones completas con
# las dos EMG tardaba minutos por combinación). Los dominios cinemática y longitud del cordón son los que se evalúan.
BASELINE_STREAMS = ("imu_t9", _SIM.STRETCH)
ASSESS_EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "assess-request.json"

# Juicio: cuántas sesiones normales forman la baseline. 3 es el mínimo con el que los detectores de cambio sostenido tienen
# puntos (``cusum_min_points`` y ``kalman_min_points`` valen 3); más alarga la simulación sin cambiar lo que se prueba
# (cada día son una sesión de ejercicio con 60 repeticiones y un spine_check, ~15 s de extracción por vía HTTP).
BASELINE_SESSIONS = 3
# La sesión que se evalúa es la siguiente, también normal: un día más de la misma misión.
HELD_OUT_DAY = BASELINE_SESSIONS
DAYS = BASELINE_SESSIONS + 1
INCOMPLETE_DATA_RULE = "RULE_INCOMPLETE_DATA"
# Control: las últimas SHIFTED_SESSIONS sesiones de ejercicio (la nueva incluida) multiplican por SHIFT_FACTOR la ROM sagital
# y las features del cordón. Los detectores piden >= 3 puntos (``cusum_min_points``): con 3 el cambio ya es sostenido, y
# x3 sale de la dispersión normal de la baseline (observado: riesgo saturado a 1,0 en la semilla 7).
SHIFTED_SESSIONS = 3
SHIFT_FACTOR = 3.0
SHIFTED_PREFIXES = ("imu.t9.sagittal", "stretch.")


@cache
def _feature_sets() -> tuple[str, ...]:
    """Feature sets (JSON) de cada sesión, día a día y en orden, tal como los devuelve ``POST /features``."""
    client = TestClient(create_default_app())
    scenario = Scenario(streams=BASELINE_STREAMS, days=DAYS, gravity=GRAVITY, chunk_ms=CHUNK_MS)
    out: list[str] = []
    for day in range(DAYS):
        for slot in SLOTS:
            response = client.post("/features", json=_SIM._simulate(scenario, SEED, slot, day).payload)
            assert response.status_code == 200, response.text
            out.append(json.dumps(response.json()))
    return tuple(out)


def _split() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(feature sets de la baseline, feature sets de la sesión que se evalúa)."""
    sets = [json.loads(s) for s in _feature_sets()]
    cut = BASELINE_SESSIONS * len(SLOTS)
    return sets[:cut], sets[cut:]


def _baseline(client: TestClient, feature_sets: list[dict[str, Any]]) -> dict[str, Any]:
    response = client.post("/baseline", json={"schema_version": "1.0.0", "astronaut_id": feature_sets[0]["astronaut_id"], "feature_sets": feature_sets})
    assert response.status_code == 200, response.text
    body = response.json()
    assert validation.errors("domain/baseline", body) == []
    return body


def _assess(client: TestClient, baseline: dict[str, Any], feature_sets: list[dict[str, Any]]) -> dict[str, Any]:
    request = json.loads(ASSESS_EXAMPLE.read_text(encoding="utf-8"))
    request.update(baseline=baseline, feature_sets=feature_sets, astronaut_id=baseline["astronaut_id"])
    request.pop("self_reports", None)
    request.pop("daily_context", None)
    response = client.post("/assess", json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    assert validation.errors("domain/assessment", body) == []
    return body


@pytest.mark.integration
def test_the_baseline_is_built_from_the_extracted_features_of_the_normal_sessions() -> None:
    client = TestClient(create_default_app())
    reference, _held_out = _split()

    baseline = _baseline(client, reference)

    extracted = {f["feature_id"] for fs in reference for f in fs["features"]}
    assert {e["feature_id"] for e in baseline["entries"]} == extracted
    assert {e["gravity_environment"] for e in baseline["entries"]} == {GRAVITY}
    assert len(baseline["built_from_session_ids"]) == BASELINE_SESSIONS * len(SLOTS)
    # una feature puede omitirse en alguna sesión (p. ej. recovery_time_s): n nunca supera las sesiones de la baseline
    assert all(1 <= e["n"] <= BASELINE_SESSIONS for e in baseline["entries"])
    counts = {e["feature_id"]: e["n"] for e in baseline["entries"] if e["protocol_id"] == _SIM_EXERCISE_PROTOCOL}
    assert counts["exercise.rep_count"] == BASELINE_SESSIONS
    assert baseline["version"] == 1


@pytest.mark.integration
def test_a_further_normal_session_does_not_deviate_from_the_real_baseline() -> None:
    client = TestClient(create_default_app())
    reference, held_out = _split()
    baseline = _baseline(client, reference)

    # como en el uso real, /assess recibe el histórico reciente y la sesión nueva
    assessment = _assess(client, baseline, [*reference, *held_out])

    assert assessment["risk_score"] == 0.0
    assert assessment["drivers"] == []
    assert assessment["based_on"]["baseline_id"] == baseline["baseline_id"]
    # El nivel no es verde, pero no por desviación: la regla de datos incompletos salta porque la completitud < 1 (no hay
    # autoinformes y cada spine_check omite las features de repeticiones que el ejercicio sí calcula). Ninguna otra regla.
    assert {r["rule_id"] for r in assessment["fired_rules"]} <= {INCOMPLETE_DATA_RULE}
    evaluated = {d["domain"] for d in assessment["domains"] if d["evaluated"]}
    assert {"kinematics", "spine_length"} <= evaluated


@pytest.mark.integration
def test_a_sustained_shift_of_the_same_sessions_does_deviate_so_the_normal_case_is_not_vacuous() -> None:
    client = TestClient(create_default_app())
    reference, held_out = _split()
    baseline = _baseline(client, reference)
    history = copy.deepcopy([*reference, *held_out])
    exercise = [fs for fs in history if fs["protocol_id"] == _SIM_EXERCISE_PROTOCOL]
    for feature_set in exercise[-SHIFTED_SESSIONS:]:
        for feature in feature_set["features"]:
            if feature["feature_id"].startswith(SHIFTED_PREFIXES):
                feature["value"] *= SHIFT_FACTOR

    assessment = _assess(client, baseline, history)

    assert assessment["risk_score"] > 0.0
    assert {d["feature_id"] for d in assessment["drivers"]} >= {"imu.t9.sagittal.rom_deg", "stretch.full_spine.range_mm"}
