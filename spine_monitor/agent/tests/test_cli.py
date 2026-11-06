import asyncio
import io
import sys
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from types import TracebackType
from typing import Any, Self

import pydantic_ai
import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from spine_agent import cli
from spine_agent.agent import ChatSession, ReplyChunk, build_agent
from spine_agent.cli import amain, build_parser, run_repl
from spine_agent.settings import AgentSettings

# ---------------------------------------------------------------- hand-written fakes


class FakeSession:
    """Scripted ChatSession: each ask() replays the next list of chunks and records the question."""

    def __init__(self, *replies: Sequence[ReplyChunk]) -> None:
        self._replies = list(replies)
        self.questions: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        return None

    async def ask(self, question: str) -> AsyncIterator[ReplyChunk]:
        self.questions.append(question)
        for chunk in self._replies.pop(0):
            yield chunk


def lines(*items: str) -> Callable[[], Awaitable[str | None]]:
    """A read_line that returns the given lines, then None (EOF)."""
    pending = list(items)

    async def read_line() -> str | None:
        return pending.pop(0) if pending else None

    return read_line


class Out:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def __call__(self, text: str) -> None:
        self.parts.append(text)

    @property
    def text(self) -> str:
        return "".join(self.parts)


async def _run(session: Any, read_line: Callable[[], Awaitable[str | None]]) -> Out:
    out = Out()
    await run_repl(session, read_line, out)
    return out


# ---------------------------------------------------------------- run_repl rendering


@pytest.mark.unit
@pytest.mark.anyio
async def test_text_deltas_are_written_in_order_without_newlines_then_one_newline_at_the_end():
    session = FakeSession([ReplyChunk("text", "You are "), ReplyChunk("text", "green.")])
    out = await _run(session, lines("how am I?"))
    assert out.parts[:2] == ["You are ", "green."]
    assert out.text == "You are green.\n"
    assert session.questions == ["how am I?"]


@pytest.mark.unit
@pytest.mark.anyio
async def test_tool_call_goes_on_its_own_line_between_text():
    session = FakeSession(
        [
            ReplyChunk("text", "Checking"),
            ReplyChunk("tool_call", tool_name="get_current_status", tool_args={"astronaut_id": "AST-01"}),
            ReplyChunk("text", "Done."),
        ]
    )
    out = await _run(session, lines("hi"))
    assert out.text == "Checking\n  [tool] get_current_status(astronaut_id='AST-01')\nDone.\n"


@pytest.mark.unit
@pytest.mark.anyio
async def test_tool_call_without_args_at_line_start_adds_no_blank_line():
    session = FakeSession([ReplyChunk("tool_call", tool_name="list_astronauts")])
    out = await _run(session, lines("hi"))
    assert out.text == "  [tool] list_astronauts()\n"


@pytest.mark.unit
@pytest.mark.anyio
async def test_fallback_is_printed_in_full_after_the_marker_line():
    session = FakeSession([ReplyChunk("fallback", "Level: amber\nSee the flight surgeon.")])
    out = await _run(session, lines("hi"))
    assert out.text == "(LLM unavailable: automatic summary)\nLevel: amber\nSee the flight surgeon.\n"


@pytest.mark.unit
@pytest.mark.anyio
async def test_notice_is_on_its_own_line_with_the_prefix():
    session = FakeSession([ReplyChunk("text", "partial"), ReplyChunk("notice", "Correction: no diagnosis.")])
    out = await _run(session, lines("hi"))
    assert out.text == "partial\n[!] Correction: no diagnosis.\n"


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_text_ending_in_a_newline_does_not_get_a_blank_line():
    session = FakeSession([ReplyChunk("text", "line\n")])
    out = await _run(session, lines("hi"))
    assert out.text == "line\n"


# ---------------------------------------------------------------- run_repl loop control


@pytest.mark.unit
@pytest.mark.anyio
async def test_blank_lines_are_ignored_and_input_is_stripped():
    session = FakeSession([ReplyChunk("text", "ok")])
    await _run(session, lines("", "   ", "  hello  "))
    assert session.questions == ["hello"]


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize("command", ["/exit", "/quit", "  /exit  "])
async def test_exit_commands_end_the_loop_before_asking_anything(command: str):
    session = FakeSession([ReplyChunk("text", "never")])
    await _run(session, lines(command, "hello"))
    assert session.questions == []


@pytest.mark.unit
@pytest.mark.anyio
async def test_eof_ends_the_loop():
    session = FakeSession([ReplyChunk("text", "a")], [ReplyChunk("text", "b")])
    await _run(session, lines("one", "two"))
    assert session.questions == ["one", "two"]


# ---------------------------------------------------------------- argparse


@pytest.mark.unit
def test_astronaut_is_required(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([])
    assert exit_info.value.code == 2
    assert "--astronaut" in capsys.readouterr().err


@pytest.mark.unit
def test_model_defaults_to_none_and_can_be_overridden():
    assert build_parser().parse_args(["--astronaut", "AST-01"]).model is None
    args = build_parser().parse_args(["--astronaut", "AST-01", "--model", "gemma4:e4b"])
    assert (args.astronaut, args.model) == ("AST-01", "gemma4:e4b")


# ---------------------------------------------------------------- amain wiring


def _echo_model(seen: list[str]) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        last = messages[-1]
        assert isinstance(last, ModelRequest)
        prompt = next(p.content for p in last.parts if isinstance(p, UserPromptPart))
        seen.append(str(prompt))
        yield "echo: "
        yield str(prompt)

    return FunctionModel(stream_function=stream)


@pytest.mark.unit
@pytest.mark.anyio
async def test_amain_prints_the_banner_streams_the_reply_and_exits_0(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pydantic_ai, "BANNER_ENABLED", True)
    seen: list[str] = []
    out = Out()
    code = await amain(
        ["--astronaut", "AST-01", "--model", "gemma4:e4b"],
        model=_echo_model(seen),
        toolsets=[],
        read_line=lines("hello", "/exit"),
        write=out,
    )
    assert code == 0
    assert seen == ["hello"]
    first_line = out.text.splitlines()[0]
    assert "AST-01" in first_line
    assert "gemma4:e4b" in first_line  # the --model override wins over the settings
    assert "Type /exit to quit." in out.text
    assert "echo: hello\n" in out.text
    assert pydantic_ai.BANNER_ENABLED is False


@pytest.mark.unit
@pytest.mark.anyio
async def test_amain_banner_uses_the_settings_model_without_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPINE_AGENT_MODEL", "from-env:1b")
    out = Out()
    await amain(["--astronaut", "AST-02"], model=_echo_model([]), toolsets=[], read_line=lines(), write=out)
    assert "from-env:1b" in out.text.splitlines()[0]


@pytest.mark.unit
@pytest.mark.anyio
async def test_amain_returns_2_on_bad_arguments(capsys: pytest.CaptureFixture[str]):
    assert await amain([], read_line=lines(), write=Out()) == 2
    assert "--astronaut" in capsys.readouterr().err


@pytest.mark.unit
@pytest.mark.anyio
async def test_amain_defaults_write_to_flushed_stdout_and_read_to_stdin(monkeypatch: pytest.MonkeyPatch):
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello\n"))  # then EOF
    code = await amain(["--astronaut", "AST-01"], model=_echo_model([]), toolsets=[])
    assert code == 0
    assert "echo: hello" in stdout.getvalue()


@pytest.mark.unit
@pytest.mark.anyio
async def test_stdin_reader_returns_none_at_eof(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert await cli.read_stdin_line() is None


# ---------------------------------------------------------------- integration: real ChatSession, real MCP server


class _NoStatus:
    async def current_status(self, astronaut_id: str) -> None:
        return None

    async def explanation(self, assessment_id: str) -> None:
        return None


@pytest.mark.integration
@pytest.mark.anyio
async def test_multi_turn_repl_passes_the_history_to_the_second_question():
    """The second answer is built from the first question, so it only works if ChatSession kept the history."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        prompts = [p.content for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)]
        yield f"seen {len(prompts)}: " + " | ".join(str(p) for p in prompts)

    settings = AgentSettings(mcp_command=sys.executable, mcp_args=["-m", "spine_mcp_server"])
    agent = build_agent(settings, model=FunctionModel(stream_function=stream))
    out = Out()
    async with ChatSession(agent, _NoStatus(), "AST-01") as session:
        await run_repl(session, lines("first", "second", "/quit"), out)
    assert "seen 1: first\n" in out.text
    assert "seen 2: first | second\n" in out.text


class _BlockedStdin(io.StringIO):
    """stdin whose readline blocks until released, like a terminal waiting for the user."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()
        self.returned = threading.Event()

    def readline(self, size: int = -1) -> str:
        self.release.wait(timeout=5)
        self.returned.set()
        return "late\n"


@pytest.mark.unit
@pytest.mark.anyio
async def test_cancelling_a_pending_read_does_not_break_when_the_thread_returns_later(monkeypatch: pytest.MonkeyPatch):
    stdin = _BlockedStdin()
    monkeypatch.setattr(sys, "stdin", stdin)
    task = asyncio.create_task(cli.read_stdin_line())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    stdin.release.set()
    assert stdin.returned.wait(timeout=5)
    await asyncio.sleep(0.05)  # let the late delivery run on the loop: it must be a silent no-op
