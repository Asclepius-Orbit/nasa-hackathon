"""Servidor MCP de Asclepius Orbit: 7 herramientas de solo lectura sobre la salud lumbar de un astronauta.

Las descripciones y los nombres de herramientas y parámetros están en inglés a propósito: los lee un LLM pequeño
local, así que dicen con precisión qué devuelven, que los datos pueden faltar (null) y que nunca diagnostican.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import AwareDatetime, BaseModel, Field
from spine_contracts import (
    SCHEMA_VERSION,
    ComparePeriods,
    Explanation,
    History,
    SelfReportList,
    SessionList,
    SimulateRequest,
    SimulateResponse,
    Status,
    dump,
)
from spine_contracts.models.engine.simulate_request_schema import Override
from spine_contracts.validation import errors

from spine_mcp_server.compare import compare_periods as compare_histories
from spine_mcp_server.errors import UpstreamError
from spine_mcp_server.ports import AppReader, AssessBaseSource, Simulator

# Tope de `limit` de la API de la app; `compare_periods` lo usa siempre para leer cada periodo entero.
MAX_HISTORY_LIMIT = 500

INSTRUCTIONS = (
    "Read-only access to the spinal-health monitoring data of astronauts (EMG, IMU, stretch sensors and self-reports). "
    "Every tool only reads data or runs a hypothetical what-if; nothing is ever written or changed. "
    "The tools describe the monitoring evidence and risk levels (green, amber, red); they never diagnose a medical condition. "
    "Any value can be null or missing when a sensor or report is unavailable; say so instead of guessing."
)

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

AstronautId = Annotated[str, Field(description="Astronaut identifier, for example 'AST-01'.")]


def _window(what: str) -> str:
    return f"{what} as an RFC 3339 timestamp with a UTC offset, for example '2027-02-09T00:00:00Z'. Timestamps without an offset are rejected."


def _result(model: BaseModel) -> CallToolResult:
    """Salida estructurada con la forma exacta del contrato (`dump`: sin defaults no fijados, con alias como `from`) y su copia en texto.

    El SDK serializaría el modelo con todos los campos por defecto (`urgency: null`...) y el esquema no los admite.
    """
    payload = dump(model)
    return CallToolResult(content=[TextContent(text=json.dumps(payload))], structured_content=payload)


@contextmanager
def _upstream_as_tool_errors() -> Iterator[None]:
    """Un fallo de la app o del motor llega al cliente como `isError: true` con «título: detalle»."""
    try:
        yield
    except UpstreamError as error:
        raise ToolError(str(error)) from error


def create_server(app: AppReader, simulator: Simulator, base: AssessBaseSource) -> MCPServer:
    """Servidor MCP con sus 7 herramientas, que solo hablan con el resto del sistema a través de los puertos dados."""
    server = MCPServer("spine-mcp-server", instructions=INSTRUCTIONS)

    @server.tool(
        name="get_current_status",
        annotations=_READ_ONLY,
        description=(
            "Get the current monitoring status of one astronaut: the latest risk assessment (level green, amber or red, risk score, confidence, "
            "drivers and escalation advice), the current session, the latest self-report, device states and how fresh each data source is. "
            "Any part can be null or missing when no data has arrived yet. It reports monitoring evidence and never diagnoses a medical condition."
        ),
    )
    def get_current_status(astronaut_id: AstronautId) -> Annotated[CallToolResult, Status]:
        with _upstream_as_tool_errors():
            return _result(app.get_status(astronaut_id))

    @server.tool(
        name="get_history",
        annotations=_READ_ONLY,
        description=(
            "Get the history of one astronaut between two instants: the computed feature sets (EMG, IMU and stretch metrics) and the risk assessments, "
            "oldest first, at most `limit` of each. `truncated` is true when more data existed than `limit` allowed (the oldest are dropped). "
            "The window is echoed back as `from` and `to`. Lists can be empty and values null or missing when there was no data. "
            "It is raw monitoring evidence and never diagnoses a medical condition."
        ),
    )
    def get_history(
        astronaut_id: AstronautId,
        start: Annotated[AwareDatetime, Field(description=_window("Start of the window (inclusive)"))],
        end: Annotated[AwareDatetime, Field(description=_window("End of the window (inclusive); must not be before `start`"))],
        limit: Annotated[int, Field(ge=1, le=MAX_HISTORY_LIMIT, description="Maximum feature sets and assessments to return, from 1 to 500.")] = 100,
    ) -> Annotated[CallToolResult, History]:
        # `start`/`end` en vez de `from`/`to`: `from` es palabra reservada en Python. La salida conserva `from`/`to` (ver `dump`).
        with _upstream_as_tool_errors():
            return _result(app.get_history(astronaut_id, start, end, limit))

    @server.tool(
        name="get_sessions",
        annotations=_READ_ONLY,
        description=(
            "List the sessions of one astronaut that started between two instants: exercise or test sessions with their protocol, device, mission context, "
            "declared load, streams and events, oldest first, at most `limit`. `truncated` is true when more sessions existed than `limit` allowed "
            "(the oldest are dropped). The window is echoed back as `from` and `to`. The list can be empty and values null or missing when there was no data. "
            "It is raw monitoring evidence and never diagnoses a medical condition."
        ),
    )
    def get_sessions(
        astronaut_id: AstronautId,
        start: Annotated[AwareDatetime, Field(description=_window("Start of the window (inclusive); sessions are matched by their start time"))],
        end: Annotated[AwareDatetime, Field(description=_window("End of the window (inclusive); must not be before `start`"))],
        limit: Annotated[int, Field(ge=1, le=MAX_HISTORY_LIMIT, description="Maximum sessions to return, from 1 to 500.")] = 100,
    ) -> Annotated[CallToolResult, SessionList]:
        with _upstream_as_tool_errors():
            return _result(app.get_sessions(astronaut_id, start, end, limit))

    @server.tool(
        name="get_self_reports",
        annotations=_READ_ONLY,
        description=(
            "List the self-reports of one astronaut (how the astronaut says they feel, for example pain or fatigue) made between two instants, "
            "oldest first, at most `limit`. `truncated` is true when more self-reports existed than `limit` allowed (the oldest are dropped). "
            "The window is echoed back as `from` and `to`. The list can be empty and values null or missing when nothing was reported. "
            "They are subjective reports, not measurements, and this tool never diagnoses a medical condition."
        ),
    )
    def get_self_reports(
        astronaut_id: AstronautId,
        start: Annotated[AwareDatetime, Field(description=_window("Start of the window (inclusive)"))],
        end: Annotated[AwareDatetime, Field(description=_window("End of the window (inclusive); must not be before `start`"))],
        limit: Annotated[int, Field(ge=1, le=MAX_HISTORY_LIMIT, description="Maximum self-reports to return, from 1 to 500.")] = 100,
    ) -> Annotated[CallToolResult, SelfReportList]:
        with _upstream_as_tool_errors():
            return _result(app.get_self_reports(astronaut_id, start, end, limit))

    @server.tool(
        name="compare_periods",
        annotations=_READ_ONLY,
        description=(
            "Compare two time windows (A and B) of the same astronaut. Returns, per feature measured in both windows with the same protocol, gravity "
            "environment and unit, the count, mean and median of each window and the deltas (B minus A); the features present in only one window; "
            "the count of green, amber and red assessments and the latest level of each window; and the mean risk score, confidence and completeness "
            "with their deltas. Scores and deltas are null when a window has no assessments. `warnings` flags empty or truncated windows "
            "(period_a_empty, period_b_truncated...) and unit mismatches. It describes monitoring evidence and never diagnoses a medical condition."
        ),
    )
    def compare_periods(
        astronaut_id: AstronautId,
        a_start: Annotated[AwareDatetime, Field(description=_window("Start of window A (inclusive)"))],
        a_end: Annotated[AwareDatetime, Field(description=_window("End of window A (inclusive)"))],
        b_start: Annotated[AwareDatetime, Field(description=_window("Start of window B (inclusive)"))],
        b_end: Annotated[AwareDatetime, Field(description=_window("End of window B (inclusive)"))],
    ) -> Annotated[CallToolResult, ComparePeriods]:
        with _upstream_as_tool_errors():
            history_a = app.get_history(astronaut_id, a_start, a_end, MAX_HISTORY_LIMIT)
            history_b = app.get_history(astronaut_id, b_start, b_end, MAX_HISTORY_LIMIT)
        try:
            return _result(compare_histories(history_a, history_b))
        except ValueError as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="explain_assessment",
        annotations=_READ_ONLY,
        description=(
            "Explain one risk assessment by its id: the assessment itself, the baseline it was compared with, the features and self-reports it was "
            "based on, and the data that could not be resolved. Use an assessment_id taken from get_current_status or get_history. "
            "Parts can be null or missing when the data was unavailable. It explains the evidence behind a risk level and never diagnoses "
            "a medical condition."
        ),
    )
    def explain_assessment(
        assessment_id: Annotated[str, Field(description="Assessment identifier (a lowercase UUID) from get_current_status or get_history.")],
    ) -> Annotated[CallToolResult, Explanation]:
        with _upstream_as_tool_errors():
            return _result(app.get_explanation(assessment_id))

    @server.tool(
        name="simulate_scenario",
        annotations=_READ_ONLY,
        description=(
            "Run a hypothetical what-if for one astronaut: apply overrides to the astronaut's current inputs and return the baseline assessment next "
            "to the assessment of the scenario, so the difference is visible. Each override changes one feature (`feature_id`) with mode 'set' "
            "(replace the value), 'add' (add to it) or 'scale' (multiply it). `project_days` optionally projects the trend of the scenario. "
            "Purely hypothetical: it never changes any real data. Values can be null or missing in the result. It never diagnoses a medical condition."
        ),
    )
    def simulate_scenario(
        astronaut_id: AstronautId,
        overrides: Annotated[list[Override], Field(min_length=1, description="Feature changes to apply, at least one. Each has feature_id, mode and value.")],
        project_days: Annotated[int | None, Field(ge=1, le=365, description="Optional horizon in days (1 to 365) for the trend of the scenario.")] = None,
    ) -> Annotated[CallToolResult, SimulateResponse]:
        with _upstream_as_tool_errors():
            # Sin `project_days` el campo no se fija: `dump` no emite lo no fijado, y el esquema no admite null.
            optional = {} if project_days is None else {"project_days": project_days}
            request = SimulateRequest(schema_version=SCHEMA_VERSION, base=base.get_base(astronaut_id), overrides=overrides, **optional)
            # Las reglas condicionales del contrato solo las expresa el esquema, no los modelos: se validan aquí antes de llamar al motor.
            problems = errors("engine/simulate-request", dump(request))
            if problems:
                raise ToolError("invalid simulate request: " + "; ".join(problems))
            return _result(simulator.simulate(request))

    return server
