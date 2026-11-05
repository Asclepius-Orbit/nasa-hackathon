import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from spine_contracts import openapi_drift, validation
from spine_engine.api import create_app, create_default_app
from spine_engine.factory import default_baseline_builder
from spine_engine.stubs import NullFeatureExtractor, stub_pipeline

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


def test_features_returns_an_honest_empty_feature_set(client):
    request = example("features-request")

    response = client.post("/features", json=request)

    assert response.status_code == 200
    body = response.json()
    assert validation.errors("domain/feature-set", body) == []
    assert body["features"] == []
    assert body["session_id"] == request["session"]["session_id"]
    assert body["protocol_id"] == request["session"]["protocol_id"]
    assert body["gravity_environment"] == request["session"]["mission_context"]["gravity_environment"]


def test_baseline_is_built_from_the_feature_sets(client):
    response = client.post("/baseline", json=example("baseline-request"))

    assert response.status_code == 200
    body = response.json()
    assert validation.errors("domain/baseline", body) == []
    assert body["version"] == 1
    assert {e["feature_id"] for e in body["entries"]} >= {"imu.l3.sagittal.rom_deg"}


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
    assert body["completeness"]["score"] == 0.8


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
