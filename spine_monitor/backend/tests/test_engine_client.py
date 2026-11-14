"""EngineClient: contract checks on both sides of every call, and how engine failures surface."""

import json
from pathlib import Path

import httpx
import pytest
from monitor.engine import EngineClient, EngineError

pytestmark = pytest.mark.anyio

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def client(handler) -> EngineClient:
    return EngineClient(httpx.AsyncClient(base_url="http://engine", transport=httpx.MockTransport(handler)))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_valid_round_trip_posts_to_the_endpoint():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=example("domain/baseline"))

    body = example("engine/baseline-request")

    assert await client(handler).baseline(body) == example("domain/baseline")
    assert seen == [("/baseline", body)]


async def test_features_and_assess_use_their_endpoints():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        answer = {"/features": "domain/feature-set.preflight", "/assess": "domain/assessment.amber"}[request.url.path]
        return httpx.Response(200, json=example(answer))

    engine = client(handler)
    assert (await engine.features(example("engine/features-request")))["session_id"] is not None
    assert (await engine.assess(example("engine/assess-request")))["level"] == "amber"
    assert paths == ["/features", "/assess"]


async def test_invalid_request_is_never_sent():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    with pytest.raises(EngineError, match="request violates engine/baseline-request"):
        await client(handler).baseline({"schema_version": "1.0.0"})


async def test_unreachable_engine():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(EngineError, match="engine unreachable"):
        await client(handler).baseline(example("engine/baseline-request"))


async def test_non_json_answer():
    with pytest.raises(EngineError, match="non-JSON") as caught:
        await client(lambda r: httpx.Response(502, text="bad gateway")).baseline(example("engine/baseline-request"))
    assert caught.value.status == 502


async def test_problem_details_are_kept():
    problem = {"type": "insufficient_data", "title": "Insufficient data", "status": 422, "detail": "no sessions"}

    with pytest.raises(EngineError, match="HTTP 422: no sessions") as caught:
        await client(lambda r: httpx.Response(422, json=problem)).baseline(example("engine/baseline-request"))
    assert (caught.value.status, caught.value.problem) == (422, problem)


async def test_error_without_problem_body():
    with pytest.raises(EngineError, match="HTTP 500: error") as caught:
        await client(lambda r: httpx.Response(500, json=["?"])).baseline(example("engine/baseline-request"))
    assert caught.value.problem is None


async def test_error_title_is_used_without_detail():
    problem = {"type": "internal", "title": "Internal error", "status": 500}

    with pytest.raises(EngineError, match="HTTP 500: Internal error"):
        await client(lambda r: httpx.Response(500, json=problem)).baseline(example("engine/baseline-request"))


async def test_answer_that_breaks_the_contract():
    with pytest.raises(EngineError, match="response violates domain/baseline"):
        await client(lambda r: httpx.Response(200, json={"version": 1})).baseline(example("engine/baseline-request"))
