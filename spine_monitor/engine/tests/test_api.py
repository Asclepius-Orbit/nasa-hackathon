import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from spine_contracts import AssessRequest, openapi_drift, validation
from spine_engine.api import create_app, create_default_app
from spine_engine.factory import default_baseline_builder
from spine_engine.pipeline import AssessmentPipeline
from spine_engine.stubs import NullBaselineStrategy, NullFeatureExtractor, NullRiskModel, NullTriagePolicy, stub_pipeline

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
EXAMPLES = CONTRACTS / "examples" / "valid" / "engine"


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_default_app())


def assert_problem(response, status: int, type_: str) -> dict:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert validation.errors("engine/error", body) == []
    assert body["type"] == type_
    return body


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_stretch_estimate_uses_measured_trials_and_personal_baseline(client):
    response = client.post("/stretch-estimate", json={
        "schema_version": "1.0.0", "baseline_resistance_ohm": 4500,
        "current_resistance_ohm": 5650, "reference_length_mm": 500,
    })
    assert response.status_code == 200
    body = response.json()
    assert validation.errors("engine/stretch-estimate-response", body) == []
    assert body["model"] == "experimental_uniform_resistance_v1"
    assert body["estimated_length_mm"] == pytest.approx(538.5723, abs=0.01)
    assert body["elongation_mm"] == pytest.approx(38.5723, abs=0.01)


@pytest.mark.parametrize("field", ["baseline_resistance_ohm", "current_resistance_ohm"])
def test_stretch_estimate_rejects_out_of_range_without_extrapolating(client, field):
    body = {"schema_version": "1.0.0", "baseline_resistance_ohm": 4500,
            "current_resistance_ohm": 5650, "reference_length_mm": 500}
    body[field] = 10000
    response = client.post("/stretch-estimate", json=body)
    assert_problem(response, 422, "invalid_request")


def test_stretch_estimate_rejects_unrepresentable_scaling(client):
    body = {"schema_version": "1.0.0", "baseline_resistance_ohm": 5e-324,
            "current_resistance_ohm": 5e-324, "reference_length_mm": 5e-324}
    assert_problem(client.post("/stretch-estimate", json=body), 422, "invalid_request")


def test_features_extracts_stretch_and_computes_exercise_for_the_two_rep_example(client):
    request = example("features-request")

    response = client.post("/features", json=request)

    assert response.status_code == 200
    body = response.json()
    assert validation.errors("domain/feature-set", body) == []
    assert {f["feature_id"] for f in body["features"] if f["feature_id"].startswith("stretch.")} == {
        "stretch.full_spine.elongation_mm",
        "stretch.full_spine.elongation_pct",
        "stretch.full_spine.rest_resistance_ohm",
    }
    assert {s["reason"] for s in body["skipped"] if s["feature_id"].startswith("exercise.")} == set()
    assert body["engine_version"] == "0.1.0"  # lo rellena el extractor compuesto, no el nulo
    assert "window" in body
    assert body["session_id"] == request["session"]["session_id"]
    assert body["protocol_id"] == request["session"]["protocol_id"]
    assert body["gravity_environment"] == request["session"]["mission_context"]["gravity_environment"]


def test_baseline_is_built_from_the_feature_sets(client):
    response = client.post("/baseline", json=example("baseline-request"))

    assert response.status_code == 200
    body = response.json()
    assert validation.errors("domain/baseline", body) == []
    assert body["version"] == 1
    assert {e["feature_id"] for e in body["entries"]} >= {"imu.t9.sagittal.rom_deg"}


def test_baseline_entries_omit_protocol_id_when_the_feature_sets_have_none(client):
    request = example("baseline-request")
    for feature_set in request["feature_sets"]:
        del feature_set["protocol_id"]

    response = client.post("/baseline", json=request)

    assert response.status_code == 200
    body = response.json()
    assert all("protocol_id" not in entry for entry in body["entries"])
    assert validation.errors("domain/baseline", body) == []


def test_assess_returns_a_valid_assessment(client):
    response = client.post("/assess", json=example("assess-request"))

    assert response.status_code == 200
    body = response.json()
    assert validation.errors("domain/assessment", body) == []
    assert body["astronaut_id"] == "AST-01"
    assert body["completeness"]["score"] == pytest.approx(4 / 9)


def test_assess_without_mission_day_omits_it_instead_of_sending_null(client):
    request = example("assess-request")
    del request["mission_day"]

    response = client.post("/assess", json=request)

    assert response.status_code == 200
    body = response.json()
    assert "mission_day" not in body
    assert validation.errors("domain/assessment", body) == []


def test_simulate_returns_a_hypothetical_pair(client):
    response = client.post("/simulate", json=example("simulate-request"))

    assert response.status_code == 200
    body = response.json()
    assert validation.errors("engine/simulate-response", body) == []
    assert body["hypothetical"] is True


def test_simulate_without_overrides_projects_the_base_unchanged(client):
    response = client.post("/simulate", json=example("simulate-request.no-overrides"))

    assert response.status_code == 200
    body = response.json()
    assert validation.errors("engine/simulate-response", body) == []
    assert body["hypothetical"] is True
    assert body["scenario"] == body["reference"]


class RecordingPipeline(AssessmentPipeline):
    """Pipeline de los stubs que además guarda lo que se le pide evaluar."""

    def __init__(self) -> None:
        super().__init__(NullBaselineStrategy(), NullRiskModel(), NullTriagePolicy())
        self.seen: list[AssessRequest] = []

    def evaluate(self, request):
        self.seen.append(request.model_copy(deep=True))
        return super().evaluate(request)


def test_simulate_reevaluates_the_base_with_the_overrides_applied():
    pipeline = RecordingPipeline()
    app = create_app(pipeline=pipeline, baseline_builder=default_baseline_builder(), feature_extractor=NullFeatureExtractor())
    request = example("simulate-request")

    response = TestClient(app).post("/simulate", json=request)

    assert response.status_code == 200
    assert response.json()["hypothetical"] is True
    reference, scenario = pipeline.seen
    feature_id = "stretch.full_spine.peak_elongation_mm"
    value = lambda req: next(f.value for f in req.feature_sets[-1].features if f.feature_id == feature_id)  # noqa: E731
    assert value(scenario) == pytest.approx(value(reference) + 5)


def test_simulate_without_overrides_gives_a_copy_of_the_reference(client):
    body = client.post("/simulate", json=example("simulate-request.no-overrides")).json()

    assert body["scenario"] == body["reference"]
    assert "projection" not in body


def test_simulate_reports_projection_and_reevaluates_projected_values():
    pipeline = RecordingPipeline()
    app = create_app(pipeline=pipeline, baseline_builder=default_baseline_builder(), feature_extractor=NullFeatureExtractor())
    request = example("simulate-request")
    request["project_days"] = 1
    latest = request["base"]["feature_sets"][0]
    older = deepcopy(latest)
    older["computed_at"] = (datetime.fromisoformat(latest["computed_at"].replace("Z", "+00:00")) - timedelta(days=1)).isoformat()
    for feature in older["features"]:
        feature["value"] = 1
    for feature in latest["features"]:
        feature["value"] = 3
    request["base"]["feature_sets"] = [latest, older]

    response = TestClient(app).post("/simulate", json=request)

    assert response.status_code == 200
    body = response.json()
    assert validation.errors("engine/simulate-response", body) == []
    assert body["hypothetical"] is True
    assert body["projection"] == {"project_days": 1, "unprojected_features": []}
    peak = "stretch.full_spine.peak_elongation_mm"
    assert next(f.value for f in pipeline.seen[1].feature_sets[0].features if f.feature_id == peak) == pytest.approx(15)


def test_simulate_rejects_projection_beyond_history(client):
    request = example("simulate-request")
    latest = request["base"]["feature_sets"][0]
    older = deepcopy(latest)
    older["computed_at"] = (datetime.fromisoformat(latest["computed_at"].replace("Z", "+00:00")) - timedelta(days=1)).isoformat()
    request["base"]["feature_sets"].append(older)
    request["project_days"] = 2

    body = assert_problem(client.post("/simulate", json=request), 422, "invalid_request")
    assert "detail" in body


def test_simulate_unknown_feature_is_a_problem(client):
    request = example("simulate-request")
    request["overrides"] = [{"feature_id": "stretch.not.in_catalog", "mode": "set", "value": 1}]

    assert_problem(client.post("/simulate", json=request), 422, "unknown_feature")


def test_simulate_feature_without_value_in_the_base_is_a_problem(client):
    request = example("simulate-request")
    request["overrides"] = [{"feature_id": "emg.latissimus_dorsi.left.rms_pct_mvc", "mode": "set", "value": 1}]
    for fs in request["base"]["feature_sets"]:
        fs["features"] = [f for f in fs["features"] if f["feature_id"] != "emg.latissimus_dorsi.left.rms_pct_mvc"]

    assert_problem(client.post("/simulate", json=request), 422, "unknown_feature")


def test_simulate_non_finite_override_is_a_problem(client):
    request = example("simulate-request")
    request["overrides"] = [{"feature_id": "stretch.full_spine.peak_elongation_mm", "mode": "scale", "value": 1e308}] * 2

    assert_problem(client.post("/simulate", json=request), 422, "invalid_request")


def test_assess_and_baseline_chain_without_the_caller_touching_the_data(client):
    baseline = client.post("/baseline", json=example("baseline-request")).json()
    request = example("assess-request") | {"baseline": baseline}

    assert client.post("/assess", json=request).status_code == 200


def test_invalid_json_is_a_problem(client):
    response = client.post("/assess", content=b"{not json", headers={"content-type": "application/json"})

    assert_problem(response, 422, "invalid_request")


def test_schema_violations_list_each_pointer(client):
    request = example("assess-request")
    del request["baseline"]
    request["feature_sets"] = []

    body = assert_problem(client.post("/assess", json=request), 422, "invalid_request")

    pointers = {e["pointer"] for e in body["errors"]}
    assert "/feature_sets" in pointers
    assert body["errors"] and all(e["message"] for e in body["errors"])


def test_conditional_schema_rules_are_enforced(client):
    # An exercise session must declare its load; Pydantic alone would let this through.
    request = example("features-request")
    assert request["session"]["session_type"] == "exercise"
    del request["session"]["load_declared"]

    body = assert_problem(client.post("/features", json=request), 422, "invalid_request")

    assert any(e["pointer"] == "/session" for e in body["errors"])


def test_unsupported_major_version_is_reported_as_such(client):
    request = example("assess-request") | {"schema_version": "2.0.0"}

    body = assert_problem(client.post("/assess", json=request), 422, "unsupported_schema_version")

    assert body["errors"][0]["pointer"] == "/schema_version"


def test_a_baseline_without_usable_sessions_is_insufficient_data(client):
    request = example("baseline-request")
    for feature_set in request["feature_sets"]:
        del feature_set["gravity_environment"]

    assert_problem(client.post("/baseline", json=request), 422, "insufficient_data")


def test_unexpected_failures_are_internal_problems_without_leaking_details():
    class Exploding:
        def evaluate(self, request):
            raise RuntimeError("secret internals")

    app = create_app(pipeline=Exploding(), baseline_builder=default_baseline_builder(), feature_extractor=NullFeatureExtractor())

    response = TestClient(app, raise_server_exceptions=False).post("/assess", json=example("assess-request"))

    body = assert_problem(response, 500, "internal")
    assert "secret" not in json.dumps(body)


def test_backends_are_injected():
    class FixedExtractor:
        def extract(self, request):
            return NullFeatureExtractor().extract(request).model_copy(update={"engine_version": "fake"})

    app = create_app(pipeline=stub_pipeline(), baseline_builder=default_baseline_builder(), feature_extractor=FixedExtractor())

    response = TestClient(app).post("/features", json=example("features-request"))

    assert response.json()["engine_version"] == "fake"


def test_routes_match_the_openapi_contract():
    contract = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))

    assert openapi_drift.diff(contract, create_default_app().openapi()) == []


def test_app_metadata_matches_the_contract_info():
    info = create_default_app().openapi()["info"]
    contract = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))["info"]

    assert info["title"] == contract["title"] == "Spine engine API"
    assert info["version"] == contract["version"] == "1.0.0"
    assert info["description"] == "Local HTTP API of the stateless prediction engine. Contract: contracts/openapi.yaml."


def test_simulate_problems_carry_title_and_detail(client):
    request = example("simulate-request")
    request["overrides"] = [{"feature_id": "stretch.not.in_catalog", "mode": "set", "value": 1}]
    unknown = assert_problem(client.post("/simulate", json=request), 422, "unknown_feature")

    request["overrides"] = [{"feature_id": "stretch.full_spine.peak_elongation_mm", "mode": "scale", "value": 1e308}] * 2
    invalid = assert_problem(client.post("/simulate", json=request), 422, "invalid_request")

    assert (unknown["title"], unknown["status"]) == ("Unknown feature", 422)
    assert unknown["detail"] == "The feature 'stretch.not.in_catalog' is not in the catalog."
    assert (invalid["title"], invalid["status"]) == ("Invalid request", 422)
    assert invalid["detail"] == "The override on 'stretch.full_spine.peak_elongation_mm' does not give a finite value."
