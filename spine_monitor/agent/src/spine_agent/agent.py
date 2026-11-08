"""The agent core: a pydantic-ai Agent over a local Ollama model with the MCP tools, and a chat session that streams
replies and degrades to a deterministic summary when the model fails.

Guardrails here are code, not prompt: the empty-answer and failure fallback, and the post-filter that appends a
correction notice when the streamed answer sounds like a diagnosis (it cannot unsay text already shown).
"""

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self

from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent, AgentRunResultEvent, FunctionToolCallEvent, PartDeltaEvent, PartStartEvent, RunContext
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, TextPart, TextPartDelta
from pydantic_ai.models import Model
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.toolsets import AbstractToolset

from spine_agent.guards import fallback_reply, find_diagnostic_terms
from spine_agent.ports import StatusSource
from spine_agent.prompts import SYSTEM_PROMPT, astronaut_instruction
from spine_agent.settings import AgentSettings

MID_STREAM_NOTICE = "The assistant failed mid-answer; falling back to the automatic summary."
CORRECTION_NOTICE = (
    "Correction: the previous answer contained wording that sounds like a medical diagnosis. This assistant cannot diagnose; please consult the flight surgeon."
)

# Failures of the model or its transport that degrade to the fallback instead of raising. The OpenAI-compatible model
# maps connection refused, timeouts and HTTP status errors to ModelAPIError subclasses; ConnectionError/TimeoutError
# cover OS-level failures and RunTimedOut. Programming errors are deliberately not here: they must surface.
MODEL_FAILURES: tuple[type[Exception], ...] = (
    ModelAPIError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    ConnectionError,
    TimeoutError,
)

# Handshake timeout for the MCP server subprocess. A cold start in the gate takes 5-8.5 s, over pydantic-ai's 5 s
# default; same margin as fallback.DEFAULT_TIMEOUT_S.
MCP_INIT_TIMEOUT_S = 30.0

type SpineAgent = Agent[AgentDeps, str]


@dataclass(frozen=True)
class AgentDeps:
    """Per-run dependencies: the astronaut of the session and the time of the turn (for the dynamic instruction)."""

    astronaut_id: str
    now: datetime


@dataclass(frozen=True)
class ReplyChunk:
    """One piece of a streamed reply: answer text, a tool call (for display), the fallback summary or a notice."""

    kind: Literal["text", "tool_call", "fallback", "notice"]
    text: str = ""
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None


def _astronaut(ctx: RunContext[AgentDeps]) -> str:
    return astronaut_instruction(ctx.deps.astronaut_id, ctx.deps.now)


def utc_now() -> datetime:
    return datetime.now(UTC)


def build_agent(
    settings: AgentSettings,
    *,
    model: Model | None = None,
    toolsets: Sequence[AbstractToolset[AgentDeps]] | None = None,
    log_file: Path | None = None,
) -> SpineAgent:
    """Agent wired from settings. `model` and `toolsets` replace the Ollama model and the MCP stdio toolset (tests).

    `log_file` is where the MCP server subprocess writes its stderr (None: this process's stderr). It only applies to
    the default stdio transport.
    """
    if model is None:
        model = OllamaModel(settings.model, provider=OllamaProvider(base_url=settings.ollama_base_url))
    if toolsets is None:
        transport = StdioTransport(
            command=settings.mcp_command,
            args=list(settings.mcp_args),
            log_file=log_file,
            keep_alive=False,  # the server subprocess ends when the toolset is exited; nothing outlives the session
        )
        # 'failed': a tool error (e.g. 404 for an unknown astronaut) reaches the model as a failed result it can
        # report ("I don't know"), instead of a retry loop that would end in UnexpectedModelBehavior.
        toolsets = [MCPToolset(transport, tool_error_behavior="failed", init_timeout=MCP_INIT_TIMEOUT_S)]
    model_settings = OpenAIChatModelSettings(
        temperature=settings.temperature,
        timeout=settings.request_timeout_s,
        # Thinking off. Ollama maps reasoning_effort "none" to think=false. The unified `thinking=False` setting is
        # silently dropped for the qwen profile (no `supports_thinking`), so the OpenAI-specific setting is used.
        openai_reasoning_effort="none",
    )
    return Agent(
        model,
        deps_type=AgentDeps,
        output_type=str,
        instructions=[SYSTEM_PROMPT, _astronaut],
        model_settings=model_settings,
        toolsets=toolsets,
    )


class ChatSession:
    """A conversation with the agent for one astronaut. Use as `async with ChatSession(...) as session:` so the MCP
    subprocess is started once and stopped on exit (without it, each run starts and stops its own)."""

    def __init__(self, agent: SpineAgent, fallback: StatusSource, astronaut_id: str, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._agent = agent
        self._clock = clock
        self._fallback = fallback
        self._astronaut_id = astronaut_id
        self._stack = AsyncExitStack()
        self.history: list[ModelMessage] = []

    async def __aenter__(self) -> Self:
        await self._stack.enter_async_context(self._agent)
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        await self._stack.aclose()

    async def ask(self, question: str) -> AsyncIterator[ReplyChunk]:
        """Stream the reply to `question`. Never raises for model or transport failures: it yields the fallback.

        History grows only with successful turns. Cancellation propagates.
        """
        streamed: list[str] = []
        output: str | None = None
        messages: list[ModelMessage] = []
        try:
            async with self._agent.run_stream_events(question, message_history=self.history, deps=AgentDeps(self._astronaut_id, self._clock())) as events:
                async for event in events:
                    if isinstance(event, AgentRunResultEvent):
                        output = event.result.output
                        messages = event.result.all_messages()
                    elif chunk := _chunk(event):
                        if chunk.kind == "text":
                            streamed.append(chunk.text)
                        yield chunk
        except MODEL_FAILURES:
            output = None

        if output is None or not output.strip():
            if any(text.strip() for text in streamed):
                yield ReplyChunk("notice", MID_STREAM_NOTICE)
            yield ReplyChunk("fallback", await self._fallback_text())
            return

        self.history = messages
        if find_diagnostic_terms("".join(streamed)):
            yield ReplyChunk("notice", CORRECTION_NOTICE)

    async def _fallback_text(self) -> str:
        status = await self._fallback.current_status(self._astronaut_id)
        assessment = (status or {}).get("latest_assessment") or {}
        assessment_id = assessment.get("assessment_id")
        explanation = await self._fallback.explanation(assessment_id) if assessment_id else None
        return fallback_reply(status, explanation)


def _chunk(event: object) -> ReplyChunk | None:
    match event:
        case PartStartEvent(part=TextPart(content=text)) if text:
            return ReplyChunk("text", text)
        case PartDeltaEvent(delta=TextPartDelta(content_delta=text)) if text:
            return ReplyChunk("text", text)
        case FunctionToolCallEvent(part=part):
            return ReplyChunk("tool_call", tool_name=part.tool_name, tool_args=part.args_as_dict())
        case _:
            return None
