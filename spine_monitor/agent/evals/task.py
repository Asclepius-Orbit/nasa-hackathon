"""What an eval run is: the input of a case, the observable output of one agent turn, and how to produce it.

`run_chat` drives the real ChatSession (the same code path as the CLI), so the offline suite and the live benchmark
grade exactly what a user would get. It only observes: tool calls from the streamed chunks, tool results from the
session history, timing around the stream.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart, ToolReturnPart
from spine_agent.agent import ChatSession, SpineAgent, utc_now
from spine_agent.ports import StatusSource

type Language = Literal["en", "es", "ca"]


@dataclass(frozen=True)
class EvalInput:
    """One question to the agent, asked by one astronaut."""

    question: str
    astronaut_id: str
    language: Language


@dataclass(frozen=True)
class CaseMeta:
    """Case metadata for reports: the benchmark groups pass rates by category."""

    category: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class AgentRun:
    """Everything the evaluators and the benchmark need from one turn."""

    text: str  # the answer the user ends up reading: the streamed text, or the fallback summary when the model failed
    tool_calls: list[ToolCall] = field(default_factory=list[ToolCall])
    tool_results: list[Any] = field(default_factory=list[Any])  # what the tools returned (dict, or the error text); empty on fallback
    fell_back: bool = False
    notices: list[str] = field(default_factory=list[str])
    ttft_s: float | None = None  # ask start -> first answer text
    total_s: float = 0.0
    output_tokens: int | None = None  # filled by the live runner (usage of the real model); None offline
    generation_s: float | None = None  # time spent inside the model streams (live runner only)


def tool_results(history: list[ModelMessage]) -> list[Any]:
    """Tool results in the history, in order. A failed tool call (e.g. unknown astronaut) is its error text."""
    results: list[Any] = []
    for message in history:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart | RetryPromptPart):
                    results.append(part.content)
    return results


async def run_chat(
    agent: SpineAgent,
    fallback: StatusSource,
    inputs: EvalInput,
    *,
    clock: Callable[[], float] = time.perf_counter,
    wall_clock: Callable[[], datetime] = utc_now,
) -> AgentRun:
    """Ask `inputs.question` in a fresh ChatSession and observe the turn. `clock` times it; `wall_clock` is the date the agent is told."""
    run = AgentRun(text="")
    streamed: list[str] = []
    async with ChatSession(agent, fallback, inputs.astronaut_id, clock=wall_clock) as session:
        start = clock()
        async for chunk in session.ask(inputs.question):
            match chunk.kind:
                case "text":
                    if run.ttft_s is None:
                        run.ttft_s = clock() - start
                    streamed.append(chunk.text)
                case "tool_call":
                    run.tool_calls.append(ToolCall(chunk.tool_name or "", chunk.tool_args or {}))
                case "notice":
                    run.notices.append(chunk.text)
                case "fallback":
                    run.fell_back = True
                    run.text = chunk.text
        run.total_s = clock() - start
        run.tool_results = tool_results(session.history)
    if not run.fell_back:
        run.text = "".join(streamed)
    return run
