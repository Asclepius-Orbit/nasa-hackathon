"""End-to-end real baseline (#49): normal simulator sessions -> ``POST /features`` -> ``POST /baseline`` ->
``POST /assess`` of one more normal session: it must not deviate.

Nothing is touched by hand between steps: each session's feature set is the ``/features`` response as is, the baseline is the
``/baseline`` response built from those feature sets, and ``/assess`` receives that baseline. It proves that the
features extracted from simulated signal are comparable across sessions of the same protocol and gravity environment.

The session helpers are loaded by path from ``test_features_stretch_simulator.py``: with ``--import-mode=importlib``
tests do not import each other by name.
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
# One seed and Earth gravity: the other seed and microgravity are already covered by each family's tests, and each
# exercise session goes over HTTP (~8 s with 1 s chunks; the global ``--timeout=120`` covers the first call).
SEED = 7
GRAVITY = "earth_1g"
CHUNK_MS = 1000
_SIM_EXERCISE_PROTOCOL = "lat_pulldown_standing_v1"
SLOTS = (_SIM.EXERCISE, _SIM.SPINE_CHECK)
# IMU and cord: without EMG at 1 kHz each session's request weighs several times less (validating and extracting 5 full sessions with
# both EMGs took minutes per combination). The kinematics and cord length domains are the ones evaluated.
BASELINE_STREAMS = ("imu_t9", _SIM.STRETCH)
ASSESS_EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "assess-request.json"

# Judgement: how many normal sessions make up the baseline. 3 is the minimum with which the sustained-change detectors have
# points (``cusum_min_points`` and ``kalman_min_points`` are 3); more lengthens the simulation without changing what is tested
# (each day is an exercise session with 60 repetitions and a spine_check, ~15 s of extraction over HTTP).
BASELINE_SESSIONS = 3
# The session under evaluation is the next one, also normal: one more day of the same mission.
HELD_OUT_DAY = BASELINE_SESSIONS
DAYS = BASELINE_SESSIONS + 1
INCOMPLETE_DATA_RULE = "RULE_INCOMPLETE_DATA"
# Control: all exercise sessions in the history (the new one included) shift the sagittal ROM and the cord features by a
# SHIFT_FACTOR, in the bad direction declared by the catalogue (``lower_is_worse`` divides, the rest
# multiplies): the risk ignores the good direction. All are shifted because an unshifted session at the start of the series
# (high z with opposite sign) stops the detector from flagging the change as sustained. A factor of 3 comes from the dispersion
# normal baseline spread (observed before #51: risk saturated at 1.0 on seed 7).
SHIFT_FACTOR = 3.0
SHIFTED_PREFIXES = ("imu.t9.sagittal", "stretch.")


@cache
def _feature_sets() -> tuple[str, ...]:
    """Feature sets (JSON) of each session, day by day and in order, as returned by ``POST /features``."""
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
    """(baseline feature sets, feature sets of the session under evaluation)."""
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
    # a feature may be omitted in some session (e.g. recovery_time_s): n never exceeds the baseline sessions
    assert all(1 <= e["n"] <= BASELINE_SESSIONS for e in baseline["entries"])
    counts = {e["feature_id"]: e["n"] for e in baseline["entries"] if e["protocol_id"] == _SIM_EXERCISE_PROTOCOL}
    assert counts["exercise.rep_count"] == BASELINE_SESSIONS
    assert baseline["version"] == 1


def _missing(assessment: dict[str, Any]) -> list[str]:
    return [item["what"] for item in assessment["completeness"]["missing"]]


@pytest.mark.integration
def test_normal_session_is_incomplete_without_current_measured_windows() -> None:
    client = TestClient(create_default_app())
    reference, held_out = _split()
    reports, context, as_of = _normal_context([*reference, *held_out])
    baseline = _baseline(client, reference, reports)

    # as in real use, /assess receives the recent history, the new session and the self-reports
    assessment = _assess(client, baseline, [*reference, *held_out], self_reports=reports, daily_context=context, as_of=as_of)

    assert assessment["risk_score"] == 0.0
    assert assessment["drivers"] == []
    assert assessment["based_on"]["baseline_id"] == baseline["baseline_id"]
    assert assessment["level"] == Level.AMBER.value
    assert {r["rule_id"] for r in assessment["fired_rules"]} == {INCOMPLETE_DATA_RULE}
    evaluated = {d["domain"] for d in assessment["domains"] if d["evaluated"]}
    assert {"kinematics", "spine_length"} <= evaluated
    missing = _missing(assessment)
    assert "imu.lumbar.sensor_x.orientation_deg" in missing
    # a feature one set skipped but another computed is not missing
    computed = {f["feature_id"] for fs in [*reference, *held_out] for f in fs["features"]}
    measured = {"imu.lumbar.sensor_x.orientation_deg", "stretch.full_spine.resistance_change_pct", "emg.reference.normalized_amplitude"}
    assert (computed - measured).isdisjoint(missing)


@pytest.mark.integration
def test_without_self_reports_symptoms_and_current_measured_windows_are_missing() -> None:
    client = TestClient(create_default_app())
    reference, held_out = _split()
    baseline = _baseline(client, reference)

    assessment = _assess(client, baseline, [*reference, *held_out])

    assert assessment["risk_score"] == 0.0
    assert assessment["drivers"] == []
    missing = _missing(assessment)
    assert missing
    assert all(what.startswith("symptoms.") or what in {
        "imu.lumbar.sensor_x.orientation_deg", "stretch.full_spine.resistance_change_pct", "emg.reference.normalized_amplitude"
    } for what in missing)
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
