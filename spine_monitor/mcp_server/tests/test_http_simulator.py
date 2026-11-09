"""HTTP simulator request, response and upstream error behavior."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from spine_contracts import SimulateRequest, SimulateResponse, dump
from spine_contracts.validation import errors
from spine_engine.api import create_default_app
from spine_mcp_server.errors import UpstreamError
from spine_mcp_server.http_client import HttpSimulator
from spine_mcp_server.mocks import EXAMPLES_DIR

BASE = "http://engine.test"


def example(name):
    return json.loads((EXAMPLES_DIR / f"engine/{name}.json").read_text(encoding="utf-8"))


def request():
    data = example("simulate-request")
    data.pop("project_days", None)
    return SimulateRequest.model_validate(data)


def simulator(handler):
    return HttpSimulator(BASE + "/", client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.contract
def test_posts_contract_request_without_unset_project_days_and_parses_response():
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append((req.method, str(req.url), body))
        assert not errors("engine/simulate-request", body)
        return httpx.Response(200, json=example("simulate-response"))

    result = simulator(handler).simulate(request())
    assert seen == [("POST", BASE + "/simulate", dump(request()))]
    assert "project_days" not in seen[0][2]
    assert result == SimulateResponse.model_validate(example("simulate-response"))
    assert not errors("engine/simulate-response", dump(result))


@pytest.mark.parametrize("status", [404, 422])
def test_engine_problem_is_preserved_and_detail_is_limited(status):
    response = httpx.Response(status, json={"type": "unknown_feature", "title": "Unknown feature", "status": status, "detail": "x" * 600})
    with pytest.raises(UpstreamError) as caught:
        simulator(lambda _: response).simulate(request())
    assert (caught.value.status, caught.value.title, caught.value.detail) == (status, "Unknown feature", "x" * 500)


def test_engine_validation_field_errors_are_preserved():
    response = httpx.Response(
        422, json={"type": "invalid_request", "title": "Invalid request", "status": 422, "errors": [{"pointer": "/base", "message": "bad"}]}
    )
    with pytest.raises(UpstreamError) as caught:
        simulator(lambda _: response).simulate(request())
    assert caught.value.detail == "/base: bad"


@pytest.mark.parametrize("response, expected", [
    (httpx.Response(500, text="secret"), (500, "Engine API error", "HTTP 500")),
    (httpx.Response(422, text="bad"), (422, "Engine API request rejected", "HTTP 422")),
    (httpx.Response(200, text="bad"), (502, "Engine API returned an invalid response", "Body is not a valid SimulateResponse.")),
])
def test_invalid_or_server_response_mapping(response, expected):
    with pytest.raises(UpstreamError) as caught:
        simulator(lambda _: response).simulate(request())
    assert (caught.value.status, caught.value.title, caught.value.detail) == expected


@pytest.mark.parametrize("exc, status, title", [(httpx.ReadTimeout, 504, "Engine API timeout"), (httpx.ConnectError, 503, "Engine API unavailable")])
def test_transport_errors(exc, status, title):
    def handler(req):
        raise exc("failed", request=req)

    with pytest.raises(UpstreamError) as caught:
        simulator(handler).simulate(request())
    assert (caught.value.status, caught.value.title) == (status, title)


@pytest.mark.integration
def test_against_real_engine_with_override_and_projection():
    payload = example("simulate-request")
    payload["project_days"] = 30
    with TestClient(create_default_app()) as client:
        result = HttpSimulator("http://testserver", client=client).simulate(SimulateRequest.model_validate(payload))
    assert result.hypothetical is True
    assert result.projection is not None
    assert result.projection.project_days == 30
    assert not errors("engine/simulate-response", dump(result))


@pytest.mark.integration
def test_against_real_engine_unknown_feature():
    payload = example("simulate-request")
    payload["overrides"] = [{"feature_id": "stretch.not.in_catalog", "mode": "set", "value": 1}]
    with TestClient(create_default_app()) as client, pytest.raises(UpstreamError) as caught:
        HttpSimulator("http://testserver", client=client).simulate(SimulateRequest.model_validate(payload))
    assert caught.value.status == 422
    assert caught.value.title == "Unknown feature"


def test_client_ownership_and_context_manager():
    with HttpSimulator(BASE, timeout=3.5) as owned:
        assert owned._client.timeout == httpx.Timeout(3.5)
    assert owned._client.is_closed
    client = httpx.Client()
    HttpSimulator(BASE, client=client).close()
    assert not client.is_closed
    client.close()
