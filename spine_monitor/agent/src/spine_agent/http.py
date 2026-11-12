"""Chat HTTP+SSE server of the agent (`contracts/agent-openapi.yaml`): `spine-agent-http`, on 127.0.0.1:8003.

POST /chat validates the request and streams the answer of the conversation's ChatSession as server-sent events
(`schemas/v1/agent/chat-event.schema.json`). The answer is produced in a task of its own that feeds a queue, so the
stream can send keepalive comments while the model is silent. Closing the connection cancels that task (the turn is
not kept); so do DELETE /chat/{id} (the stream ends with `error` `invalid`) and shutdown (`error` `unavailable`).

The agent is entered once in the lifespan, so the MCP tool server is started once and stopped on shutdown, in the
same task (its anyio scopes must be exited where they were entered). ChatSessions are never entered here: when the
tool server could not start, each run starts its own, and a failure to reach it ends the stream with `unavailable`.
"""

import asyncio
import json
import logging
import os
import re
import secrets
from collections.abc import AsyncGenerator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import pydantic_ai
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from mcp.shared.exceptions import MCPError
from spine_contracts import validation
from starlette.types import Receive, Scope, Send

from spine_agent.agent import CORRECTION_NOTICE, MID_STREAM_NOTICE, ChatSession, ReplyChunk, SpineAgent, build_agent
from spine_agent.conversations import Conversation, Conversations, Refused
from spine_agent.fallback import McpStatusSource
from spine_agent.ports import StatusSource
from spine_agent.settings import AgentSettings

_log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0.0"
REQUEST_SCHEMA = "agent/chat-request"
PROBLEM = "application/problem+json"
PING = ": ping\n\n"
ASTRONAUT_HEADER = "x-astronaut-id"
GRACEFUL_SHUTDOWN_S = 5  # uvicorn waits this long for open streams on shutdown, then closes them
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_NOTICE_CODES = {MID_STREAM_NOTICE: "mid_stream", CORRECTION_NOTICE: "correction"}
_TITLES = {401: "Unauthorized", 403: "Forbidden", 409: "Busy", 422: "Invalid request", 503: "Unavailable"}

type Event = dict[str, Any]


class Problem(Exception):
    def __init__(self, status: int, detail: str, errors: list[dict[str, str]] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.errors = errors


def default_token_file() -> Path:
    return Path.home() / ".spine-agent" / "http-token"


def token_of(settings: AgentSettings) -> str:
    """The configured token; else the one in the token file, created with a fresh random token when missing."""
    if settings.http_token is not None and settings.http_token.get_secret_value().strip():
        return settings.http_token.get_secret_value().strip()
    path = settings.http_token_file or default_token_file()
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(token)
    os.chmod(path, 0o600)  # the mode of os.open only applies on creation
    _log.info("generated the agent HTTP token in %s", path)
    return token


def _event(type_: str, **fields: Any) -> Event:
    return {"schema_version": SCHEMA_VERSION, "type": type_, **fields}


def _event_of(chunk: ReplyChunk) -> Event:
    match chunk.kind:
        case "text":
            return _event("text", delta=chunk.text)
        case "tool_call":
            return _event("tool_call", tool_name=chunk.tool_name, tool_args=chunk.tool_args or {})
        case "fallback":
            return _event("fallback", text=chunk.text)
        case _:  # "notice"
            return _event("notice", code=_NOTICE_CODES[chunk.text], text=chunk.text)


def _frame(event: Event) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'), ensure_ascii=False)}\n\n"


def _unreachable(exc: BaseException) -> bool:
    """Whether a failure means the tool server cannot be reached (it cannot start, or its pipe broke), rather than a bug.

    The MCP client wraps a server that cannot start in a RuntimeError caused by the OSError: the cause chain is followed.
    """
    if isinstance(exc, BaseExceptionGroup):
        return all(_unreachable(inner) for inner in exc.exceptions)
    if isinstance(exc, OSError | MCPError | anyio.ClosedResourceError | anyio.BrokenResourceError):
        return True
    return exc.__cause__ is not None and _unreachable(exc.__cause__)


class Answer:
    """The answer to one question: its SSE events, and `close`, which stops it and frees the conversation (idempotent)."""

    def __init__(self, conversations: Conversations, conversation: Conversation, question: str, keepalive_s: float) -> None:
        self._conversations = conversations
        self._conversation = conversation
        self._question = question
        self._keepalive_s = keepalive_s
        self._closed = False

    async def events(self) -> AsyncGenerator[str]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._conversation.answer = asyncio.create_task(self._produce(queue))
        try:
            while True:
                try:
                    async with asyncio.timeout(self._keepalive_s):
                        event = await queue.get()
                except TimeoutError:
                    yield PING
                    continue
                yield _frame(event)
                if event["type"] in ("end", "error"):
                    return
        finally:
            await self.close()

    async def _produce(self, queue: asyncio.Queue[Event]) -> None:
        try:
            async for chunk in self._conversation.session.ask(self._question):
                queue.put_nowait(_event_of(chunk))
        except asyncio.CancelledError:
            code, message = self._conversation.cancel_reason or ("unavailable", "the answer was cancelled")
            queue.put_nowait(_event("error", code=code, message=message))
            raise
        except Exception as exc:
            if _unreachable(exc):
                _log.warning("the answer failed: the tool server cannot be reached", exc_info=True)
                queue.put_nowait(_event("error", code="unavailable", message="the MCP tool server cannot be reached"))
            else:
                _log.exception("the answer failed")
                queue.put_nowait(_event("error", code="internal", message="internal error of the agent"))
        else:
            queue.put_nowait(_event("end"))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            task = self._conversation.answer
            if task is not None and not task.done():
                task.cancel()
                # Shielded: this runs while the response is being cancelled (the client left), and the conversation must
                # stay busy until the model run is really over.
                with anyio.CancelScope(shield=True):
                    await asyncio.wait({task})
        finally:
            self._conversations.release(self._conversation)


class ChatEventResponse(StreamingResponse):
    """The SSE response of an answer. It always watches for the client leaving (also under ASGI spec 2.4, where Starlette
    would only notice on the next write), and stops the answer however the response ends, even if it never started."""

    media_type = "text/event-stream"

    def __init__(self, answer: Answer) -> None:
        self._answer = answer
        self._events = answer.events()
        super().__init__(self._events, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            async with anyio.create_task_group() as group:

                async def stream() -> None:
                    try:
                        await self.stream_response(send)
                    except OSError:  # the client left during a write
                        pass
                    group.cancel_scope.cancel()

                group.start_soon(stream)
                await self.listen_for_disconnect(receive)
                group.cancel_scope.cancel()
        finally:
            await self._events.aclose()  # a generator stopped at a `yield` gets no exception: close it
            await self._answer.close()


def _problem(status: int, detail: str, errors: list[dict[str, str]] | None = None) -> JSONResponse:
    # `app/error` only enumerates read-API types: every refusal here is `invalid_request`, told apart by the status.
    body: dict[str, Any] = {"type": "invalid_request", "title": _TITLES.get(status, "Error"), "status": status, "detail": detail}
    if errors:
        body["errors"] = errors
    response = JSONResponse(body, status_code=status, media_type=PROBLEM)
    if status == 401:
        response.headers["WWW-Authenticate"] = "Bearer"
    return response


def _unlocked(request: Request, token: str) -> str:
    """The unlocked astronaut of an authorized request. Raises Problem 401 (token) or 403 (no X-Astronaut-Id)."""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    given = value.strip() if scheme.lower() == "bearer" else ""
    if not given or not secrets.compare_digest(given.encode(), token.encode()):
        raise Problem(401, "missing or invalid token")
    astronaut_id = request.headers.get(ASTRONAUT_HEADER, "").strip()
    if not astronaut_id:
        raise Problem(403, "no unlocked astronaut (X-Astronaut-Id)")
    return astronaut_id


async def _chat_request(request: Request) -> dict[str, Any]:
    try:
        payload = json.loads(await request.body())
    except ValueError:
        raise Problem(422, "the body is not valid JSON", [{"pointer": "/", "message": "not valid JSON"}]) from None
    found = validation.errors(REQUEST_SCHEMA, payload)
    if found:
        errors = [{"pointer": e.split(": ", 1)[0], "message": e.split(": ", 1)[-1]} for e in found]
        raise Problem(422, f"does not conform to {REQUEST_SCHEMA}", errors)
    return payload


def create_app(
    settings: AgentSettings | None = None,
    *,
    agent: SpineAgent | None = None,
    fallback: StatusSource | None = None,
    clock: Callable[[], float] | None = None,
) -> FastAPI:
    """The chat app. `agent`, `fallback` and `clock` (monotonic seconds, for the idle TTL) are injectable for tests."""
    settings = settings or AgentSettings()
    token = token_of(settings)
    chat_agent = agent or build_agent(settings, log_file=settings.mcp_log_file)
    status_source = fallback or McpStatusSource(settings)
    conversations = Conversations(
        lambda astronaut_id: ChatSession(chat_agent, status_source, astronaut_id),
        max_size=settings.http_max_conversations,
        idle_ttl_s=settings.http_idle_ttl_s,
        clock=clock or (lambda: asyncio.get_running_loop().time()),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            try:
                await stack.enter_async_context(chat_agent)
            except Exception:
                _log.warning("the MCP tool server did not start; each answer will try to start its own", exc_info=True)
            try:
                yield
            finally:
                await conversations.close()

    app = FastAPI(title="Spine LLM agent chat API", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.exception_handler(Problem)
    async def _on_problem(_: Request, exc: Problem) -> JSONResponse:
        return _problem(exc.status, exc.detail, exc.errors)

    @app.exception_handler(Refused)
    async def _on_refused(_: Request, exc: Refused) -> JSONResponse:
        return _problem(exc.status, exc.detail)

    @app.get("/health", operation_id="agentHealth")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/chat", operation_id="askAgent")
    async def ask(request: Request) -> Response:
        astronaut_id = _unlocked(request, token)
        payload = await _chat_request(request)
        if payload["astronaut_id"] != astronaut_id:
            raise Problem(403, "the request is about another astronaut than the one unlocked")
        conversation = conversations.claim(payload["conversation_id"], astronaut_id)
        return ChatEventResponse(Answer(conversations, conversation, payload["question"], settings.http_keepalive_s))

    @app.delete("/chat/{conversation_id}", operation_id="forgetConversation", status_code=204)
    async def forget(conversation_id: str, request: Request) -> Response:
        astronaut_id = _unlocked(request, token)
        if not _UUID.fullmatch(conversation_id):
            raise Problem(422, "the conversation id is not a lowercase UUID", [{"pointer": "conversation_id", "message": "not a lowercase UUID"}])
        conversations.forget(conversation_id, astronaut_id)
        return Response(status_code=204)

    return app


def main(*, run: Callable[..., Any] = uvicorn.run) -> int:
    """Console script `spine-agent-http`: serve the chat app on SPINE_AGENT_HTTP_HOST:SPINE_AGENT_HTTP_PORT."""
    pydantic_ai.BANNER_ENABLED = False  # set here, not at import time (as the chat CLI does)
    settings = AgentSettings()
    run(create_app(settings), host=settings.http_host, port=settings.http_port, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S)
    return 0
