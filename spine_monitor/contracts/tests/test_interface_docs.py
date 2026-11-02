from pathlib import Path

import pytest
import yaml
from spine_contracts import openapi_drift, topics

CONTRACTS_DIR = Path(__file__).resolve().parents[1]
DOCS = ["asyncapi.yaml", "openapi.yaml", "app-openapi.yaml"]


def load(name: str):
    return yaml.safe_load((CONTRACTS_DIR / name).read_text(encoding="utf-8"))


def iter_refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_refs(item)


@pytest.mark.parametrize("name", DOCS)
def test_external_refs_point_to_existing_schemas(name):
    external = [ref for ref in iter_refs(load(name)) if not ref.startswith("#")]

    assert external, "the document should reference the JSON Schemas"
    for ref in external:
        assert (CONTRACTS_DIR / ref.split("#")[0]).is_file(), f"{name}: {ref} does not exist"


@pytest.mark.parametrize("name", DOCS)
def test_internal_refs_resolve(name):
    document = load(name)
    for ref in (r for r in iter_refs(document) if r.startswith("#/")):
        node = document
        for part in ref[2:].split("/"):
            assert part in node, f"{name}: broken {ref}"
            node = node[part]


def test_asyncapi_addresses_match_the_topic_helpers():
    channels = load("asyncapi.yaml")["channels"]
    ids = {"astronaut_id": "AST-01", "device_id": "wb-01", "stream_id": "emg_lat_left"}

    assert channels["stream"]["address"].format(**ids) == topics.stream("AST-01", "wb-01", "emg_lat_left")
    assert channels["status"]["address"].format(**ids) == topics.status("AST-01", "wb-01")
    assert channels["cmd"]["address"].format(**ids) == topics.cmd("AST-01", "wb-01")
    assert channels["ack"]["address"].format(**ids) == topics.ack("AST-01", "wb-01")
    assert channels["clock"]["address"].format(**ids) == topics.clock("AST-01", "wb-01")
    assert channels["calibration"]["address"].format(**ids) == topics.calibration("AST-01", "wb-01", "emg_lat_left")


def test_asyncapi_calibration_is_retained_qos1_with_the_calibration_schema():
    document = load("asyncapi.yaml")
    operation = document["operations"]["sendCalibration"]
    message = document["components"]["messages"]["calibration"]

    assert operation["channel"] == {"$ref": "#/channels/calibration"}
    assert operation["bindings"]["mqtt"]["qos"] == 1
    assert operation["bindings"]["mqtt"]["retain"] is True
    assert message["payload"] == {"$ref": "schemas/v1/domain/calibration.schema.json"}


def test_openapi_paths_are_the_engine_endpoints():
    assert set(load("openapi.yaml")["paths"]) == {"/health", "/features", "/baseline", "/assess", "/simulate"}


APP_PATHS = {
    "/health",
    "/crew/{astronaut_id}/status",
    "/crew/{astronaut_id}/history",
    "/assessments/{assessment_id}/explanation",
    "/crew/{astronaut_id}/assessments",
    "/assessments/{assessment_id}",
    "/crew/{astronaut_id}/sessions",
    "/sessions/{session_id}",
    "/sessions/{session_id}/feature-set",
    "/crew/{astronaut_id}/self-reports",
    "/self-reports/{report_id}",
    "/crew/{astronaut_id}/baselines/{baseline_id}",
}


def test_app_openapi_paths_are_the_read_endpoints():
    assert set(load("app-openapi.yaml")["paths"]) == APP_PATHS


def test_app_openapi_is_read_only():
    methods = {method for item in load("app-openapi.yaml")["paths"].values() for method in item}
    assert methods == {"get"}


def test_app_openapi_operation_ids_are_unique():
    ids = [op["operationId"] for item in load("app-openapi.yaml")["paths"].values() for op in item.values()]
    assert len(ids) == len(set(ids))


def test_app_openapi_path_parameters_are_declared():
    document = load("app-openapi.yaml")
    for path, item in document["paths"].items():
        declared = {
            document["components"]["parameters"][p["$ref"].rsplit("/", 1)[1]]["name"]
            for p in item["get"].get("parameters", [])
            if p["$ref"].startswith("#/components/parameters/")
        }
        in_path = {part[1:-1] for part in path.split("/") if part.startswith("{")}
        assert in_path <= declared, f"{path}: undeclared path parameters {in_path - declared}"


def test_app_errors_are_problem_details():
    for name, response in load("app-openapi.yaml")["components"]["responses"].items():
        assert list(response["content"]) == ["application/problem+json"], name


def test_openapi_drift_is_empty_for_the_same_document():
    contract = load("app-openapi.yaml")
    assert openapi_drift.diff(contract, contract) == []


def test_openapi_drift_reports_missing_unexpected_and_renamed_operations():
    contract = load("app-openapi.yaml")
    generated = {
        "paths": {
            "/crew/{astronaut_id}/status": {"get": {"operationId": "status"}},
            "/crew/{astronaut_id}/history": {"get": {"operationId": "getHistory"}},
            "/crew/{astronaut_id}/reset": {"post": {"operationId": "reset"}},
        }
    }

    problems = openapi_drift.diff(contract, generated)

    assert "unexpected: POST /crew/{astronaut_id}/reset" in problems
    assert "missing: GET /sessions/{session_id}" in problems
    assert "operationId of GET /crew/{astronaut_id}/status: expected 'getStatus', got 'status'" in problems
    assert not any("getHistory" in problem for problem in problems)
