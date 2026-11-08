"""HttpAppReader: the real HTTP client of the `AppReader` port, against MockTransport and against the real read app over ASGI."""

import json
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.client.client import Client
from mcp.types import TextContent
from monitor.db import Store
from monitor.readonly import create_read_app
from monitor.settings import AppSettings
from spine_contracts import SCHEMA_VERSION, Explanation, History, SelfReportList, SessionList, SimulateRequest, Status
from spine_mcp_server.errors import UpstreamError
from spine_mcp_server.http_client import HttpAppReader, UnavailableAssessBase, UnavailableSimulator
from spine_mcp_server.mocks import EXAMPLES_DIR, ExampleAppReader, ExampleAssessBaseSource
from spine_mcp_server.server import create_server

AST = "AST-01"
ASSESSMENT_ID = "a55e55e0-0000-4000-8000-0000000000a1"
NOW = datetime(2027, 2, 9, 10, 0, tzinfo=UTC)
FROM = datetime(2027, 2, 1, tzinfo=UTC)
TO = datetime(2027, 2, 10, tzinfo=UTC)
WIDE_FROM = datetime(2026, 1, 1, tzinfo=UTC)
BASE = "http://app.test"


def example(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def reader(handler) -> HttpAppReader:
    return HttpAppReader(BASE, client=httpx.Client(transport=httpx.MockTransport(handler)))


def problem(status: int, title: str = "Not found", detail: str | None = "unknown astronaut X", type_: str = "not_found") -> httpx.Response:
    body = {"type": type_, "title": title, "status": status}
    if detail is not None:
        body["detail"] = detail
    return httpx.Response(status, json=body, headers={"content-type": "application/problem+json"})


def raises(call) -> UpstreamError:
    with pytest.raises(UpstreamError) as info:
        call()
    return info.value


# --- construction and requests -------------------------------------------------------------------


def test_construction_does_no_network_and_owns_its_client_with_the_timeout():
    http = HttpAppReader("http://127.0.0.1:1", timeout=3.5)

    assert http._client.timeout == httpx.Timeout(3.5)
    assert str(http._client.base_url).rstrip("/") == "http://127.0.0.1:1"


def test_default_timeout_is_10_seconds():
    assert HttpAppReader("http://127.0.0.1:1")._client.timeout == httpx.Timeout(10.0)


def test_close_closes_the_client_it_owns():
    http = HttpAppReader("http://127.0.0.1:1")

    http.close()

    assert http._client.is_closed


def test_close_leaves_an_injected_client_open():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))

    HttpAppReader(BASE, client=client).close()

    assert not client.is_closed


def test_context_manager_closes_the_owned_client():
    with HttpAppReader("http://127.0.0.1:1") as http:
        assert not http._client.is_closed

    assert http._client.is_closed


def test_status_request_and_parse():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(200, json=example("app/status"))

    status = reader(handler).get_status(AST)

    assert seen == [("GET", f"/crew/{AST}/status", {})]
    assert status == Status.model_validate(example("app/status"))


UNSAFE_IDS = ["../x", "a?b=1", "a#b", "a/b", "AST-01\n", "a b", "%2e%2e"]


@pytest.mark.parametrize("unsafe", UNSAFE_IDS)
def test_astronaut_ids_are_escaped_as_a_single_path_segment(unsafe):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json=example("app/history"))

    reader(handler).get_history(unsafe, FROM, TO, 7)

    assert len(seen) == 1
    path, _, query = seen[0].partition("?")
    assert path == f"/crew/{quote(unsafe, safe='')}/history"
    assert httpx.QueryParams(query).keys() == {"from", "to", "limit"}


@pytest.mark.parametrize("unsafe", UNSAFE_IDS)
def test_assessment_ids_are_escaped_as_a_single_path_segment(unsafe):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json=example("app/explanation"))

    reader(handler).get_explanation(unsafe)

    assert seen == [f"/assessments/{quote(unsafe, safe='')}/explanation"]


@pytest.mark.parametrize("method", ["get_status", "get_sessions", "get_self_reports"])
def test_every_astronaut_endpoint_escapes_the_id(method):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(404, text="x")

    args = (FROM, TO) if method != "get_status" else ()
    raises(lambda: getattr(reader(handler), method)("a/b", *args))

    assert seen[0].startswith("/crew/a%2Fb/")


def test_base_url_trailing_slash_is_tolerated():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=example("app/status"))

    HttpAppReader(BASE + "/", client=httpx.Client(transport=httpx.MockTransport(handler))).get_status(AST)

    assert seen == [f"{BASE}/crew/{AST}/status"]


@pytest.mark.parametrize(
    ("method", "path", "fixture", "model"),
    [
        ("get_history", "history", "app/history", History),
        ("get_sessions", "sessions", "app/session-list", SessionList),
        ("get_self_reports", "self-reports", "app/self-report-list", SelfReportList),
    ],
)
def test_windowed_requests_use_from_to_limit_with_offset_aware_isoformat(method, path, fixture, model):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json=example(fixture))

    madrid = timezone(timedelta(hours=1))
    result = getattr(reader(handler), method)(AST, datetime(2027, 2, 1, 1, tzinfo=madrid), TO, 7)

    assert seen == [(f"/crew/{AST}/{path}", {"from": "2027-02-01T01:00:00+01:00", "to": "2027-02-10T00:00:00+00:00", "limit": "7"})]
    assert isinstance(result, model)


def test_limit_defaults_to_100():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["limit"])
        return httpx.Response(200, json=example("app/history"))

    reader(handler).get_history(AST, FROM, TO)

    assert seen == ["100"]


def test_explanation_request_and_parse():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=example("app/explanation"))

    explanation = reader(handler).get_explanation(ASSESSMENT_ID)

    assert seen == [f"/assessments/{ASSESSMENT_ID}/explanation"]
    assert explanation == Explanation.model_validate(example("app/explanation"))


# --- error mapping -------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [404, 422])
def test_problem_bodies_become_the_same_error_as_the_mocks(status):
    error = raises(lambda: reader(lambda _: problem(status, "Invalid range", "to is before from", "invalid_range")).get_status(AST))

    assert (error.status, error.title, error.detail) == (status, "Invalid range", "to is before from")


def _validation_problem(errors: list[dict[str, str]], detail: str | None = None) -> httpx.Response:
    body: dict = {"type": "invalid_request", "title": "Invalid request", "status": 422, "errors": errors}
    if detail is not None:
        body["detail"] = detail
    return httpx.Response(422, json=body, headers={"content-type": "application/problem+json"})


def test_field_errors_become_the_detail_when_the_problem_has_none():
    response = _validation_problem([{"pointer": "assessment_id", "message": "not a valid UUID"}, {"pointer": "limit", "message": "must be <= 500"}])

    error = raises(lambda: reader(lambda _: response).get_explanation("nope"))

    assert (error.status, error.title, error.detail) == (422, "Invalid request", "assessment_id: not a valid UUID; limit: must be <= 500")


def test_problem_detail_wins_over_field_errors():
    response = _validation_problem([{"pointer": "limit", "message": "bad"}], detail="the range is inverted")

    error = raises(lambda: reader(lambda _: response).get_status(AST))

    assert error.detail == "the range is inverted"


def test_empty_field_errors_keep_detail_none():
    error = raises(lambda: reader(lambda _: _validation_problem([])).get_status(AST))

    assert error.detail is None


def test_long_upstream_detail_is_truncated():
    error = raises(lambda: reader(lambda _: problem(404, "Not found", "x" * 2000)).get_status(AST))

    assert error.detail == "x" * 500


def test_detail_of_exactly_the_limit_is_kept():
    error = raises(lambda: reader(lambda _: problem(404, "Not found", "y" * 500)).get_status(AST))

    assert error.detail == "y" * 500


def test_long_field_errors_are_truncated_too():
    response = _validation_problem([{"pointer": "p", "message": "m" * 2000}])

    error = raises(lambda: reader(lambda _: response).get_status(AST))

    assert error.detail is not None
    assert len(error.detail) == 500


def test_problem_without_detail_keeps_detail_none():
    error = raises(lambda: reader(lambda _: problem(404, "Not found", None)).get_explanation(ASSESSMENT_ID))

    assert (error.status, error.title, error.detail) == (404, "Not found", None)


@pytest.mark.parametrize("status", [301, 304, 400, 403, 404, 409, 422])
def test_non_success_below_500_without_a_valid_problem_body_gets_a_synthetic_title(status):
    error = raises(lambda: reader(lambda _: httpx.Response(status, text="nope")).get_status(AST))

    assert (error.status, error.title, error.detail) == (status, "App API request rejected", f"HTTP {status}")


def test_4xx_with_json_that_is_not_a_problem_is_synthetic():
    error = raises(lambda: reader(lambda _: httpx.Response(404, json={"detail": "Not Found"})).get_status(AST))

    assert (error.status, error.title) == (404, "App API request rejected")


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_keeps_its_status_and_does_not_forward_the_upstream_detail(status):
    error = raises(lambda: reader(lambda _: problem(status, "Internal error", "boom", "internal")).get_status(AST))

    assert (error.status, error.title, error.detail) == (status, "App API error", f"HTTP {status}")


def test_5xx_without_a_problem_body_uses_the_http_status_as_detail():
    error = raises(lambda: reader(lambda _: httpx.Response(500, text="<html>")).get_status(AST))

    assert (error.status, error.title, error.detail) == (500, "App API error", "HTTP 500")


def test_invalid_json_is_a_502():
    error = raises(lambda: reader(lambda _: httpx.Response(200, text="not json")).get_status(AST))

    assert (error.status, error.title) == (502, "App API returned an invalid response")
    assert error.detail


def test_body_failing_model_validation_is_a_502():
    error = raises(lambda: reader(lambda _: httpx.Response(200, json={"astronaut_id": AST})).get_status(AST))

    assert (error.status, error.title) == (502, "App API returned an invalid response")
    assert error.detail


def test_timeout_is_a_504():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    error = raises(lambda: reader(handler).get_status(AST))

    assert (error.status, error.title) == (504, "App API timeout")
    assert error.detail


def test_connection_failures_are_a_503():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    error = raises(lambda: reader(handler).get_history(AST, FROM, TO))

    assert (error.status, error.title) == (503, "App API unavailable")
    assert error.detail


# --- against the real read app over ASGI ----------------------------------------------------------


def _populate(store: Store) -> None:
    """The contract examples wired together, as in backend/tests/test_read_api.py."""
    store.put_profile(example("domain/astronaut-profile"))
    store.put_daily_context(example("domain/daily-context"))
    preflight = example("domain/session.exercise")
    store.insert_session(preflight, "processed")
    store.put_feature_set(example("domain/feature-set.preflight"), phase="preflight", anchor_at=preflight["started_at"])
    store.put_baseline(example("domain/baseline"))
    inflight = example("domain/session.exercise") | {
        "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a03",
        "started_at": "2027-02-09T08:30:00Z",
        "ended_at": "2027-02-09T08:50:00Z",
        "mission_context": {"mission_day": 87, "phase": "inflight", "gravity_environment": "microgravity"},
    }
    store.insert_session(inflight, "processed")
    store.put_feature_set(example("domain/feature-set.inflight"), phase="inflight", anchor_at="2027-02-09T08:30:00Z")
    for name in ("self-report.weekly-odi", "self-report.daily"):
        store.put_self_report(example(f"domain/{name}"))
    store.put_assessment(example("domain/assessment.amber"))
    store.put_device_status(example("telemetry/device-status.degraded") | {"timestamp": "2027-02-09T09:57:00Z"})


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "app.db")
    yield store
    store.close()


@pytest.fixture
def app_client(store):
    return TestClient(create_read_app(AppSettings(_env_file=None), store=store, clock=lambda: NOW))


@pytest.fixture
def real(store, app_client) -> HttpAppReader:
    _populate(store)
    return HttpAppReader("http://testserver", client=app_client)


@pytest.fixture
def mock() -> ExampleAppReader:
    return ExampleAppReader()


@pytest.mark.integration
def test_status_against_the_real_app(real):
    status = real.get_status(AST)

    assert isinstance(status, Status)
    assert status.astronaut_id == AST
    assert status.latest_assessment is not None
    assert status.latest_assessment.assessment_id == ASSESSMENT_ID


@pytest.mark.integration
def test_history_against_the_real_app_matches_the_mock_assessments(real, mock):
    history = real.get_history(AST, FROM, TO)

    assert isinstance(history, History)
    assert (history.from_, history.to, history.limit, history.truncated) == (FROM, TO, 100, False)
    # Parity with the mock on assessments. Feature sets are NOT compared: the mock filters them by `computed_at`,
    # the API by the start of their session, so the two legitimately disagree on some of them.
    assert history.assessments == mock.get_history(AST, FROM, TO).assessments


@pytest.mark.integration
def test_sessions_against_the_real_app(real):
    sessions = real.get_sessions(AST, WIDE_FROM, TO)

    assert isinstance(sessions, SessionList)
    assert [s.started_at for s in sessions.items] == sorted(s.started_at for s in sessions.items)
    assert len(sessions.items) == 2
    assert real.get_sessions(AST, WIDE_FROM, TO, 1).truncated is True


@pytest.mark.integration
def test_self_reports_against_the_real_app_match_the_mock(real, mock):
    reports = real.get_self_reports(AST, FROM, TO)

    assert isinstance(reports, SelfReportList)
    assert {r.report_id for r in mock.get_self_reports(AST, FROM, TO).items} <= {r.report_id for r in reports.items}


@pytest.mark.integration
def test_explanation_against_the_real_app_matches_the_mock(real, mock):
    explanation = real.get_explanation(ASSESSMENT_ID)

    assert isinstance(explanation, Explanation)
    assert explanation.assessment == mock.get_explanation(ASSESSMENT_ID).assessment


@pytest.mark.integration
def test_unknown_ids_are_404_like_the_mock(real, mock):
    # The real app and the mock agree on the status; their titles differ ("Not found" vs "Astronaut not found").
    for call in (lambda r: r.get_status("AST-99"), lambda r: r.get_explanation("a55e55e0-0000-4000-8000-0000000000ff")):
        assert raises(lambda call=call: call(real)).status == raises(lambda call=call: call(mock)).status == 404


@pytest.mark.integration
def test_inverted_range_is_422_like_the_mock(real, mock):
    error = raises(lambda: real.get_history(AST, TO, FROM))

    assert error.status == raises(lambda: mock.get_history(AST, TO, FROM)).status == 422
    assert error.title == "Invalid range"


@pytest.mark.integration
def test_out_of_range_limit_is_422(real):
    error = raises(lambda: real.get_sessions(AST, FROM, TO, 0))

    assert error.status == 422


def _simulate_request():
    return SimulateRequest(schema_version=SCHEMA_VERSION, base=ExampleAssessBaseSource().get_base(AST), overrides=[], project_days=None)


def test_unavailable_simulator_raises_501_explaining_the_missing_endpoint():
    with pytest.raises(UpstreamError) as caught:
        UnavailableSimulator().simulate(_simulate_request())
    assert caught.value.status == 501
    assert caught.value.title == "Simulation not available"
    assert "no endpoint for this yet" in str(caught.value)
    assert "http mode" in str(caught.value)
    assert "#" not in str(caught.value)


def test_unavailable_assess_base_raises_501_explaining_the_missing_endpoint():
    with pytest.raises(UpstreamError) as caught:
        UnavailableAssessBase().get_base(AST)
    assert caught.value.status == 501
    assert caught.value.title == "Assessment base not available"
    assert "no endpoint for this yet" in str(caught.value)
    assert "http mode" in str(caught.value)
    assert "#" not in str(caught.value)


@pytest.mark.integration
@pytest.mark.anyio
async def test_simulate_scenario_through_the_server_reports_the_unavailable_ports_as_a_tool_error():
    server = create_server(ExampleAppReader(), UnavailableSimulator(), UnavailableAssessBase())
    async with Client(server) as client:
        result = await client.call_tool("simulate_scenario", {"astronaut_id": AST, "overrides": []})
    assert result.is_error
    assert isinstance(result.content[0], TextContent)
    text = result.content[0].text
    assert "Assessment base not available: The app API has no endpoint for this yet" in text
    assert text.endswith("the assessment base is not available in http mode.")
    assert "http mode" in result.content[0].text
