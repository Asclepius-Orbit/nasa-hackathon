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
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from spine_contracts import Level, catalog, validation
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
# Control: todas las sesiones de ejercicio del historial (la nueva incluida) desplazan por un factor SHIFT_FACTOR la ROM
# sagital y las features del cordón, en el sentido malo que declara el catálogo (``lower_is_worse`` divide, el resto
# multiplica): el riesgo ignora el sentido bueno. Se desplazan todas porque una sesión sin desplazar al inicio de la serie
# (z alto y de signo contrario) impide que el detector marque el cambio como sostenido. Un factor 3 sale de la dispersión
# normal de la baseline (observado antes de #51: riesgo saturado a 1,0 en la semilla 7).
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


def _baseline(client: TestClient, feature_sets: list[dict[str, Any]], self_reports: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    body = {"schema_version": "1.0.0", "astronaut_id": feature_sets[0]["astronaut_id"], "feature_sets": feature_sets}
    if self_reports is not None:
        body["self_reports"] = self_reports
    response = client.post("/baseline", json=body)
    assert response.status_code == 200, response.text
    body = response.json()
    assert validation.errors("domain/baseline", body) == []
    return body


def _normal_context(feature_sets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """(daily self-reports, daily context, as_of) of the simulated days: the example's own report, repeated each day.

    The report values and the context come from the contract example (no clinical value of its own); only the ids, the
    timestamps and the gravity environment are adapted so that every day has one report before its sessions and the
    baseline and the new session see the same normal self-reports.
    """
    example = json.loads(ASSESS_EXAMPLE.read_text(encoding="utf-8"))
    ends = [datetime.fromisoformat(fs["window"]["end"]) for fs in feature_sets]
    days = sorted({end.date() for end in ends})
    reports = []
    for index, day in enumerate(days):
        report = copy.deepcopy(example["self_reports"][0])
        report["report_id"] = f"5e1f0e90-0000-4000-8000-{index:012d}"
        report["timestamp"] = datetime.combine(day, datetime.min.time(), tzinfo=ends[0].tzinfo).replace(hour=8).isoformat()
        report.pop("mission_day", None)
        reports.append(report)
    as_of = max(ends) + timedelta(minutes=5)
    context = copy.deepcopy(example["daily_context"][0])
    context.update(date=as_of.date().isoformat(), gravity_environment=GRAVITY, phase="preflight")
    context.pop("mission_day", None)
    return reports, [context], as_of.isoformat()


def _assess(
    client: TestClient,
    baseline: dict[str, Any],
    feature_sets: list[dict[str, Any]],
    *,
    self_reports: list[dict[str, Any]] | None = None,
    daily_context: list[dict[str, Any]] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    request = json.loads(ASSESS_EXAMPLE.read_text(encoding="utf-8"))
    request.update(baseline=baseline, feature_sets=feature_sets, astronaut_id=baseline["astronaut_id"])
    request.pop("self_reports", None)
    request.pop("daily_context", None)
    request.pop("mission_day", None)
    if self_reports is not None:
        request["self_reports"] = self_reports
    if daily_context is not None:
        request["daily_context"] = daily_context
    if as_of is not None:
        request["as_of"] = as_of
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


def _missing(assessment: dict[str, Any]) -> list[str]:
    return [item["what"] for item in assessment["completeness"]["missing"]]


@pytest.mark.integration
def test_a_further_normal_session_with_normal_self_reports_is_green_and_misses_nothing_computed_elsewhere() -> None:
    client = TestClient(create_default_app())
    reference, held_out = _split()
    reports, context, as_of = _normal_context([*reference, *held_out])
    baseline = _baseline(client, reference, reports)

    # como en el uso real, /assess recibe el histórico reciente, la sesión nueva y los autoinformes
    assessment = _assess(client, baseline, [*reference, *held_out], self_reports=reports, daily_context=context, as_of=as_of)

    assert assessment["risk_score"] == 0.0
    assert assessment["drivers"] == []
    assert assessment["based_on"]["baseline_id"] == baseline["baseline_id"]
    assert assessment["level"] == Level.GREEN.value
    assert assessment["fired_rules"] == []
    evaluated = {d["domain"] for d in assessment["domains"] if d["evaluated"]}
    assert {"kinematics", "spine_length"} <= evaluated
    missing = _missing(assessment)
    assert not [what for what in missing if what.startswith(("exercise.", "imu."))]
    # a feature one set skipped but another computed is not missing
    computed = {f["feature_id"] for fs in [*reference, *held_out] for f in fs["features"]}
    assert computed.isdisjoint(missing)


@pytest.mark.integration
def test_without_self_reports_only_the_symptom_features_are_missing() -> None:
    client = TestClient(create_default_app())
    reference, held_out = _split()
    baseline = _baseline(client, reference)

    assessment = _assess(client, baseline, [*reference, *held_out])

    assert assessment["risk_score"] == 0.0
    assert assessment["drivers"] == []
    missing = _missing(assessment)
    assert missing
    assert all(what.startswith("symptoms.") for what in missing)
    assert {r["rule_id"] for r in assessment["fired_rules"]} <= {INCOMPLETE_DATA_RULE}


@pytest.mark.integration
def test_a_sustained_shift_of_the_same_sessions_does_deviate_so_the_normal_case_is_not_vacuous() -> None:
    client = TestClient(create_default_app())
    reference, held_out = _split()
    baseline = _baseline(client, reference)
    history = copy.deepcopy([*reference, *held_out])
    exercise = [fs for fs in history if fs["protocol_id"] == _SIM_EXERCISE_PROTOCOL]
    for feature_set in exercise:
        for feature in feature_set["features"]:
            if feature["feature_id"].startswith(SHIFTED_PREFIXES):
                worse_when_lower = catalog.risk_direction(feature["feature_id"]) == "lower_is_worse"
                feature["value"] = feature["value"] / SHIFT_FACTOR if worse_when_lower else feature["value"] * SHIFT_FACTOR

    assessment = _assess(client, baseline, history)

    assert assessment["risk_score"] > 0.0
    assert {d["feature_id"] for d in assessment["drivers"]} >= {"imu.t9.sagittal.rom_deg", "stretch.full_spine.range_mm"}
