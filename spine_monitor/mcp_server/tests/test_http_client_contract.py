"""HttpAppReader against `contracts/app-openapi.yaml`: what the client sends and what it relies on must be in the contract.

Every request is recorded (method, path, query, status, body) from the real read app and checked against the parsed
contract, so a renamed path, an undeclared query parameter or a new port method fails here instead of in production.
"""

import inspect
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from monitor.db import Store
from monitor.readonly import create_read_app
from monitor.settings import AppSettings
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from spine_contracts import validation
from spine_mcp_server.errors import UpstreamError
from spine_mcp_server.http_client import HttpAppReader
from spine_mcp_server.ports import AppReader

pytestmark = pytest.mark.contract

CONTRACT = yaml.safe_load((Path(__file__).resolve().parents[2] / "contracts" / "app-openapi.yaml").read_text(encoding="utf-8"))
SCHEMA_BASE = "https://schemas.asclepius-orbit.example/v1/"
AST = "AST-01"
ASSESSMENT_ID = "a55e55e0-0000-4000-8000-0000000000a1"
NOW = datetime(2027, 2, 9, 10, 0, tzinfo=UTC)
FROM = datetime(2027, 2, 1, tzinfo=UTC)
TO = datetime(2027, 2, 10, tzinfo=UTC)

# Port method -> contract operationId. A new port method is not in this map and fails `test_every_port_method_is_mapped`.
OPERATIONS = {
    "get_status": "getStatus",
    "get_history": "getHistory",
    "get_sessions": "listSessions",
    "get_self_reports": "listSelfReports",
    "get_explanation": "getAssessmentExplanation",
}

# Port method -> how to call it on a reader.
CALLS = {
    "get_status": lambda r, **kw: r.get_status(AST),
    "get_history": lambda r, **kw: r.get_history(AST, kw.get("from_", FROM), kw.get("to", TO), **({"limit": kw["limit"]} if "limit" in kw else {})),
    "get_sessions": lambda r, **kw: r.get_sessions(AST, kw.get("from_", FROM), kw.get("to", TO), **({"limit": kw["limit"]} if "limit" in kw else {})),
    "get_self_reports": lambda r, **kw: r.get_self_reports(AST, kw.get("from_", FROM), kw.get("to", TO), **({"limit": kw["limit"]} if "limit" in kw else {})),
    "get_explanation": lambda r, **kw: r.get_explanation(ASSESSMENT_ID),
}


def example(name: str) -> dict:
    from spine_mcp_server.mocks import EXAMPLES_DIR

    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


# --- contract helpers ----------------------------------------------------------------------------


def _resolve(node: dict) -> dict:
    """Follow a local `#/components/...` $ref once; other refs are left for the schema registry."""
    ref = node.get("$ref", "")
    if not ref.startswith("#/"):
        return node
    target = CONTRACT
    for part in ref[2:].split("/"):
        target = target[part]
    return target


def operation(operation_id: str) -> tuple[str, dict]:
    for template, item in CONTRACT["paths"].items():
        for method, op in item.items():
            if method == "get" and op["operationId"] == operation_id:
                return template, op
    raise AssertionError(f"operationId {operation_id} is not a GET operation in app-openapi.yaml")


def parameters(op: dict, where: str) -> dict[str, dict]:
    resolved = [_resolve(p) for p in op.get("parameters", [])]
    return {p["name"]: p for p in resolved if p["in"] == where}


def declared_statuses(op: dict) -> set[str]:
    return set(op["responses"])


def response_schema_name(op: dict) -> str:
    """'schemas/v1/app/status.schema.json' -> 'app/status', as `spine_contracts.validation` names it."""
    ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    return re.fullmatch(r"schemas/v1/(.+)\.schema\.json", ref).group(1)  # pyright: ignore[reportOptionalMemberAccess]


def path_regex(template: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", re.escape(template)) + "$")


@pytest.fixture(scope="module")
def registry() -> Registry:
    reg = Registry()
    for path in sorted(validation.SCHEMAS_DIR.rglob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        reg = reg.with_resource(schema["$id"], Resource.from_contents(schema, DRAFT202012))
    return reg


def param_errors(registry: Registry, param: dict, value: object) -> list[str]:
    """Violations of the parameter's schema (resolving `schemas/v1/...` refs to their `$id`)."""
    schema = dict(param["schema"])
    if "$ref" in schema:
        schema["$ref"] = schema["$ref"].replace("schemas/v1/", SCHEMA_BASE)
    return [e.message for e in Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).iter_errors(value)]


# --- recording against the real app --------------------------------------------------------------


@dataclass
class Exchange:
    method: str
    path: str
    query: dict[str, str]
    status: int
    content_type: str
    body: object


@dataclass
class Recorder:
    exchanges: list[Exchange] = field(default_factory=list)

    def __call__(self, response: httpx.Response) -> None:
        response.read()
        request = response.request
        self.exchanges.append(
            Exchange(
                request.method, request.url.path, dict(request.url.params), response.status_code, response.headers.get("content-type", ""), response.json()
            )
        )

    @property
    def last(self) -> Exchange:
        return self.exchanges[-1]


def _populate(store: Store) -> None:
    """Same data as test_http_client._populate (the contract examples wired together)."""
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
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def real(tmp_path, recorder):
    store = Store(tmp_path / "app.db")
    _populate(store)
    client = TestClient(create_read_app(AppSettings(_env_file=None), store=store, clock=lambda: NOW))
    client.event_hooks["response"].append(recorder)
    yield HttpAppReader("http://testserver", client=client)
    store.close()


# --- the port maps to the contract ---------------------------------------------------------------


def test_every_port_method_is_mapped_to_a_contract_operation():
    port_methods = {name for name, _ in inspect.getmembers(AppReader, inspect.isfunction) if not name.startswith("_")}

    assert port_methods == set(OPERATIONS) == set(CALLS)
    for operation_id in OPERATIONS.values():
        assert operation(operation_id)[1]["operationId"] == operation_id


def test_the_client_implements_every_port_method():
    assert {n for n in OPERATIONS} <= {n for n, _ in inspect.getmembers(HttpAppReader, inspect.isfunction)}


# --- requests ------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", list(OPERATIONS))
def test_request_uses_a_declared_get_path_and_declared_parameters(real, recorder, registry, method):
    template, op = operation(OPERATIONS[method])

    CALLS[method](real)

    sent = recorder.last
    assert sent.method == "GET"
    match = path_regex(template).match(sent.path)
    assert match, f"{sent.path} does not match contract path {template}"
    path_params = parameters(op, "path")
    assert set(match.groupdict()) == set(path_params)
    for name, value in match.groupdict().items():
        assert param_errors(registry, path_params[name], value) == [], name
    query_params = parameters(op, "query")
    assert set(sent.query) <= set(query_params)
    assert {n for n, p in query_params.items() if p.get("required")} <= set(sent.query)
    for name, value in sent.query.items():
        schema = query_params[name]["schema"]
        typed = int(value) if schema.get("type") == "integer" else value
        assert param_errors(registry, query_params[name], typed) == [], name


@pytest.mark.parametrize("method", ["get_history", "get_sessions", "get_self_reports"])
def test_windowed_operations_declare_from_to_limit_and_the_client_sends_them(real, recorder, method):
    _, op = operation(OPERATIONS[method])

    CALLS[method](real)

    assert set(parameters(op, "query")) == {"from", "to", "limit"}
    assert set(recorder.last.query) == {"from", "to", "limit"}


@pytest.mark.parametrize("method", ["get_history", "get_sessions", "get_self_reports"])
def test_default_limit_is_the_contract_default(real, recorder, method):
    _, op = operation(OPERATIONS[method])

    CALLS[method](real)

    assert recorder.last.query["limit"] == str(parameters(op, "query")["limit"]["schema"]["default"])


@pytest.mark.parametrize("method", ["get_history", "get_sessions", "get_self_reports"])
def test_limit_bounds_of_the_contract_are_accepted_and_just_outside_are_declared_422(real, recorder, method):
    _, op = operation(OPERATIONS[method])
    bounds = parameters(op, "query")["limit"]["schema"]

    for limit in (bounds["minimum"], bounds["maximum"]):
        CALLS[method](real, limit=limit)
        assert recorder.last.status == 200
        assert recorder.last.query["limit"] == str(limit)
    for limit in (bounds["minimum"] - 1, bounds["maximum"] + 1):
        with pytest.raises(UpstreamError) as info:
            CALLS[method](real, limit=limit)
        assert info.value.status == recorder.last.status == 422
        assert "422" in declared_statuses(op)


# --- responses -----------------------------------------------------------------------------------


@pytest.mark.parametrize("method", list(OPERATIONS))
def test_real_app_bodies_validate_against_the_schema_the_contract_names(real, recorder, method):
    _, op = operation(OPERATIONS[method])

    CALLS[method](real)

    assert recorder.last.status == 200
    assert recorder.last.content_type.startswith("application/json")
    assert validation.errors(response_schema_name(op), recorder.last.body) == []


@pytest.mark.parametrize(
    ("method", "kwargs", "status"),
    [
        ("get_status", {}, 404),
        ("get_history", {"from_": TO, "to": FROM}, 422),
        ("get_sessions", {"from_": TO, "to": FROM}, 422),
        ("get_self_reports", {"from_": TO, "to": FROM}, 422),
        ("get_explanation", {}, 404),
    ],
)
def test_error_statuses_the_client_meets_are_declared_and_problem_shaped(tmp_path, method, kwargs, status):
    store = Store(tmp_path / "empty.db")  # no data: unknown ids give 404, inverted windows 422
    recorder = Recorder()
    client = TestClient(create_read_app(AppSettings(_env_file=None), store=store, clock=lambda: NOW))
    client.event_hooks["response"].append(recorder)
    reader = HttpAppReader("http://testserver", client=client)
    _, op = operation(OPERATIONS[method])
    if method == "get_status":
        client_call = lambda: reader.get_status("AST-99")  # noqa: E731
    else:
        client_call = lambda: CALLS[method](reader, **kwargs)  # noqa: E731

    with pytest.raises(UpstreamError) as info:
        client_call()
    store.close()

    assert info.value.status == recorder.last.status == status
    assert str(status) in declared_statuses(op)
    assert recorder.last.content_type.startswith("application/problem+json")
    assert validation.errors("app/error", recorder.last.body) == []
