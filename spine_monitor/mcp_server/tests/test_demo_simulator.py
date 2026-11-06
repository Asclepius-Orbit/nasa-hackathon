"""Valores exactos del simulador de demo: la referencia es la valoración de `now` y el escenario mueve el riesgo de forma lineal.

Fórmula (calculada a mano en cada caso, no llamando al código): referencia = riesgo 0,62, elongación 9,1 mm, dominios
spine_length 0,70, muscle 0,55, kinematics 0,20, symptoms 0,47. Con `extra` mm de más:
    z = round((9,1 + extra - 6,5) / 1,083, 1)
    spine_length = round(clamp(0,1 + 0,25 z), 2);  contribución = round(clamp(0,13 z, 0,02, 0,35), 2)
    riesgo = round(clamp(0,62 + 0,052 extra, 0, 0,99), 2)
    nivel = el peor entre el nivel del riesgo (<0,4 green, <0,8 amber, resto red) y el de los dominios (muscle 0,55 es siempre amber).
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from spine_contracts import SimulateRequest, dump
from spine_mcp_server.demo_data import build_demo_app_reader, build_demo_assess_base, build_demo_simulator

AST = "AST-01"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
STRETCH = "stretch.lumbar.peak_elongation_mm"
OTHER = "emg.latissimus_dorsi.left.rms_pct_mvc"
SURGEON = "REC_CONTACT_FLIGHT_SURGEON"


def _simulate(overrides: list[dict[str, Any]], project_days: int | None = None, now: datetime = NOW) -> dict[str, Any]:
    body: dict[str, Any] = {"schema_version": "1.0.0", "base": dump(build_demo_assess_base(now).get_base(AST)), "overrides": overrides}
    if project_days is not None:
        body["project_days"] = project_days
    return dump(build_demo_simulator(now).simulate(SimulateRequest.model_validate(body)))


def _ov(mode: str, value: float, feature_id: str = STRETCH) -> dict[str, Any]:
    return {"feature_id": feature_id, "mode": mode, "value": value}


def _driver(assessment: dict[str, Any], feature_id: str = STRETCH) -> dict[str, Any]:
    return next(d for d in assessment["drivers"] if d["feature_id"] == feature_id)


def _domain(assessment: dict[str, Any], name: str = "spine_length") -> dict[str, Any]:
    return next(d for d in assessment["domains"] if d["domain"] == name)


def test_reference_is_the_latest_assessment_with_known_numbers():
    reference = _simulate([])["reference"]
    assert (reference["risk_score"], reference["level"]) == (0.62, "amber")
    assert (_driver(reference)["value"], _driver(reference)["deviation_z"], _driver(reference)["contribution"]) == (9.1, 2.4, 0.31)
    assert [(d["domain"], d["score"], d["level"]) for d in reference["domains"]] == [
        ("spine_length", 0.7, "amber"),
        ("muscle", 0.55, "amber"),
        ("kinematics", 0.2, "green"),
        ("symptoms", 0.47, "amber"),
    ]
    assert reference == dump(build_demo_app_reader(NOW).get_status(AST).latest_assessment)


@pytest.mark.parametrize(
    "overrides",
    [
        [],
        [_ov("add", 0)],
        [_ov("set", 9.1)],
        [_ov("scale", 1)],
        [_ov("add", 7, OTHER), _ov("set", 1, OTHER), _ov("scale", 3, OTHER)],
        [_ov("add", 1), _ov("add", -1)],
    ],
    ids=["empty", "add-0", "set-current", "scale-1", "other-features", "cancelling-adds"],
)
def test_overrides_that_do_not_move_the_stretch_leave_the_scenario_equal_to_the_reference(overrides: list[dict[str, Any]]):
    response = _simulate(overrides)
    assert response["scenario"] == response["reference"]
    assert response["hypothetical"] is True
    assert response["schema_version"] == "1.0.0"


# (overrides, nuevo stretch, z, score spine_length, contribución, riesgo, nivel)
AMBER_CASES = [
    pytest.param([_ov("add", -2)], 7.1, 0.6, 0.25, 0.08, 0.52, "amber", id="add-2-down"),  # 0,62-0,104 = 0,516
    pytest.param([_ov("set", 7.1)], 7.1, 0.6, 0.25, 0.08, 0.52, "amber", id="set-7.1"),  # extra = 7,1-9,1 = -2
    pytest.param([_ov("scale", 0.5)], 4.55, -1.8, 0.0, 0.02, 0.38, "amber", id="scale-half"),  # extra -4,55: riesgo green pero muscle amber
    pytest.param([_ov("add", 0.4)], 9.5, 2.8, 0.8, 0.36, 0.64, "amber", id="add-0.4"),  # riesgo 0,62+0,0208
    pytest.param([_ov("add", 1), _ov("set", 7.1)], 8.1, 1.5, 0.47, 0.2, 0.57, "amber", id="mixed-add-then-set-uses-reference"),
]


@pytest.mark.parametrize(("overrides", "value", "z", "spine", "contribution", "risk", "level"), AMBER_CASES)
def test_scenarios_that_stay_amber_have_the_hand_computed_numbers(overrides, value, z, spine, contribution, risk, level):  # noqa: ANN001
    scenario = _simulate(overrides)["scenario"]
    assert (_driver(scenario)["value"], _driver(scenario)["deviation_z"]) == (value, z)
    assert _domain(scenario)["score"] == pytest.approx(spine, abs=0.011)
    assert _driver(scenario)["contribution"] == pytest.approx(contribution, abs=0.011)
    assert (scenario["risk_score"], scenario["level"]) == (risk, level)
    assert scenario["trend"] == {"projected_level": "red", "days_to_threshold": 12.0}
    assert scenario["recommendations"] == ["REC_LUMBAR_STAB_01", "REC_INCREASE_ARED"]
    assert scenario["escalation"] == {"required": False, "reasons": []}


def test_amber_scenario_keeps_everything_else_of_the_reference():
    response = _simulate([_ov("add", -2)])
    reference, scenario = response["reference"], response["scenario"]
    assert scenario["assessment_id"] != reference["assessment_id"]
    assert _driver(scenario, "symptoms.pain_nrs_7d_mean") == _driver(reference, "symptoms.pain_nrs_7d_mean")
    assert [d for d in scenario["domains"] if d["domain"] != "spine_length"] == [d for d in reference["domains"] if d["domain"] != "spine_length"]
    for key in ("as_of", "mission_day", "confidence", "fired_rules", "based_on", "astronaut_id"):
        assert scenario[key] == reference[key]


def test_the_level_flips_to_red_exactly_between_0_48_and_0_49_mm_of_extra_stretch():
    # extra 0,48: z = round(3,08 / 1,083, 1) = 2,8 -> 0,1 + 0,25 * 2,8 queda en 0,7999... (< 0,8): amber, y el riesgo es 0,64496 -> 0,64
    below = _simulate([_ov("add", 0.48)])["scenario"]
    assert (_driver(below)["deviation_z"], below["risk_score"], below["level"]) == (2.8, 0.64, "amber")
    assert _domain(below)["score"] == 0.8
    assert _domain(below)["level"] == "amber"
    assert below["escalation"]["required"] is False
    assert SURGEON not in below["recommendations"]
    # extra 0,49: z = round(3,09 / 1,083, 1) = 2,9 -> spine_length 0,825 -> red (el riesgo, 0,64548 -> 0,65, seguiría en amber)
    above = _simulate([_ov("add", 0.49)])["scenario"]
    assert (_driver(above)["deviation_z"], above["risk_score"], above["level"]) == (2.9, 0.65, "red")
    assert _domain(above)["score"] == pytest.approx(0.82, abs=0.011)
    assert _domain(above)["level"] == "red"


def test_red_scenario_escalates_and_recommends_the_flight_surgeon():
    scenario = _simulate([_ov("add", 5)])["scenario"]
    # extra 5: 14,1 mm, z = round(7,6 / 1,083, 1) = 7,0, spine 1,0 (tope), contribución 0,35 (tope), riesgo 0,62 + 0,26 = 0,88
    assert (_driver(scenario)["value"], _driver(scenario)["deviation_z"], _driver(scenario)["contribution"]) == (14.1, 7.0, 0.35)
    assert _domain(scenario)["score"] == 1.0
    assert (scenario["risk_score"], scenario["level"]) == (0.88, "red")
    assert scenario["trend"] == {"projected_level": "red", "days_to_threshold": 0}
    assert scenario["recommendations"] == ["REC_LUMBAR_STAB_01", "REC_INCREASE_ARED", SURGEON]
    assert scenario["escalation"] == {"required": True, "urgency": "priority", "reasons": ["Scenario: spine elongation beyond the alert range."]}


def test_risk_is_clamped_to_0_99_and_never_below_zero():
    high = _simulate([_ov("add", 20)])["scenario"]  # 0,62 + 1,04 = 1,66
    assert (high["risk_score"], high["level"]) == (0.99, "red")
    assert _driver(high)["value"] == 29.1
    low = _simulate([_ov("add", -20)])["scenario"]  # 0,62 - 1,04 < 0
    assert (low["risk_score"], low["level"]) == (0.0, "amber")  # muscle sigue en amber
    assert (_driver(low)["value"], _driver(low)["deviation_z"], _driver(low)["contribution"], _domain(low)["score"]) == (-10.9, -16.1, 0.02, 0.0)


def test_scale_doubles_and_mixed_overrides_add_up():
    doubled = _simulate([_ov("scale", 2)])["scenario"]  # extra 9,1 -> 18,2 mm, z 10,8, riesgo 0,62 + 0,4732 = 1,0932 -> 0,99
    assert (_driver(doubled)["value"], _driver(doubled)["deviation_z"], doubled["risk_score"]) == (18.2, 10.8, 0.99)
    two = _simulate([_ov("add", 1), _ov("add", 1)])["scenario"]  # extra 2: 11,1 mm, z = round(4,6 / 1,083, 1) = 4,2, riesgo 0,724 -> 0,72
    assert (_driver(two)["value"], _driver(two)["deviation_z"], two["risk_score"], two["level"]) == (11.1, 4.2, 0.72, "red")
    skipped = _simulate([_ov("add", 2), _ov("add", 100, OTHER)])["scenario"]
    assert skipped == two


def test_project_days_does_not_change_the_demo_scenario():
    assert _simulate([_ov("add", 5)], project_days=30) == _simulate([_ov("add", 5)])
    assert _simulate([_ov("add", -2)], project_days=7)["scenario"]["trend"] == {"projected_level": "red", "days_to_threshold": 12.0}


def test_scenario_ids_are_deterministic_and_depend_on_the_stretch_and_the_clock():
    a = _simulate([_ov("add", 5)])["scenario"]["assessment_id"]
    assert a == _simulate([_ov("add", 5)])["scenario"]["assessment_id"]
    assert a != _simulate([_ov("add", 4)])["scenario"]["assessment_id"]
    assert a != _simulate([_ov("add", 5)], now=datetime(2026, 11, 6, 9, 30, tzinfo=UTC))["scenario"]["assessment_id"]
