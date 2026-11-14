"""Interactive chat CLI: `spine-agent --astronaut AST-01` starts a multi-turn REPL over a ChatSession."""

import argparse
import asyncio
import os
import sys
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol

import pydantic_ai
from pydantic_ai.models import Model
from pydantic_ai.toolsets import AbstractToolset

from spine_agent.agent import AgentDeps, ChatSession, ReplyChunk, build_agent
from spine_agent.fallback import McpStatusSource
from spine_agent.settings import AgentSettings

EXIT_COMMANDS = frozenset({"/exit", "/quit"})
FALLBACK_MARKER = "(LLM unavailable: automatic summary)"

type ReadLine = Callable[[], Awaitable[str | None]]
type Write = Callable[[str], None]


class Session(Protocol):
    """What the REPL needs from a ChatSession."""

    def ask(self, question: str) -> AsyncIterator[ReplyChunk]: ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spine-agent", description="Chat with the local spine-health assistant.")
    parser.add_argument("--astronaut", required=True, help="astronaut id to talk about, e.g. AST-01")
    parser.add_argument("--model", default=None, help="model name, overriding SPINE_AGENT_MODEL")
    return parser


class _Renderer:
    """Writes chunks; tracks whether the cursor is at the start of a line so each block lands on its own line."""

    def __init__(self, write: Write) -> None:
        self._write = write
        self._at_line_start = True

    def _emit(self, text: str) -> None:
        self._write(text)
        self._at_line_start = text.endswith("\n")

    def _own_line(self) -> None:
        if not self._at_line_start:
            self._emit("\n")

    def render(self, chunk: ReplyChunk) -> None:
        match chunk.kind:
            case "text":
                self._emit(chunk.text)
            case "tool_call":
                self._own_line()
                args = ", ".join(f"{key}={value!r}" for key, value in (chunk.tool_args or {}).items())
                self._emit(f"  [tool] {chunk.tool_name}({args})\n")
            case "fallback":
                self._own_line()
                self._emit(f"{FALLBACK_MARKER}\n")
                self._emit(chunk.text)
            case _:  # "notice"
                self._own_line()
                self._emit(f"[!] {chunk.text}\n")

    def end_reply(self) -> None:
        self._own_line()


async def run_repl(session: Session, read_line: ReadLine, write: Write) -> None:
    """Read lines and stream the reply to each, until /exit, /quit or EOF (read_line returns None)."""
    renderer = _Renderer(write)
    while (line := await read_line()) is not None:
        question = line.strip()
        if not question:
            continue
        if question in EXIT_COMMANDS:
            return
        async for chunk in session.ask(question):
            renderer.render(chunk)
        renderer.end_reply()


async def read_stdin_line() -> str | None:
    """Next line from stdin without blocking the loop; None at EOF.

    A daemon thread rather than asyncio.to_thread: on Ctrl-C the default executor would wait for the blocked
    input() to return and hang the exit.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str | None] = loop.create_future()

    def deliver(value: str | None) -> None:
        if not future.done():  # the waiter was cancelled (Ctrl-C) before the thread returned
            future.set_result(value)

    def read() -> None:
        try:
            value = input("> ")
        except EOFError:
            value = None
        loop.call_soon_threadsafe(deliver, value)

    threading.Thread(target=read, daemon=True).start()
    return await future


def _write_stdout(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


async def amain(
    argv: Sequence[str] | None = None,
    *,
    model: Model | None = None,
    toolsets: Sequence[AbstractToolset[AgentDeps]] | None = None,
    read_line: ReadLine = read_stdin_line,
    write: Write = _write_stdout,
) -> int:
    """Run the chat. `model`, `toolsets`, `read_line` and `write` are injectable for tests; returns the exit code."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exit_request:  # argparse already printed the error
        return int(exit_request.code or 0)
    pydantic_ai.BANNER_ENABLED = False  # the chat owns the terminal; set here, not at import time
    settings = AgentSettings()
    if args.model:
        settings.model = args.model
    # The server's stderr would interleave with the chat: discard it unless a log file is configured.
    agent = build_agent(settings, model=model, toolsets=toolsets, log_file=settings.mcp_log_file or Path(os.devnull))
    write(f"Spine assistant for {args.astronaut} (model: {settings.model}). Type /exit to quit.\n")
    async with ChatSession(agent, McpStatusSource(settings), args.astronaut) as session:
        await run_repl(session, read_line, write)
    return 0
