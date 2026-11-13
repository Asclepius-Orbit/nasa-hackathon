"""Tests del dataset de demo: datos de AST-01 generados relativos a un reloj inyectable, servidos por el servidor MCP real."""

import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver import MCPServer
from spine_contracts import Level, SimulateRequest, dump
from spine_contracts.validation import errors, validate
from spine_mcp_server import build_server, main, parse_args
from spine_mcp_server.demo_data import build_demo_app_reader, build_demo_assess_base, build_demo_simulator
from spine_mcp_server.errors import UpstreamError
from spine_mcp_server.mocks import ExampleAppReader
from spine_mcp_server.server import create_server

AST = "AST-01"
WEEK = timedelta(days=7)
# Los tres días del hackathon (13-15 nov 2026) y hoy, con una hora no redonda para ejercitar el redondeo.
NOWS = [
    datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
    datetime(2026, 11, 14, 12, 0, tzinfo=UTC),
    datetime(2026, 11, 15, 12, 0, tzinfo=UTC),
    datetime(2026, 11, 6, 9, 30, tzinfo=UTC),
]
NOW = NOWS[0]
TIMEOUT_SECONDS = 30
OVERRIDE = {"feature_id": "stretch.full_spine.peak_elongation_mm", "mode": "add", "value": 5}

pytestmark = pytest.mark.parametrize("now", NOWS, ids=lambda n: n.isoformat())


def _request(now: datetime) -> SimulateRequest:
    base = build_demo_assess_base(now).get_base(AST)
    return SimulateRequest.model_validate({"schema_version": "1.0.0", "base": dump(base), "overrides": [OVERRIDE]})


def _all_payloads(now: datetime) -> dict[str, tuple[str, Any]]:
    reader = build_demo_app_reader(now)
    start, end = now - timedelta(days=30), now
    history = reader.get_history(AST, start, end)
    return {
        "status": ("app/status", reader.get_status(AST)),
        "history": ("app/history", history),
        "sessions": ("app/session-list", reader.get_sessions(AST, start, end)),
        "self_reports": ("app/self-report-list", reader.get_self_reports(AST, start, end)),
        "explanation": ("app/explanation", reader.get_explanation(str(history.assessments[-1].assessment_id))),
        "base": ("engine/assess-request", build_demo_assess_base(now).get_base(AST)),
        "simulation": ("engine/simulate-response", build_demo_simulator(now).simulate(_request(now))),
    }


@pytest.mark.contract
@pytest.mark.parametrize("payload", ["status", "history", "sessions", "self_reports", "explanation", "base", "simulation"])
def test_every_generated_payload_validates_against_its_json_schema(now: datetime, payload: str):
    schema, model = _all_payloads(now)[payload]
    assert errors(schema, dump(model)) == []


def test_same_clock_gives_identical_output(now: datetime):
    first, second = _all_payloads(now), _all_payloads(now)
    assert {k: dump(v[1]) for k, v in first.items()} == {k: dump(v[1]) for k, v in second.items()}


def test_clock_is_rounded_down_to_the_hour(now: datetime):
    exact = build_demo_app_reader(now.replace(minute=0))
    assert dump(build_demo_app_reader(now.replace(minute=0) + timedelta(minutes=17)).get_status(AST)) == dump(exact.get_status(AST))


def test_a_later_clock_shifts_the_ids(now: datetime):
    a = build_demo_app_reader(now).get_status(AST).latest_assessment
    b = build_demo_app_reader(now + timedelta(hours=1)).get_status(AST).latest_assessment
    assert a is not None and b is not None
    assert a.assessment_id != b.assessment_id


def test_latest_assessment_is_amber_and_as_of_now(now: datetime):
    latest = build_demo_app_reader(now).get_status(AST).latest_assessment
    assert latest is not None
    assert latest.level == Level.AMBER
    assert latest.as_of == now.replace(minute=0)
    assert latest.trend is not None and latest.trend.projected_level == Level.RED
    assert [d.feature_id for d in latest.drivers] == ["stretch.full_spine.peak_elongation_mm", "symptoms.pain_nrs_7d_mean"]
    assert all(d.deviation_z is not None and d.deviation_z > 1 for d in latest.drivers)
    assert [r.rule_id for r in latest.fired_rules] == ["RULE_SYMPTOM_PAIN_TREND"]


def test_trajectory_is_green_then_amber_the_last_six_days_with_a_rising_risk(now: datetime):
    assessments = build_demo_app_reader(now).get_history(AST, now - timedelta(days=14), now).assessments
    assert [a.level for a in assessments] == [Level.GREEN] * 8 + [Level.AMBER] * 6
    risks = [a.risk_score for a in assessments]
    assert risks == sorted(set(risks))


def test_last_week_has_enough_data_to_talk_about(now: datetime):
    reader = build_demo_app_reader(now)
    start = now - WEEK
    assessments = reader.get_history(AST, start, now).assessments
    assert len(assessments) >= 6
    assert [a.risk_score for a in assessments] == sorted(a.risk_score for a in assessments)
    assert len(reader.get_sessions(AST, start, now).items) >= 3
    reports = reader.get_self_reports(AST, start, now).items
    assert len(reports) >= 6
    pains = [r.pain_nrs for r in reports if r.kind == "daily"]
    assert pains[-1] > pains[0]


def test_two_weeks_hold_exercise_sessions_spine_checks_and_one_weekly_odi(now: datetime):
    reader = build_demo_app_reader(now)
    sessions = reader.get_sessions(AST, now - timedelta(days=14), now).items
    kinds = [s.session_type for s in sessions]
    assert kinds.count("exercise") >= 6 and 1 <= kinds.count("spine_check") <= 4
    reports = reader.get_self_reports(AST, now - timedelta(days=14), now).items
    assert [r.kind for r in reports].count("weekly_odi") == 1
    history = reader.get_history(AST, now - timedelta(days=14), now)
    assert len(history.feature_sets) == kinds.count("exercise")
    assert all(f.computed_at <= now for f in history.feature_sets)
    assert all(s.started_at <= now for s in sessions)


def test_unknown_astronaut_is_a_not_found_error(now: datetime):
    reader = build_demo_app_reader(now)
    with pytest.raises(UpstreamError) as caught:
        reader.get_status("AST-99")
    assert caught.value.status == 404
    with pytest.raises(UpstreamError):
        build_demo_assess_base(now).get_base("AST-99")
    with pytest.raises(UpstreamError):
        reader.get_explanation("00000000-0000-4000-8000-000000000000")


def test_explanation_works_for_every_assessment_of_the_history(now: datetime):
    reader = build_demo_app_reader(now)
    assessments = reader.get_history(AST, now - timedelta(days=14), now).assessments
    assert len(assessments) == 14
    for assessment in assessments:
        explanation = reader.get_explanation(str(assessment.assessment_id))
        assert explanation.assessment == assessment
        assert errors("app/explanation", dump(explanation)) == []
        assert explanation.unresolved == []
        assert explanation.baseline is not None and explanation.baseline_entries
        assert assessment.based_on is not None
        assert {f.session_id for f in explanation.feature_sets} == set(assessment.based_on.session_ids or [])
        assert {r.report_id for r in explanation.self_reports} == set(assessment.based_on.self_report_ids or [])


def test_demo_reader_keeps_the_example_reader_behaviour(now: datetime):
    assert isinstance(build_demo_app_reader(now), ExampleAppReader)
    with pytest.raises(UpstreamError) as caught:
        build_demo_app_reader(now).get_history(AST, now, now - timedelta(days=1))
    assert caught.value.status == 422


def test_simulation_reference_is_the_latest_assessment_and_the_scenario_is_hypothetical(now: datetime):
    response = build_demo_simulator(now).simulate(_request(now))
    latest = build_demo_app_reader(now).get_status(AST).latest_assessment
    assert response.reference == latest
    assert response.hypothetical is True
    assert response.scenario.risk_score > response.reference.risk_score
    assert response.scenario.assessment_id != response.reference.assessment_id
    unchanged = build_demo_simulator(now).simulate(_request(now).model_copy(update={"overrides": []}))
    assert unchanged.scenario.risk_score == unchanged.reference.risk_score


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _server(now: datetime) -> MCPServer:
    return create_server(build_demo_app_reader(now), build_demo_simulator(now), build_demo_assess_base(now))


@pytest.mark.integration
@pytest.mark.anyio
async def test_compare_periods_between_last_week_and_the_week_before_has_non_null_deltas(now: datetime):
    arguments = {
        "astronaut_id": AST,
        "a_start": (now - 2 * WEEK).isoformat(),
        "a_end": (now - WEEK).isoformat(),
        "b_start": (now - WEEK).isoformat(),
        "b_end": now.isoformat(),
    }
    async with Client(_server(now)) as client:
        result = await client.call_tool("compare_periods", arguments)
    assert not result.is_error, result.content
    data = result.structured_content
    assert data is not None
    validate("mcp/compare-periods", data)
    assert data["warnings"] == []
    assert data["features"]
    assert all(f["delta_mean"] is not None and f["delta_median"] is not None for f in data["features"])
    assert data["scores"]["risk_score"]["delta"] > 0
    assert data["levels"]["a"]["latest"] == "green" and data["levels"]["b"]["latest"] == "amber"


@pytest.mark.integration
@pytest.mark.anyio
async def test_tools_answer_over_the_demo_data_and_unknown_astronaut_is_a_tool_error(now: datetime):
    window = {"start": (now - WEEK).isoformat(), "end": now.isoformat()}
    async with Client(_server(now)) as client:
        history = await client.call_tool("get_history", {"astronaut_id": AST, **window})
        missing = await client.call_tool("get_current_status", {"astronaut_id": "AST-99"})
        simulated = await client.call_tool("simulate_scenario", {"astronaut_id": AST, "overrides": [OVERRIDE]})
    assert history.structured_content is not None and history.structured_content["assessments"]
    assert missing.is_error
    assert not simulated.is_error, simulated.content


def test_flags_default_to_the_demo_dataset_and_wall_clock(now: datetime):
    args = parse_args([])
    assert (args.dataset, args.now) == ("demo", None)


def test_flags_pick_dataset_and_pin_the_clock(now: datetime):
    args = parse_args(["--dataset", "examples", "--now", "2026-11-13T12:00:00Z"])
    assert args.dataset == "examples"
    assert args.now == NOW


def test_flags_reject_unknown_dataset_and_naive_or_malformed_clock(now: datetime, capsys: pytest.CaptureFixture[str]):
    for argv in (["--dataset", "other"], ["--now", "2026-11-13T12:00:00"], ["--now", "yesterday"]):
        with pytest.raises(SystemExit):
            parse_args(argv)
    assert "error" in capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.anyio
async def test_build_server_serves_the_pinned_demo_clock_or_the_examples(now: datetime):
    async with Client(build_server(["--now", now.isoformat()])) as client:
        demo = await client.call_tool("get_current_status", {"astronaut_id": AST})
    async with Client(build_server(["--dataset", "examples"])) as client:
        examples = await client.call_tool("get_current_status", {"astronaut_id": AST})
    assert demo.structured_content is not None and examples.structured_content is not None
    assert demo.structured_content["latest_assessment"]["as_of"].startswith(now.strftime("%Y-%m-%dT%H:00:00"))
    assert examples.structured_content["latest_assessment"]["assessment_id"] == "a55e55e0-0000-4000-8000-0000000000a1"


def test_build_server_without_pinned_clock_uses_the_wall_clock(now: datetime, monkeypatch: pytest.MonkeyPatch):
    seen: list[datetime] = []
    real = build_demo_app_reader

    def spy(clock: datetime) -> ExampleAppReader:
        seen.append(clock)
        return real(clock)

    monkeypatch.setattr("spine_mcp_server.build_demo_app_reader", spy)
    before = datetime.now(UTC)
    build_server([])
    assert before <= seen[0] <= datetime.now(UTC)


def test_main_runs_the_server_over_stdio(now: datetime, monkeypatch: pytest.MonkeyPatch):
    ran: list[str] = []
    monkeypatch.setattr(MCPServer, "run", lambda self, transport: ran.append(transport))
    main(["--now", now.isoformat()])
    assert ran == ["stdio"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_stdio_server_serves_the_demo_dataset_at_the_pinned_clock(now: datetime):
    params = StdioServerParameters(command=sys.executable, args=["-m", "spine_mcp_server", "--now", now.isoformat()])
    with anyio.fail_after(TIMEOUT_SECONDS), open(os.devnull, "w") as errlog:
        async with Client(stdio_client(params, errlog=errlog)) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            result = await client.call_tool("get_current_status", {"astronaut_id": AST})
    assert len(tools) == 7
    assert result.structured_content is not None
    validate("app/status", result.structured_content)
    assert result.structured_content["latest_assessment"]["level"] == "amber"
    assert result.structured_content["latest_assessment"]["as_of"].startswith(now.strftime("%Y-%m-%dT%H:00:00"))
