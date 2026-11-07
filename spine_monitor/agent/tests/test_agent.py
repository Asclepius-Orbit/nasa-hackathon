import asyncio
import json
import re
import sys
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelConnectionError, ModelHTTPError, ModelTimeoutError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from spine_agent.agent import AgentDeps, ChatSession, ReplyChunk, build_agent
from spine_agent.fallback import McpStatusSource
from spine_agent.guards import DISCLAIMER
from spine_agent.prompts import SYSTEM_PROMPT, astronaut_instruction
from spine_agent.settings import AgentSettings

ASSESSMENT_ID = "a55e55e0-0000-4000-8000-0000000000a1"
MID_STREAM_NOTICE = "The assistant failed mid-answer; falling back to the automatic summary."
CORRECTION_NOTICE = (
    "Correction: the previous answer contained wording that sounds like a medical diagnosis. This assistant cannot diagnose; please consult the flight surgeon."
)

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)

type Script = Callable[[list[ModelMessage], AgentInfo], AsyncIterator[str | DeltaToolCalls]]


# ---------------------------------------------------------------- fakes at the boundary (the LLM and the status source)


class FakeStatusSource:
    """Hand-written StatusSource: canned data, None means no data. Records what it was asked."""

    def __init__(self, statuses: dict[str, dict[str, Any]] | None = None, explanations: dict[str, dict[str, Any]] | None = None) -> None:
        self._statuses = statuses or {}
        self._explanations = explanations or {}
        self.asked: list[str] = []

    async def current_status(self, astronaut_id: str) -> dict[str, Any] | None:
        self.asked.append(f"status:{astronaut_id}")
        return self._statuses.get(astronaut_id)

    async def explanation(self, assessment_id: str) -> dict[str, Any] | None:
        self.asked.append(f"explanation:{assessment_id}")
        return self._explanations.get(assessment_id)


AMBER_STATUS: dict[str, Any] = {
    "astronaut_id": "AST-01",
    "latest_assessment": {
        "assessment_id": ASSESSMENT_ID,
        "level": "amber",
        "as_of": "2027-02-09T09:00:00Z",
        "drivers": [{"feature_id": "stretch.lumbar.peak_elongation_mm", "value": 9.1, "unit": "mm"}],
    },
}
AMBER_EXPLANATION: dict[str, Any] = {"assessment": {"fired_rules": [{"rule_id": "RULE_SYMPTOM_PAIN_TREND", "description": "pain trend"}]}}


def _tool_returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [part for m in messages if isinstance(m, ModelRequest) for part in m.parts if isinstance(part, ToolReturnPart)]


def _call(name: str, args: dict[str, Any], call_id: str) -> DeltaToolCalls:
    return {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=call_id)}


def amber_script() -> Script:
    """Scripted LLM: get_current_status, then explain_assessment, then a streamed answer built from the real tool output."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        returns = _tool_returns(messages[-1:])
        if not returns:
            yield _call("get_current_status", {"astronaut_id": "AST-01"}, "c1")
        elif returns[0].tool_name == "get_current_status":
            yield _call("explain_assessment", {"assessment_id": ASSESSMENT_ID}, "c2")
        else:
            status = next(r for r in _tool_returns(messages) if r.tool_name == "get_current_status")
            level = "amber" if '"amber"' in status.model_response_str() else "unknown"
            yield "You are "
            yield f"{level} because "
            yield "lumbar elongation is 9.1 mm."

    return stream


def text_script(*chunks: str) -> Script:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        for chunk in chunks:
            yield chunk

    return stream


def failing_script(error: Exception, *, after: tuple[str, ...] = ()) -> Script:
    """Scripted LLM that streams `after` and then fails like a dead Ollama would."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        for chunk in after:
            yield chunk
        raise error

    return stream


def _settings(**overrides: Any) -> AgentSettings:
    return AgentSettings(**{"mcp_command": sys.executable, "mcp_args": ["-m", "spine_mcp_server", "--dataset", "examples"], **overrides})


async def _collect(session: ChatSession, question: str) -> list[ReplyChunk]:
    return [chunk async for chunk in session.ask(question)]


def _text(chunks: list[ReplyChunk]) -> str:
    return "".join(c.text for c in chunks if c.kind == "text")


# ---------------------------------------------------------------- build_agent wiring


@pytest.mark.unit
def test_build_agent_defaults_to_ollama_with_settings():
    agent = build_agent(_settings(ollama_base_url="http://gpu:1234/v1", model="gemma4:e4b", temperature=0.4, request_timeout_s=12.0))
    model = agent.model
    assert isinstance(model, OllamaModel)
    assert model.model_name == "gemma4:e4b"
    assert model.base_url.rstrip("/") == "http://gpu:1234/v1"
    assert agent.model_settings == {"temperature": 0.4, "timeout": 12.0, "openai_reasoning_effort": "none"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_thinking_is_disabled_on_the_wire_for_qwen():
    """Ollama maps reasoning_effort "none" to think=false; the unified `thinking=False` would be dropped for the qwen profile."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "qwen3.5:4b",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
            },
        )

    settings = _settings(model="qwen3.5:4b", temperature=0.3)
    agent = build_agent(settings, toolsets=[])
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http_client:
        wire_model = OllamaModel(settings.model, provider=OllamaProvider(base_url="http://ollama.test/v1", http_client=http_client))
        with override_allow_model_requests(True):  # MockTransport: no socket is opened
            result = await agent.run("hi", model=wire_model, deps=AgentDeps("AST-01", NOW))
    assert result.output == "ok"
    assert bodies[0]["reasoning_effort"] == "none"
    assert bodies[0]["temperature"] == 0.3
    assert bodies[0]["model"] == "qwen3.5:4b"


@pytest.mark.unit
@pytest.mark.anyio
async def test_instructions_are_the_static_prompt_plus_the_session_astronaut():
    seen: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(info)
        yield "ok"

    agent = build_agent(_settings(), model=FunctionModel(stream_function=stream), toolsets=[])
    async with ChatSession(agent, FakeStatusSource(), "AST-07", clock=lambda: NOW) as session:
        await _collect(session, "hello")
    assert seen[0].instructions is not None
    assert SYSTEM_PROMPT.strip() in seen[0].instructions
    assert astronaut_instruction("AST-07", NOW) in seen[0].instructions


@pytest.mark.unit
@pytest.mark.anyio
async def test_each_turn_carries_the_time_of_that_turn():
    seen: list[str] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(info.instructions or "")
        yield "ok"

    times = iter([NOW, NOW + timedelta(days=1)])
    agent = build_agent(_settings(), model=FunctionModel(stream_function=stream), toolsets=[])
    async with ChatSession(agent, FakeStatusSource(), "AST-01", clock=lambda: next(times)) as session:
        await _collect(session, "first")
        await _collect(session, "second")
    # El rango "últimas 24 h" del segundo turno empieza en el instante del primero: se comprueba la frase de la hora actual.
    assert "Current date and time (UTC): 2026-11-13T12:00:00Z" in seen[0]
    assert "Current date and time (UTC): 2026-11-14T12:00:00Z" in seen[1]
    assert "Current date and time (UTC): 2026-11-13T12:00:00Z" not in seen[1]


@pytest.mark.unit
@pytest.mark.anyio
async def test_the_default_clock_is_the_utc_wall_clock():
    seen: list[str] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(info.instructions or "")
        yield "ok"

    agent = build_agent(_settings(), model=FunctionModel(stream_function=stream), toolsets=[])
    before = datetime.now(UTC).replace(microsecond=0)
    async with ChatSession(agent, FakeStatusSource(), "AST-01") as session:
        await _collect(session, "hello")
    match = re.search(r"Current date and time \(UTC\): (\S+Z)\.", seen[0])
    assert match is not None
    stamped = datetime.fromisoformat(match.group(1))
    assert before <= stamped <= datetime.now(UTC)


# ---------------------------------------------------------------- happy path against the real MCP server


@pytest.mark.integration
@pytest.mark.anyio
async def test_happy_path_streams_tool_calls_then_text_and_updates_history():
    agent = build_agent(_settings(), model=FunctionModel(stream_function=amber_script()))
    async with ChatSession(agent, FakeStatusSource(), "AST-01") as session:
        chunks = await _collect(session, "Why am I amber?")
        history = list(session.history)

    assert [c.kind for c in chunks] == ["tool_call", "tool_call", "text", "text", "text"]
    assert chunks[0] == ReplyChunk("tool_call", tool_name="get_current_status", tool_args={"astronaut_id": "AST-01"})
    assert chunks[1] == ReplyChunk("tool_call", tool_name="explain_assessment", tool_args={"assessment_id": ASSESSMENT_ID})
    assert _text(chunks) == "You are amber because lumbar elongation is 9.1 mm."  # "amber" comes from the real mock data
    assert len(history) == 6  # request, tool call, tool return, tool call, tool return, answer
    explanation_return = _tool_returns(history)[1]
    assert explanation_return.tool_name == "explain_assessment"
    assert "RULE_SYMPTOM_PAIN_TREND" in explanation_return.model_response_str()


@pytest.mark.integration
@pytest.mark.anyio
async def test_second_turn_sees_the_history():
    prompts_seen: list[list[str]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        prompts_seen.append([str(p.content) for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)])
        yield f"answer {len(prompts_seen)}"

    agent = build_agent(_settings(), model=FunctionModel(stream_function=stream))
    async with ChatSession(agent, FakeStatusSource(), "AST-01") as session:
        await _collect(session, "first")
        second = await _collect(session, "second")
        history_len = len(session.history)

    assert _text(second) == "answer 2"
    assert prompts_seen[1] == ["first", "second"]
    assert history_len == 4


@pytest.mark.integration
@pytest.mark.anyio
async def test_tool_error_for_unknown_astronaut_reaches_the_model_as_a_failure():
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        returns = _tool_returns(messages[-1:])
        if not returns:
            yield _call("get_current_status", {"astronaut_id": "AST-99"}, "c1")
        else:
            yield "I don't know: " + ("not found" if "not found" in returns[0].model_response_str().lower() else "??")

    agent = build_agent(_settings(), model=FunctionModel(stream_function=stream))
    async with ChatSession(agent, FakeStatusSource(), "AST-99") as session:
        chunks = await _collect(session, "status?")
    assert [c.kind for c in chunks] == ["tool_call", "text"]
    assert _text(chunks) == "I don't know: not found"


@pytest.mark.integration
@pytest.mark.anyio
async def test_no_subprocess_is_left_running_after_the_session(tmp_path: Path, pid_wrapper_args: Callable[[Path], list[str]], pid_alive: Callable[[int], bool]):
    pid_file = tmp_path / "server.pid"
    agent = build_agent(_settings(mcp_args=pid_wrapper_args(pid_file)), model=FunctionModel(stream_function=amber_script()))
    async with ChatSession(agent, FakeStatusSource(), "AST-01") as session:
        await _collect(session, "Why am I amber?")
        pid = int(pid_file.read_text())
        assert pid_alive(pid)
    assert not pid_alive(pid)


@pytest.mark.integration
@pytest.mark.anyio
async def test_server_stderr_goes_to_the_log_file_not_the_console(tmp_path: Path, capfd: pytest.CaptureFixture[str], noisy_server_args: list[str]):
    log = tmp_path / "mcp.log"
    agent = build_agent(_settings(mcp_args=noisy_server_args), model=FunctionModel(stream_function=amber_script()), log_file=log)
    async with ChatSession(agent, FakeStatusSource(), "AST-01") as session:
        await _collect(session, "Why am I amber?")
    assert "MCP-SERVER-LOG-LINE" in log.read_text()
    assert "MCP-SERVER-LOG-LINE" not in capfd.readouterr().err


@pytest.mark.integration
@pytest.mark.anyio
async def test_server_stderr_defaults_to_the_console(capfd: pytest.CaptureFixture[str], noisy_server_args: list[str]):
    agent = build_agent(_settings(mcp_args=noisy_server_args), model=FunctionModel(stream_function=amber_script()))
    async with ChatSession(agent, FakeStatusSource(), "AST-01") as session:
        await _collect(session, "Why am I amber?")
    assert "MCP-SERVER-LOG-LINE" in capfd.readouterr().err


# ---------------------------------------------------------------- degradation (no MCP subprocess: toolsets=[])


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        ModelConnectionError("qwen3.5:4b", "Connection error."),
        ModelTimeoutError("qwen3.5:4b", "Request timed out."),
        ModelHTTPError(status_code=500, model_name="qwen3.5:4b", body="boom"),
        UnexpectedModelBehavior("garbage"),
    ],
    ids=["connect", "timeout", "http", "unexpected"],
)
async def test_model_failure_yields_the_fallback_without_raising(error: Exception):
    source = FakeStatusSource({"AST-01": AMBER_STATUS}, {ASSESSMENT_ID: AMBER_EXPLANATION})
    agent = build_agent(_settings(), model=FunctionModel(stream_function=failing_script(error)), toolsets=[])
    async with ChatSession(agent, source, "AST-01") as session:
        chunks = await _collect(session, "Why am I amber?")
        assert session.history == []

    assert [c.kind for c in chunks] == ["fallback"]
    assert "AMBER" in chunks[0].text
    assert "RULE_SYMPTOM_PAIN_TREND" in chunks[0].text
    assert DISCLAIMER in chunks[0].text
    assert source.asked == ["status:AST-01", f"explanation:{ASSESSMENT_ID}"]


def _refuse(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("Connection refused", request=request)


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize(
    "respond",
    [
        _refuse,
        lambda request: httpx2.Response(500, json={"error": "model runner crashed"}),
        lambda request: httpx2.Response(404, json={"error": "model 'qwen3.5:4b' not found"}),
    ],
    ids=["refused", "http-500", "model-missing"],
)
async def test_real_ollama_model_transport_failure_yields_the_fallback(respond: Callable[[httpx2.Request], httpx2.Response]):
    """The real OllamaModel against a dead or broken Ollama (faked at the HTTP transport): degrades, never raises."""
    settings = _settings()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        model = OllamaModel(settings.model, provider=OllamaProvider(base_url="http://ollama.test/v1", http_client=http_client))
        agent = build_agent(settings, model=model, toolsets=[])
        with override_allow_model_requests(True):  # MockTransport: no socket is opened
            async with ChatSession(agent, FakeStatusSource({"AST-01": AMBER_STATUS}), "AST-01") as session:
                chunks = await _collect(session, "Why am I amber?")
    assert [c.kind for c in chunks] == ["fallback"]
    assert DISCLAIMER in chunks[0].text


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize("chunks", [("",), ("  ", "\n")], ids=["empty", "blank"])  # FunctionModel needs one item
async def test_empty_answer_yields_the_fallback(chunks: tuple[str, ...]):
    agent = build_agent(_settings(), model=FunctionModel(stream_function=text_script(*chunks)), toolsets=[])
    async with ChatSession(agent, FakeStatusSource({"AST-01": AMBER_STATUS}), "AST-01") as session:
        replies = await _collect(session, "Why am I amber?")
        assert session.history == []
    assert replies[-1].kind == "fallback"
    assert "AMBER" in replies[-1].text
    assert "notice" not in [c.kind for c in replies]


@pytest.mark.unit
@pytest.mark.anyio
async def test_failure_mid_stream_yields_a_notice_then_the_fallback():
    script = failing_script(ModelConnectionError("qwen3.5:4b", "reset", in_stream=True), after=("You are ", "amb"))
    agent = build_agent(_settings(), model=FunctionModel(stream_function=script), toolsets=[])
    async with ChatSession(agent, FakeStatusSource({"AST-01": AMBER_STATUS}), "AST-01") as session:
        chunks = await _collect(session, "Why am I amber?")
    assert [c.kind for c in chunks] == ["text", "text", "notice", "fallback"]
    assert chunks[2].text == MID_STREAM_NOTICE


@pytest.mark.unit
@pytest.mark.anyio
async def test_failed_turn_keeps_the_previous_history():
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ModelConnectionError("qwen3.5:4b", "refused")
        yield "fine"

    agent = build_agent(_settings(), model=FunctionModel(stream_function=stream), toolsets=[])
    async with ChatSession(agent, FakeStatusSource(), "AST-01") as session:
        await _collect(session, "one")
        before = list(session.history)
        await _collect(session, "two")
        assert session.history == before
        assert len(before) == 2


@pytest.mark.unit
@pytest.mark.anyio
async def test_fallback_without_assessment_does_not_ask_for_an_explanation():
    source = FakeStatusSource({"AST-02": {"astronaut_id": "AST-02", "latest_assessment": None}})
    agent = build_agent(_settings(), model=FunctionModel(stream_function=failing_script(ModelConnectionError("qwen3.5:4b", "x"))), toolsets=[])
    async with ChatSession(agent, source, "AST-02") as session:
        chunks = await _collect(session, "status?")
    assert source.asked == ["status:AST-02"]
    assert "no assessment" in chunks[-1].text.lower()


@pytest.mark.integration
@pytest.mark.anyio
async def test_unknown_astronaut_fallback_says_i_dont_know_from_the_real_server():
    settings = _settings()
    agent = build_agent(settings, model=FunctionModel(stream_function=failing_script(ModelConnectionError("qwen3.5:4b", "refused"))), toolsets=[])
    async with ChatSession(agent, McpStatusSource(settings), "AST-99") as session:
        chunks = await _collect(session, "Why am I amber?")
    assert [c.kind for c in chunks] == ["fallback"]
    assert "I don't know" in chunks[0].text
    assert not any(ch.isdigit() for ch in chunks[0].text)


# ---------------------------------------------------------------- post-filter and cancellation


@pytest.mark.unit
@pytest.mark.anyio
async def test_diagnostic_wording_in_the_answer_adds_a_correction_notice():
    agent = build_agent(_settings(), model=FunctionModel(stream_function=text_script("You have a ", "herniated disc.")), toolsets=[])
    async with ChatSession(agent, FakeStatusSource(), "AST-01") as session:
        chunks = await _collect(session, "Do I have a herniated disc?")
        assert len(session.history) == 2
    assert [c.kind for c in chunks] == ["text", "text", "notice"]
    assert chunks[-1].text == CORRECTION_NOTICE


@pytest.mark.unit
@pytest.mark.anyio
async def test_clean_answer_has_no_notice():
    agent = build_agent(_settings(), model=FunctionModel(stream_function=text_script("I can't diagnose; ask the flight surgeon.")), toolsets=[])
    async with ChatSession(agent, FakeStatusSource(), "AST-01") as session:
        chunks = await _collect(session, "Do I have a herniated disc?")
    assert [c.kind for c in chunks] == ["text"]


@pytest.mark.unit
@pytest.mark.anyio
async def test_cancellation_propagates_and_leaves_history_untouched():
    started = asyncio.Event()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "partial"
        started.set()
        await asyncio.Event().wait()  # hangs like a stalled model until cancelled
        yield "never"

    source = FakeStatusSource({"AST-01": AMBER_STATUS})
    agent = build_agent(_settings(), model=FunctionModel(stream_function=stream), toolsets=[])
    async with ChatSession(agent, source, "AST-01") as session:
        task = asyncio.create_task(_collect(session, "Why am I amber?"))
        await asyncio.wait_for(started.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert session.history == []
    assert source.asked == []


@pytest.mark.unit
def test_reply_chunk_defaults():
    chunk = ReplyChunk("text", "hi")
    assert (chunk.tool_name, chunk.tool_args) == (None, None)
    assert isinstance(build_agent(_settings(), toolsets=[]), Agent)
