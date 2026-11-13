"""Tests del servidor MCP: las 7 herramientas, en proceso (`Client` sobre `MCPServer`), contra puertos falsos y los mocks de ejemplo."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp.client.client import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, Tool
from spine_contracts import SCHEMA_VERSION, AssessRequest, Explanation, History, SelfReportList, SessionList, SimulateRequest, SimulateResponse, Status, dump
from spine_contracts.validation import validate
from spine_mcp_server import main
from spine_mcp_server.errors import UpstreamError
from spine_mcp_server.mocks import ExampleAppReader, ExampleAssessBaseSource, ExampleSimulator
from spine_mcp_server.server import create_server

AST = "AST-01"
ASSESSMENT_ID = "a55e55e0-0000-4000-8000-0000000000a1"
TOOLS = {"get_current_status", "get_history", "compare_periods", "explain_assessment", "simulate_scenario", "get_sessions", "get_self_reports"}
WIDE = {"start": "2027-01-01T00:00:00Z", "end": "2027-03-01T00:00:00Z"}
WIDE_PERIODS = {"a_start": WIDE["start"], "a_end": WIDE["end"], "b_start": WIDE["start"], "b_end": WIDE["end"]}
OVERRIDE = {"feature_id": "stretch.full_spine.peak_elongation_mm", "mode": "add", "value": 5}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class RecordingApp:
    """`AppReader` falso que delega en los ejemplos y apunta cada llamada (método y argumentos).

    Si se da `second_history`, la segunda llamada a `get_history` devuelve ese historial en lugar del de los ejemplos.
    """

    def __init__(self, second_history: History | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._inner = ExampleAppReader()
        self._second_history = second_history

    def get_status(self, astronaut_id: str) -> Status:
        self.calls.append(("get_status", (astronaut_id,)))
        return self._inner.get_status(astronaut_id)

    def get_history(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> History:
        self.calls.append(("get_history", (astronaut_id, from_, to, limit)))
        history = self._inner.get_history(astronaut_id, from_, to, limit)
        if self._second_history is not None and sum(name == "get_history" for name, _ in self.calls) == 2:
            return self._second_history
        return history

    def get_sessions(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> SessionList:
        self.calls.append(("get_sessions", (astronaut_id, from_, to, limit)))
        return self._inner.get_sessions(astronaut_id, from_, to, limit)

    def get_self_reports(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> SelfReportList:
        self.calls.append(("get_self_reports", (astronaut_id, from_, to, limit)))
        return self._inner.get_self_reports(astronaut_id, from_, to, limit)

    def get_explanation(self, assessment_id: str) -> Explanation:
        self.calls.append(("get_explanation", (assessment_id,)))
        return self._inner.get_explanation(assessment_id)


class RecordingSimulator:
    def __init__(self) -> None:
        self.requests: list[SimulateRequest] = []

    def simulate(self, request: SimulateRequest) -> SimulateResponse:
        self.requests.append(request)
        return ExampleSimulator().simulate(request)


class RecordingBase:
    def __init__(self, base: AssessRequest | None = None) -> None:
        self.calls: list[str] = []
        self._base = base

    def get_base(self, astronaut_id: str) -> AssessRequest:
        self.calls.append(astronaut_id)
        return self._base if self._base is not None else ExampleAssessBaseSource().get_base(astronaut_id)


class FailingApp:
    def get_status(self, astronaut_id: str) -> Status:
        raise UpstreamError(503, "Boom", "it broke")

    def get_history(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> History:
        raise UpstreamError(503, "Boom", "it broke")

    def get_sessions(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> SessionList:
        raise UpstreamError(503, "Boom", "it broke")

    def get_self_reports(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> SelfReportList:
        raise UpstreamError(503, "Boom", "it broke")

    def get_explanation(self, assessment_id: str) -> Explanation:
        raise UpstreamError(503, "Boom", "it broke")


class FailingSimulator:
    def simulate(self, request: SimulateRequest) -> SimulateResponse:
        raise UpstreamError(503, "Boom", "it broke")


class FailingBase:
    def get_base(self, astronaut_id: str) -> AssessRequest:
        raise UpstreamError(503, "Boom", "it broke")


def _server(app: Any = None, simulator: Any = None, base: Any = None) -> MCPServer:
    return create_server(app or ExampleAppReader(), simulator or ExampleSimulator(), base or ExampleAssessBaseSource())


async def _call(server: MCPServer, name: str, arguments: dict[str, Any]) -> CallToolResult:
    async with Client(server) as client:
        return await client.call_tool(name, arguments)


async def _tools(server: MCPServer) -> dict[str, Tool]:
    async with Client(server) as client:
        return {tool.name: tool for tool in (await client.list_tools()).tools}


def _text(result: CallToolResult) -> str:
    assert result.is_error
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


def _data(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


# Argumentos válidos de cada herramienta para AST-01 y el esquema de su salida.
CALLS: list[tuple[str, dict[str, Any], str]] = [
    ("get_current_status", {"astronaut_id": AST}, "app/status"),
    ("get_history", {"astronaut_id": AST} | WIDE, "app/history"),
    ("get_sessions", {"astronaut_id": AST} | WIDE, "app/session-list"),
    ("get_self_reports", {"astronaut_id": AST} | WIDE, "app/self-report-list"),
    ("compare_periods", {"astronaut_id": AST} | WIDE_PERIODS, "mcp/compare-periods"),
    ("explain_assessment", {"assessment_id": ASSESSMENT_ID}, "app/explanation"),
    ("simulate_scenario", {"astronaut_id": AST, "overrides": [OVERRIDE], "project_days": 30}, "engine/simulate-response"),
]


# --- catálogo de herramientas ---


@pytest.mark.anyio
async def test_lists_exactly_the_seven_tools():
    assert set(await _tools(_server())) == TOOLS


@pytest.mark.anyio
async def test_server_identifies_itself_and_tells_the_model_it_is_read_only_and_never_diagnoses():
    async with Client(_server()) as client:
        assert client.server_info is not None
        assert client.server_info.name == "spine-mcp-server"
        instructions = (client.instructions or "").lower()
    assert "read-only" in instructions
    assert "never diagnose" in instructions
    assert "null" in instructions


@pytest.mark.anyio
async def test_every_tool_is_read_only_idempotent_and_closed_world():
    for tool in (await _tools(_server())).values():
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name
        assert tool.annotations.idempotent_hint is True, tool.name
        assert tool.annotations.open_world_hint is False, tool.name


@pytest.mark.anyio
async def test_every_tool_has_an_english_description_that_says_it_never_diagnoses():
    for tool in (await _tools(_server())).values():
        description = (tool.description or "").lower()
        assert len(description) > 80, tool.name
        assert "never diagnos" in description, tool.name
        assert "null" in description or "missing" in description, tool.name


@pytest.mark.anyio
async def test_every_input_parameter_is_described():
    for tool in (await _tools(_server())).values():
        for name, prop in tool.input_schema["properties"].items():
            assert prop.get("description"), f"{tool.name}.{name}"


@pytest.mark.anyio
async def test_parameter_names_and_required_ones():
    tools = await _tools(_server())
    expected = {
        "get_current_status": ({"astronaut_id"}, {"astronaut_id"}),
        "get_history": ({"astronaut_id", "start", "end", "limit"}, {"astronaut_id", "start", "end"}),
        "get_sessions": ({"astronaut_id", "start", "end", "limit"}, {"astronaut_id", "start", "end"}),
        "get_self_reports": ({"astronaut_id", "start", "end", "limit"}, {"astronaut_id", "start", "end"}),
        "compare_periods": ({"astronaut_id", "a_start", "a_end", "b_start", "b_end"}, {"astronaut_id", "a_start", "a_end", "b_start", "b_end"}),
        "explain_assessment": ({"assessment_id"}, {"assessment_id"}),
        "simulate_scenario": ({"astronaut_id", "overrides", "project_days"}, {"astronaut_id"}),
    }
    for name, (properties, required) in expected.items():
        schema = tools[name].input_schema
        assert set(schema["properties"]) == properties, name
        assert set(schema["required"]) == required, name


@pytest.mark.anyio
async def test_get_history_limit_schema_is_1_to_500_default_100():
    limit = (await _tools(_server()))["get_history"].input_schema["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 500, 100)


@pytest.mark.anyio
async def test_every_tool_declares_an_output_schema():
    for tool in (await _tools(_server())).values():
        assert tool.output_schema is not None, tool.name


# --- respuestas con los mocks, validadas contra el esquema ---


@pytest.mark.contract
@pytest.mark.anyio
@pytest.mark.parametrize(("name", "arguments", "schema"), CALLS, ids=[c[0] for c in CALLS])
async def test_each_tool_output_validates_against_its_schema(name: str, arguments: dict[str, Any], schema: str):
    validate(schema, _data(await _call(_server(), name, arguments)))


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "arguments", "_schema"), CALLS, ids=[c[0] for c in CALLS])
async def test_each_tool_also_returns_its_output_as_json_text(name: str, arguments: dict[str, Any], _schema: str):
    result = await _call(_server(), name, arguments)
    (content,) = result.content
    assert isinstance(content, TextContent)
    assert content.type == "text"
    assert json.loads(content.text) == _data(result)


@pytest.mark.anyio
async def test_get_current_status_returns_the_astronaut_status():
    data = _data(await _call(_server(), "get_current_status", {"astronaut_id": AST}))
    assert data["astronaut_id"] == AST
    assert data == dump(ExampleAppReader().get_status(AST))


@pytest.mark.anyio
async def test_get_history_passes_the_window_and_limit_to_the_port_and_returns_from_and_to():
    app = RecordingApp()
    data = _data(await _call(_server(app=app), "get_history", {"astronaut_id": AST, "start": WIDE["start"], "end": WIDE["end"], "limit": 7}))
    assert app.calls == [("get_history", (AST, datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 3, 1, tzinfo=UTC), 7))]
    assert data["from"] == "2027-01-01T00:00:00Z"
    assert data["to"] == "2027-03-01T00:00:00Z"
    assert data["limit"] == 7
    assert "from_" not in data


@pytest.mark.anyio
async def test_get_history_default_limit_is_100():
    app = RecordingApp()
    await _call(_server(app=app), "get_history", {"astronaut_id": AST} | WIDE)
    assert app.calls[0][1][3] == 100


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [1, 500])
async def test_get_history_accepts_the_limit_bounds(limit: int):
    app = RecordingApp()
    _data(await _call(_server(app=app), "get_history", {"astronaut_id": AST, "limit": limit} | WIDE))
    assert app.calls[0][1][3] == limit


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["get_sessions", "get_self_reports"])
async def test_list_tool_limit_schema_is_1_to_500_default_100(name: str):
    limit = (await _tools(_server()))[name].input_schema["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 500, 100)


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["get_sessions", "get_self_reports"])
async def test_list_tool_passes_the_window_and_limit_to_the_port_and_returns_from_and_to(name: str):
    app = RecordingApp()
    data = _data(await _call(_server(app=app), name, {"astronaut_id": AST, "start": WIDE["start"], "end": WIDE["end"], "limit": 7}))
    assert app.calls == [(name, (AST, datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 3, 1, tzinfo=UTC), 7))]
    assert data["from"] == "2027-01-01T00:00:00Z"
    assert data["to"] == "2027-03-01T00:00:00Z"
    assert data["limit"] == 7
    assert "from_" not in data


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["get_sessions", "get_self_reports"])
async def test_list_tool_default_limit_is_100(name: str):
    app = RecordingApp()
    await _call(_server(app=app), name, {"astronaut_id": AST} | WIDE)
    assert app.calls[0][1][3] == 100


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["get_sessions", "get_self_reports"])
@pytest.mark.parametrize("limit", [1, 500])
async def test_list_tool_accepts_the_limit_bounds(name: str, limit: int):
    app = RecordingApp()
    _data(await _call(_server(app=app), name, {"astronaut_id": AST, "limit": limit} | WIDE))
    assert app.calls[0][1][3] == limit


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["get_sessions", "get_self_reports"])
@pytest.mark.parametrize("limit", [0, 501, -1])
async def test_list_tool_rejects_a_limit_out_of_range(name: str, limit: int):
    app = RecordingApp()
    assert _text(await _call(_server(app=app), name, {"astronaut_id": AST, "limit": limit} | WIDE))
    assert app.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["get_sessions", "get_self_reports"])
async def test_list_tool_end_before_start_is_an_invalid_range_tool_error(name: str):
    text = _text(await _call(_server(), name, {"astronaut_id": AST, "start": WIDE["end"], "end": WIDE["start"]}))
    assert "invalid_range" in text


@pytest.mark.anyio
async def test_explain_assessment_returns_the_explanation():
    data = _data(await _call(_server(), "explain_assessment", {"assessment_id": ASSESSMENT_ID}))
    assert data["assessment"]["assessment_id"] == ASSESSMENT_ID


@pytest.mark.anyio
async def test_simulate_scenario_builds_the_request_for_the_simulator():
    simulator, base = RecordingSimulator(), RecordingBase()
    data = _data(await _call(_server(simulator=simulator, base=base), "simulate_scenario", {"astronaut_id": AST, "overrides": [OVERRIDE], "project_days": 30}))
    assert base.calls == [AST]
    (request,) = simulator.requests
    assert request.schema_version == SCHEMA_VERSION
    assert request.base == ExampleAssessBaseSource().get_base(AST)
    assert dump(request)["overrides"] == [OVERRIDE]
    assert request.project_days == 30
    assert data == dump(ExampleSimulator().simulate(request))


@pytest.mark.anyio
async def test_simulate_scenario_project_days_is_optional():
    simulator = RecordingSimulator()
    _data(await _call(_server(simulator=simulator), "simulate_scenario", {"astronaut_id": AST, "overrides": [OVERRIDE]}))
    assert simulator.requests[0].project_days is None
    assert "project_days" not in dump(simulator.requests[0])


@pytest.mark.anyio
@pytest.mark.parametrize("arguments", [{"overrides": []}, {}], ids=["empty list", "omitted"])
async def test_simulate_scenario_without_overrides_projects_the_trajectory_unchanged(arguments: dict[str, Any]):
    simulator = RecordingSimulator()
    data = _data(await _call(_server(simulator=simulator), "simulate_scenario", {"astronaut_id": AST, "project_days": 30} | arguments))
    (request,) = simulator.requests
    assert request.overrides == []
    assert dump(request)["overrides"] == []
    assert validate("engine/simulate-response", data) is None


@pytest.mark.anyio
async def test_simulate_scenario_overrides_schema_is_optional_without_min_items():
    schema = (await _tools(_server()))["simulate_scenario"].input_schema
    assert "overrides" not in schema["required"]
    assert "minItems" not in schema["properties"]["overrides"]
    assert "no changes" in schema["properties"]["overrides"]["description"]


# --- compare_periods ---


@pytest.mark.anyio
async def test_compare_periods_reads_each_period_once_with_limit_500():
    app = RecordingApp()
    a = ("2027-02-01T00:00:00Z", "2027-02-10T00:00:00Z")
    b = ("2027-02-11T00:00:00Z", "2027-02-12T00:00:00Z")
    data = _data(await _call(_server(app=app), "compare_periods", {"astronaut_id": AST, "a_start": a[0], "a_end": a[1], "b_start": b[0], "b_end": b[1]}))
    parse = datetime.fromisoformat
    assert app.calls == [
        ("get_history", (AST, parse(a[0]), parse(a[1]), 500)),
        ("get_history", (AST, parse(b[0]), parse(b[1]), 500)),
    ]
    assert data["astronaut_id"] == AST
    assert data["period_a"]["assessments_count"] == 1
    assert data["period_b"]["assessments_count"] == 0


@pytest.mark.contract
@pytest.mark.anyio
async def test_compare_periods_with_empty_periods_warns_and_has_null_scores():
    far = {"a_start": "2028-01-01T00:00:00Z", "a_end": "2028-01-02T00:00:00Z", "b_start": "2028-02-01T00:00:00Z", "b_end": "2028-02-02T00:00:00Z"}
    data = _data(await _call(_server(), "compare_periods", {"astronaut_id": AST} | far))
    validate("mcp/compare-periods", data)
    assert {"period_a_empty", "period_b_empty"} <= set(data["warnings"])
    assert data["scores"]["risk_score"] == {"a": None, "b": None, "delta": None}
    assert data["features"] == []


@pytest.mark.anyio
async def test_compare_periods_is_a_tool_error_when_histories_belong_to_different_astronauts():
    wide_from, wide_to = datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 3, 1, tzinfo=UTC)
    other = ExampleAppReader().get_history(AST, wide_from, wide_to).model_copy(update={"astronaut_id": "AST-02"})
    result = await _call(_server(app=RecordingApp(second_history=other)), "compare_periods", {"astronaut_id": AST} | WIDE_PERIODS)
    assert "different astronauts" in _text(result)


# --- errores ---


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "arguments", "title"),
    [
        ("get_current_status", {"astronaut_id": "AST-99"}, "Astronaut not found"),
        ("get_history", {"astronaut_id": "AST-99"} | WIDE, "Astronaut not found"),
        ("get_sessions", {"astronaut_id": "AST-99"} | WIDE, "Astronaut not found"),
        ("get_self_reports", {"astronaut_id": "AST-99"} | WIDE, "Astronaut not found"),
        ("explain_assessment", {"assessment_id": "00000000-0000-4000-8000-000000000000"}, "Assessment not found"),
        ("simulate_scenario", {"astronaut_id": "AST-99", "overrides": [OVERRIDE]}, "Astronaut not found"),
        ("compare_periods", {"astronaut_id": "AST-99"} | WIDE_PERIODS, "Astronaut not found"),
    ],
)
async def test_unknown_ids_are_tool_errors_carrying_the_upstream_title(name: str, arguments: dict[str, Any], title: str):
    assert title in _text(await _call(_server(), name, arguments))


@pytest.mark.anyio
async def test_end_before_start_is_an_invalid_range_tool_error():
    text = _text(await _call(_server(), "get_history", {"astronaut_id": AST, "start": WIDE["end"], "end": WIDE["start"]}))
    assert "invalid_range" in text


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "arguments", "_schema"), CALLS, ids=[c[0] for c in CALLS])
async def test_upstream_errors_become_title_and_detail(name: str, arguments: dict[str, Any], _schema: str):
    server = _server(app=FailingApp(), simulator=FailingSimulator(), base=FailingBase())
    # El SDK antepone «Error executing tool <name>: » al mensaje del ToolError.
    assert _text(await _call(server, name, arguments)).endswith(": Boom: it broke")


@pytest.mark.anyio
async def test_simulator_failure_is_a_tool_error_even_when_the_base_is_fine():
    result = await _call(_server(simulator=FailingSimulator()), "simulate_scenario", {"astronaut_id": AST, "overrides": [OVERRIDE]})
    assert _text(result).endswith(": Boom: it broke")


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [0, 501, -1])
async def test_get_history_rejects_a_limit_out_of_range(limit: int):
    app = RecordingApp()
    assert _text(await _call(_server(app=app), "get_history", {"astronaut_id": AST, "limit": limit} | WIDE))
    assert app.calls == []


@pytest.mark.anyio
async def test_naive_datetimes_are_rejected():
    app = RecordingApp()
    assert _text(await _call(_server(app=app), "get_history", {"astronaut_id": AST, "start": "2027-01-01T00:00:00", "end": WIDE["end"]}))
    assert app.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [
        {"overrides": [{"feature_id": "NotAFeature", "mode": "set", "value": 1}]},
        {"overrides": [{"feature_id": "stretch.full_spine.peak_elongation_mm", "mode": "multiply", "value": 1}]},
        {"overrides": [OVERRIDE], "project_days": 0},
        {"overrides": [OVERRIDE], "project_days": 366},
    ],
)
async def test_simulate_scenario_rejects_invalid_arguments_without_calling_the_simulator(arguments: dict[str, Any]):
    simulator = RecordingSimulator()
    assert _text(await _call(_server(simulator=simulator), "simulate_scenario", {"astronaut_id": AST} | arguments))
    assert simulator.requests == []


@pytest.mark.anyio
async def test_simulate_scenario_validates_the_request_against_the_schema_and_reports_each_violation():
    # Una base con una regla que solo expresa el esquema (feature_sets no vacío): `model_copy` no valida.
    invalid_base = ExampleAssessBaseSource().get_base(AST).model_copy(update={"feature_sets": []})
    simulator = RecordingSimulator()
    result = await _call(_server(simulator=simulator, base=RecordingBase(invalid_base)), "simulate_scenario", {"astronaut_id": AST, "overrides": [OVERRIDE]})
    text = _text(result)
    assert "invalid simulate request: /base/feature_sets: [] should be non-empty" in text
    assert simulator.requests == []


# --- solo lectura ---


@pytest.mark.anyio
async def test_read_tools_only_call_their_own_read_method():
    app = RecordingApp()
    server = _server(app=app)
    await _call(server, "get_current_status", {"astronaut_id": AST})
    assert [name for name, _ in app.calls] == ["get_status"]
    await _call(server, "explain_assessment", {"assessment_id": ASSESSMENT_ID})
    assert [name for name, _ in app.calls] == ["get_status", "get_explanation"]
    await _call(server, "get_history", {"astronaut_id": AST} | WIDE)
    assert [name for name, _ in app.calls] == ["get_status", "get_explanation", "get_history"]
    await _call(server, "get_sessions", {"astronaut_id": AST} | WIDE)
    await _call(server, "get_self_reports", {"astronaut_id": AST} | WIDE)
    assert [name for name, _ in app.calls][3:] == ["get_sessions", "get_self_reports"]


@pytest.mark.anyio
async def test_simulate_scenario_does_not_touch_the_app_port():
    app = RecordingApp()
    await _call(_server(app=app), "simulate_scenario", {"astronaut_id": AST, "overrides": [OVERRIDE]})
    assert app.calls == []


# --- punto de entrada ---


@pytest.mark.anyio
async def test_main_serves_the_seven_tools_over_stdio(monkeypatch: pytest.MonkeyPatch):
    # La frontera es el transporte: se sustituye `run` (que bloquearía leyendo stdin) y se comprueba qué servidor y transporte se arrancan.
    started: list[tuple[MCPServer, str]] = []
    monkeypatch.setattr(MCPServer, "run", lambda self, transport="stdio": started.append((self, transport)))
    main([])  # argv explícito: sin él argparse leería los argumentos de pytest
    ((server, transport),) = started
    assert transport == "stdio"
    assert set(await _tools(server)) == TOOLS
