"""The chat HTTP+SSE endpoint (`contracts/agent-openapi.yaml`): auth, validation, the event stream and its lifecycle.

The LLM is the boundary: a scripted FunctionModel (or a failing one, for the no-LLM fallback path) and a hand-written
status source. httpx's ASGITransport buffers a response until it ends, so whatever must be watched while it streams
(keepalives, a second question meanwhile, the client leaving) goes through `AsgiCall`, which drives the ASGI app by hand.
"""

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from pydantic_ai.exceptions import ModelConnectionError
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from spine_agent import http
from spine_agent.agent import CORRECTION_NOTICE, MID_STREAM_NOTICE, build_agent
from spine_agent.settings import AgentSettings
from spine_contracts import validation

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


TOKEN = "test-token"
AUTH = {"authorization": f"Bearer {TOKEN}", "x-astronaut-id": "AST-01"}
CONV = "c0ffee00-0000-4000-8000-000000000001"
OTHER_CONV = "c0ffee00-0000-4000-8000-000000000002"
EVENT_SCHEMA = "agent/chat-event"

type Script = Callable[[list[ModelMessage], AgentInfo], AsyncIterator[str | DeltaToolCalls]]


# ---------------------------------------------------------------- fakes at the boundary (the LLM, the status source, the clock)


class FakeStatusSource:
    async def current_status(self, astronaut_id: str) -> dict[str, Any] | None:
        return None

    async def explanation(self, assessment_id: str) -> dict[str, Any] | None:
        return None


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def text_script(*chunks: str) -> Script:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        for chunk in chunks:
            yield chunk

    return stream


def failing_script(error: Exception, *, after: tuple[str, ...] = ()) -> Script:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        for chunk in after:
            yield chunk
        raise error

    return stream


class Gate:
    """A model that waits until the test opens it, and records whether its run was cancelled meanwhile."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.opened = asyncio.Event()
        self.cancelled = asyncio.Event()

    def script(self, *chunks: str) -> Script:
        async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
            self.started.set()
            try:
                await self.opened.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            for chunk in chunks:
                yield chunk

        return stream


def questions_seen(messages: list[ModelMessage]) -> list[str]:
    return [str(part.content) for m in messages if isinstance(m, ModelRequest) for part in m.parts if isinstance(part, UserPromptPart)]


def echo_history_script() -> Script:
    """Answers with the questions the model sees, so a test can tell whether the history was kept."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        yield " | ".join(questions_seen(messages))

    return stream


def tool_script() -> Script:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        returned = [p for m in messages[-1:] if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returned:
            yield {0: DeltaToolCall(name="get_current_status", json_args=json.dumps({"astronaut_id": "AST-01"}), tool_call_id="c1")}
        else:
            yield f"Level: {returned[0].content}."

    return stream


def status_tools() -> FunctionToolset[Any]:
    toolset: FunctionToolset[Any] = FunctionToolset()

    @toolset.tool_plain
    def get_current_status(astronaut_id: str) -> str:
        return "amber"

    return toolset


def settings(**overrides: Any) -> AgentSettings:
    values: dict[str, Any] = {"mcp_command": sys.executable, "mcp_args": ["-m", "spine_mcp_server", "--dataset", "examples"], "http_token": TOKEN}
    return AgentSettings(**{**values, **overrides})


def make_app(script: Script, *, toolsets: list[Any] | None = None, clock: Callable[[], float] | None = None, **overrides: Any):
    s = settings(**overrides)
    agent = build_agent(s, model=FunctionModel(stream_function=script), toolsets=[] if toolsets is None else toolsets)
    return http.create_app(s, agent=agent, fallback=FakeStatusSource(), clock=clock or Clock())


def body(question: str = "How is my back?", conversation_id: str = CONV, astronaut_id: str = "AST-01") -> dict[str, Any]:
    return {"schema_version": "1.0.0", "astronaut_id": astronaut_id, "conversation_id": conversation_id, "question": question}


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent")


def events_of(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Exact framing: keepalive comments, or `event: <type>` + one `data:` line + blank line; every data conforms."""
    assert text.endswith("\n\n")
    found = []
    for frame in text.removesuffix("\n\n").split("\n\n"):
        if frame == ": ping":
            continue
        event, data = frame.split("\n")
        assert event.startswith("event: ") and data.startswith("data: "), frame
        payload = json.loads(data.removeprefix("data: "))
        assert validation.errors(EVENT_SCHEMA, payload) == [], payload
        assert payload["type"] == event.removeprefix("event: ")
        found.append((payload["type"], payload))
    return found


async def ask(app, question: str = "How is my back?", conversation_id: str = CONV) -> list[tuple[str, dict[str, Any]]]:
    async with client(app) as c:
        response = await c.post("/chat", json=body(question, conversation_id), headers=AUTH)
    assert response.status_code == 200, response.text
    return events_of(response.text)


def assert_problem(response: httpx.Response, status: int) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert validation.errors("app/error", response.json()) == []
    assert response.json()["status"] == status


class AsgiCall:
    """One request driven through the ASGI app by hand: the body is read frame by frame, and the client can leave."""

    def __init__(self, app, method: str, path: str, payload: dict[str, Any], headers: dict[str, str]) -> None:
        self._app = app
        self._scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(k.encode(), v.encode()) for k, v in {**headers, "content-type": "application/json"}.items()],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8003),
        }
        self._body = json.dumps(payload).encode()
        self._sent: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._left = asyncio.Event()
        self._requested = False
        self._buffer = ""
        self.task: asyncio.Task[None] | None = None

    async def _receive(self) -> dict[str, Any]:
        if not self._requested:
            self._requested = True
            return {"type": "http.request", "body": self._body, "more_body": False}
        await self._left.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict[str, Any]) -> None:
        if self._left.is_set() and message["type"] == "http.response.body":
            raise OSError("client gone")
        await self._sent.put(message)

    async def start(self) -> int:
        self.task = asyncio.create_task(self._app(self._scope, self._receive, self._send))
        message = await asyncio.wait_for(self._sent.get(), 5)
        assert message["type"] == "http.response.start"
        self.headers = {k.decode(): v.decode() for k, v in message["headers"]}
        return message["status"]

    async def frame(self) -> str:
        """The next frame (keepalive comment or event), without its closing blank line; '' when the body ended."""
        while "\n\n" not in self._buffer:
            message = await asyncio.wait_for(self._sent.get(), 5)
            self._buffer += message.get("body", b"").decode()
            if not message.get("more_body", False):
                break
        frame, _, self._buffer = self._buffer.partition("\n\n")
        return frame

    async def rest(self) -> list[tuple[str, dict[str, Any]]]:
        frames = []
        while frame := await self.frame():
            frames.append(frame)
        return events_of("".join(f"{f}\n\n" for f in frames)) if frames else []

    async def leave(self) -> None:
        self._left.set()
        assert self.task is not None
        await asyncio.wait_for(self.task, 5)


def chat_call(app, question: str = "How is my back?", conversation_id: str = CONV) -> AsgiCall:
    return AsgiCall(app, "POST", "/chat", body(question, conversation_id), AUTH)


# ---------------------------------------------------------------- contract surface


async def test_health_needs_no_token():
    async with client(make_app(text_script("ok"))) as c:
        response = await c.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


# ---------------------------------------------------------------- refusals before the stream


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic test-token", "Bearer "])
async def test_chat_without_a_valid_token_is_401(authorization):
    headers = {"x-astronaut-id": "AST-01"} | ({"authorization": authorization} if authorization else {})
    async with client(make_app(text_script("ok"))) as c:
        response = await c.post("/chat", json=body(), headers=headers)
    assert_problem(response, 401)
    assert response.headers["www-authenticate"] == "Bearer"


async def test_chat_without_an_unlocked_astronaut_is_403():
    async with client(make_app(text_script("ok"))) as c:
        response = await c.post("/chat", json=body(), headers={"authorization": f"Bearer {TOKEN}"})
    assert_problem(response, 403)


async def test_chat_about_another_astronaut_than_the_unlocked_one_is_403():
    async with client(make_app(text_script("ok"))) as c:
        response = await c.post("/chat", json=body(astronaut_id="AST-02"), headers=AUTH)
    assert_problem(response, 403)


async def test_a_conversation_of_another_astronaut_is_403():
    app = make_app(text_script("ok"))
    await ask(app)
    async with client(app) as c:
        response = await c.post("/chat", json=body(astronaut_id="AST-02"), headers=AUTH | {"x-astronaut-id": "AST-02"})
    assert_problem(response, 403)


async def test_a_body_that_is_not_json_is_422():
    async with client(make_app(text_script("ok"))) as c:
        response = await c.post("/chat", content=b"{not json", headers=AUTH | {"content-type": "application/json"})
    assert_problem(response, 422)


@pytest.mark.parametrize(
    ("payload", "pointer"),
    [
        (body(question="   "), "/question"),
        (body(conversation_id="chat-1"), "/conversation_id"),
        ({k: v for k, v in body().items() if k != "schema_version"}, "/"),
        ([body()], "/"),
    ],
)
async def test_a_body_that_does_not_conform_is_422_with_pointers(payload, pointer):
    async with client(make_app(text_script("ok"))) as c:
        response = await c.post("/chat", json=payload, headers=AUTH)
    assert_problem(response, 422)
    assert response.json()["type"] == "invalid_request"
    assert pointer in [e["pointer"] for e in response.json()["errors"]]


# ---------------------------------------------------------------- the stream


async def test_an_answer_streams_text_then_end():
    app = make_app(text_script("You are ", "amber today."))
    async with client(app) as c:
        response = await c.post("/chat", json=body(), headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    found = events_of(response.text)
    assert found[-1] == ("end", {"schema_version": "1.0.0", "type": "end"})
    assert "".join(p["delta"] for kind, p in found if kind == "text") == "You are amber today."
    assert [kind for kind, _ in found] == ["text"] * (len(found) - 1) + ["end"]


async def test_a_tool_call_is_streamed_before_the_text():
    found = await ask(make_app(tool_script(), toolsets=[status_tools()]))
    call: dict[str, Any] = {"schema_version": "1.0.0", "type": "tool_call", "tool_name": "get_current_status", "tool_args": {"astronaut_id": "AST-01"}}
    assert found[0] == ("tool_call", call)
    assert "".join(p["delta"] for kind, p in found if kind == "text") == "Level: amber."
    assert found[-1][0] == "end"


async def test_without_the_llm_the_fallback_is_streamed_then_end():
    found = await ask(make_app(failing_script(ModelConnectionError("qwen3.5:4b", "Connection refused"))))
    assert [kind for kind, _ in found] == ["fallback", "end"]
    assert found[0][1]["text"]


async def test_a_model_that_fails_mid_answer_sends_the_mid_stream_notice_then_the_fallback():
    found = await ask(make_app(failing_script(ModelConnectionError("qwen3.5:4b", "reset", in_stream=True), after=("You are ",))))
    kinds = [kind for kind, _ in found]
    assert kinds[-3:] == ["notice", "fallback", "end"]
    assert found[-3][1] == {"schema_version": "1.0.0", "type": "notice", "code": "mid_stream", "text": MID_STREAM_NOTICE}


async def test_a_diagnosis_is_followed_by_the_correction_notice():
    found = await ask(make_app(text_script("You have a herniated disc.")))
    assert found[-2][1] == {"schema_version": "1.0.0", "type": "notice", "code": "correction", "text": CORRECTION_NOTICE}
    assert found[-1][0] == "end"


async def test_a_bug_in_the_agent_ends_the_stream_with_an_internal_error():
    found = await ask(make_app(failing_script(ValueError("bug"))))
    assert [kind for kind, _ in found] == ["error"]
    assert found[0][1]["code"] == "internal"


async def test_an_unreachable_tool_server_ends_the_stream_with_unavailable():
    s = settings(mcp_command="spine-no-such-command-xyz", mcp_args=[])
    app = http.create_app(s, agent=build_agent(s, model=FunctionModel(stream_function=text_script("ok"))), fallback=FakeStatusSource(), clock=Clock())
    async with app.router.lifespan_context(app):  # entering the agent fails: the app still starts
        found = await ask(app)
    assert [kind for kind, _ in found] == ["error"]
    assert found[0][1]["code"] == "unavailable"


async def test_keepalive_comments_are_sent_while_waiting_for_the_model():
    gate = Gate()
    call = chat_call(make_app(gate.script("ok"), http_keepalive_s=0.01))
    assert await call.start() == 200
    assert await call.frame() == ": ping"
    assert await call.frame() == ": ping"
    gate.opened.set()
    found = await call.rest()
    assert found[-1][0] == "end"


# ---------------------------------------------------------------- conversations


async def test_the_same_conversation_keeps_the_history_and_a_new_id_starts_over():
    app = make_app(echo_history_script())
    await ask(app, "first")
    second = await ask(app, "second")
    other = await ask(app, "third", OTHER_CONV)
    assert "".join(p["delta"] for kind, p in second if kind == "text") == "first | second"
    assert "".join(p["delta"] for kind, p in other if kind == "text") == "third"


async def test_a_second_question_while_answering_is_409_and_other_conversations_go_on():
    gate = Gate()
    app = make_app(gate.script("ok"))
    first = chat_call(app)
    assert await first.start() == 200
    await gate.started.wait()
    async with client(app) as c:
        response = await c.post("/chat", json=body("again"), headers=AUTH)
    assert_problem(response, 409)
    gate.opened.set()
    assert (await first.rest())[-1][0] == "end"
    assert (await ask(app, "again"))[-1][0] == "end"  # free again once the answer ended


async def test_closing_the_connection_cancels_the_answer_and_frees_the_conversation():
    gate = Gate()
    app = make_app(gate.script("never shown"))
    call = chat_call(app, "first")
    assert await call.start() == 200
    await gate.started.wait()
    await call.leave()
    assert gate.cancelled.is_set()
    gate.opened.set()
    found = await ask(app, "second")  # not busy any more, and the cancelled turn is not in the history
    assert found[-1][0] == "end"


async def test_forget_drops_the_history_and_is_idempotent():
    app = make_app(echo_history_script())
    await ask(app, "first")
    async with client(app) as c:
        assert (await c.delete(f"/chat/{CONV}", headers=AUTH)).status_code == 204
        assert (await c.delete(f"/chat/{CONV}", headers=AUTH)).status_code == 204
    found = await ask(app, "second")
    assert "".join(p["delta"] for kind, p in found if kind == "text") == "second"


async def test_forget_is_guarded_like_chat():
    app = make_app(text_script("ok"))
    await ask(app)
    async with client(app) as c:
        assert_problem(await c.delete(f"/chat/{CONV}"), 401)
        assert_problem(await c.delete(f"/chat/{CONV}", headers={"authorization": f"Bearer {TOKEN}"}), 403)
        assert_problem(await c.delete(f"/chat/{CONV}", headers=AUTH | {"x-astronaut-id": "AST-02"}), 403)
        assert_problem(await c.delete("/chat/not-a-uuid", headers=AUTH), 422)


async def test_forgetting_a_conversation_while_it_answers_ends_its_stream_with_invalid():
    gate = Gate()
    app = make_app(gate.script("never shown"))
    call = chat_call(app)
    assert await call.start() == 200
    await gate.started.wait()
    async with client(app) as c:
        assert (await c.delete(f"/chat/{CONV}", headers=AUTH)).status_code == 204
    found = await call.rest()
    assert [kind for kind, _ in found] == ["error"]
    assert found[0][1]["code"] == "invalid"
    assert gate.cancelled.is_set()


async def test_a_full_pool_forgets_the_least_recently_used_idle_conversation():
    app = make_app(echo_history_script(), http_max_conversations=2)
    await ask(app, "a1", CONV)
    await ask(app, "b1", OTHER_CONV)
    await ask(app, "a2", CONV)  # CONV is now the most recently used
    third = "c0ffee00-0000-4000-8000-000000000003"
    await ask(app, "c1", third)  # evicts OTHER_CONV
    a = await ask(app, "a3", CONV)
    b = await ask(app, "b2", OTHER_CONV)
    assert "".join(p["delta"] for kind, p in a if kind == "text") == "a1 | a2 | a3"
    assert "".join(p["delta"] for kind, p in b if kind == "text") == "b2"


async def test_a_full_pool_of_answering_conversations_refuses_a_new_one_with_503():
    gate = Gate()
    app = make_app(gate.script("ok"), http_max_conversations=1)
    call = chat_call(app)
    assert await call.start() == 200
    await gate.started.wait()
    async with client(app) as c:
        response = await c.post("/chat", json=body(conversation_id=OTHER_CONV), headers=AUTH)
    assert_problem(response, 503)
    gate.opened.set()
    assert (await call.rest())[-1][0] == "end"


async def test_an_idle_conversation_is_forgotten_after_the_ttl():
    clock = Clock()
    app = make_app(echo_history_script(), clock=clock, http_idle_ttl_s=60)
    await ask(app, "first")
    clock.now += 59
    kept = await ask(app, "second")
    clock.now += 61
    forgotten = await ask(app, "third")
    assert "".join(p["delta"] for kind, p in kept if kind == "text") == "first | second"
    assert "".join(p["delta"] for kind, p in forgotten if kind == "text") == "third"


async def test_shutdown_ends_open_streams_with_unavailable():
    gate = Gate()
    app = make_app(gate.script("never shown"))
    call = chat_call(app)
    async with app.router.lifespan_context(app):
        assert await call.start() == 200
        await gate.started.wait()
    found = await call.rest()
    assert [kind for kind, _ in found] == ["error"]
    assert found[0][1]["code"] == "unavailable"
    assert gate.cancelled.is_set()


class BrokenPipeCall(AsgiCall):
    """A client whose connection breaks on the first write of the body, without ever sending http.disconnect."""

    async def _send(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            raise OSError("broken pipe")
        await super()._send(message)


async def test_a_connection_broken_during_a_write_ends_the_response_and_frees_the_conversation():
    app = make_app(text_script("first"))
    call = BrokenPipeCall(app, "POST", "/chat", body(), AUTH)
    assert await call.start() == 200
    assert call.task is not None
    await asyncio.wait_for(call.task, 5)
    assert (await ask(app, "second"))[-1][0] == "end"
