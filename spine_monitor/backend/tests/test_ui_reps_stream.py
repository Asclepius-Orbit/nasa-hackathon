"""GET /ui/sessions/{id}/reps: the SSE generator, driven with no wall clock, and the route (headers and status codes).

httpx's ASGITransport buffers a response until it ends, so the live part drives the generator directly; only what ends by
itself (a session already over, the refusals) goes through the route.
"""

import asyncio
import json
import signal
from collections.abc import AsyncIterator
from typing import Any

import pytest
from monitor.live.stream import RepEventResponse, RepStream, next_message
from monitor.main import create_app
from monitor.settings import AppSettings
from spine_contracts import validation

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

STATE_SCHEMA = "app/ui-rep-state"
ERROR_SCHEMA = "app/error"
TICK_S = 0.5
HEARTBEAT_S = 15.0
UNKNOWN = "aaaaaaaa-bbbb-4ccc-8ddd-000000000000"


class Ticks:
    """The wait: a message when there is one, otherwise a tick right away. Time only passes when a test moves the clock."""

    def __init__(self) -> None:
        self.count = 0

    async def __call__(self, queue: asyncio.Queue, _tick_s: float):
        await asyncio.sleep(0)
        if queue.empty():
            self.count += 1
            return None
        return queue.get_nowait()


async def only_messages(queue: asyncio.Queue, _tick_s: float):
    return await queue.get()


def stream(harness, wait=None) -> RepStream:
    return RepStream(harness.runtime.hub, tick_s=TICK_S, heartbeat_s=HEARTBEAT_S, wait=wait or Ticks())


def parse(frame: str) -> tuple[str, dict[str, Any]]:
    """Exact frame shape: `event: <name>`, `data: <one JSON line>`, blank line. Every state must conform to the schema."""
    assert frame.endswith("\n\n")
    event, data = frame.removesuffix("\n\n").split("\n")
    assert event.startswith("event: ") and data.startswith("data: ")
    view = json.loads(data.removeprefix("data: "))
    assert validation.errors(STATE_SCHEMA, view) == []
    return event.removeprefix("event: "), view


async def next_frame(events: AsyncIterator[str]) -> str:
    return await asyncio.wait_for(anext(events), 5)


async def assert_closed(events: AsyncIterator[str]) -> None:
    with pytest.raises(StopAsyncIteration):
        await next_frame(events)


async def running(harness) -> tuple[str, dict[str, Any]]:
    await harness.setup_astronaut()
    return await harness.start_session()


# --- live: connection, changes, signal, heartbeat ----------------------------------------------------


async def test_a_real_session_over_the_bus_streams_its_10_reps(harness) -> None:
    session_id, _ = await running(harness)
    frames: list[str] = []
    events = stream(harness, only_messages).events(session_id)

    async def client() -> None:
        async for frame in events:
            frames.append(frame)

    reader = asyncio.create_task(client())
    await asyncio.sleep(0)
    await harness.send_stream_chunks(harness.imu_chunks(session_id))
    await asyncio.sleep(0)
    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader

    parsed = [parse(frame) for frame in frames]
    assert {event for event, _ in parsed} == {"state"}
    views = [view for _, view in parsed]
    assert (views[0]["rep"], views[0]["total"], views[0]["signal"], views[0]["phase"]) == (0, 10, "no_signal", None)
    assert (views[-1]["rep"], views[-1]["last"]["rep"], views[-1]["total"], views[-1]["phase"], views[-1]["signal"]) == (10, 10, 10, "resting", "ok")
    trace = []
    for view in views[1:]:
        if not trace or trace[-1] != (view["rep"], view["phase"]):
            trace.append((view["rep"], view["phase"]))
    assert trace == [(0, "resting"), *((rep, phase) for rep in range(1, 11) for phase in ("pulling", "letting_up", "resting"))]
    # An event only when something changed: `as_of` alone is not a change.
    contents = [{key: value for key, value in view.items() if key != "as_of"} for view in views]
    assert all(before != after for before, after in zip(contents, contents[1:], strict=False))


async def test_no_signal_after_2_s_and_back_with_no_early_event(harness) -> None:
    session_id, _ = await running(harness)
    chunks = harness.imu_chunks(session_id)
    events = stream(harness).events(session_id)
    assert parse(await next_frame(events))[1]["signal"] == "no_signal"  # a session just started, with no chunk yet

    await harness.send_stream_chunks(chunks[:1])
    event, view = parse(await next_frame(events))
    assert (event, view["signal"], view["phase"]) == ("state", "ok", "resting")

    harness.clock.advance(seconds=1.9)
    assert await next_frame(events) == ": ping\n\n"  # nothing to tell: the ticks only end in the heartbeat

    harness.clock.advance(seconds=0.1)
    event, view = parse(await next_frame(events))
    assert (event, view["signal"], view["phase"], view["progress_pct"], view["rep"], view["total"]) == ("state", "no_signal", None, None, 0, 10)
    assert view["as_of"] == harness.clock().isoformat()

    await harness.send_stream_chunks(chunks[1:2])
    assert (parse(await next_frame(events))[1]["signal"]) == "ok"
    await events.aclose()


async def test_heartbeat_keeps_an_idle_stream_under_15_s(harness) -> None:
    session_id, _ = await running(harness)
    ticks = Ticks()
    events = stream(harness, ticks).events(session_id)
    await next_frame(events)
    assert ticks.count == 0

    assert await next_frame(events) == ": ping\n\n"
    assert ticks.count * TICK_S == 14.5  # one tick of margin: a late tick still is within the 15 s
    assert await next_frame(events) == ": ping\n\n"
    assert ticks.count * TICK_S == 29.0
    await events.aclose()


async def test_the_default_wait_returns_a_message_or_none_on_the_tick() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    assert await next_message(queue, 0) is None
    queue.put_nowait("message")
    assert await next_message(queue, 0) == "message"
    assert queue.empty()


# --- the end of the session --------------------------------------------------------------------------


async def test_end_carries_the_last_state_and_closes_without_a_leak(harness) -> None:
    session_id, start = await running(harness)
    await harness.send_stream_chunks(harness.imu_chunks(session_id)[:1])
    events = stream(harness).events(session_id)
    first = parse(await next_frame(events))[1]

    await harness.stop_session(session_id, start)

    event, view = parse(await next_frame(events))
    assert event == "end"
    assert {k: v for k, v in view.items() if k != "as_of"} == {k: v for k, v in first.items() if k != "as_of"}
    await assert_closed(events)
    assert harness.runtime.hub.tracked_sessions() == set()


async def test_a_session_rejected_by_the_device_ends_the_stream(harness) -> None:
    await harness.setup_astronaut()
    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan())
    body = response.json()
    session_id = body["session"]["session_id"]
    events = stream(harness).events(session_id)
    await next_frame(events)

    await harness.ack(body["command"], "rejected", error={"code": "busy", "message": "busy"})

    assert parse(await next_frame(events))[0] == "end"
    await assert_closed(events)
    assert harness.store.session(session_id).state == "rejected"


async def test_a_session_that_ends_just_as_the_client_connects_gets_state_and_end(harness) -> None:
    """The route has answered, but the generator has not started when the session ends: nobody would ever tell it otherwise."""
    session_id, start = await running(harness)
    await harness.send_stream_chunks(harness.imu_chunks(session_id)[:3])
    events = stream(harness).events(session_id)

    await harness.stop_session(session_id, start)

    assert parse(await next_frame(events))[0] == "state"
    assert parse(await next_frame(events))[0] == "end"
    await assert_closed(events)
    assert harness.runtime.hub.tracked_sessions() == set()


async def test_route_on_a_finished_session_sends_final_state_and_end_with_sse_headers(harness) -> None:
    session_id, start = await running(harness)
    chunks = harness.imu_chunks(session_id)
    await harness.send_stream_chunks(chunks[:60])
    await harness.stop_session(session_id, start)
    assert harness.store.session(session_id).state in ("processed", "processing_failed")

    response = await harness.http.get(f"/ui/sessions/{session_id}/reps")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    frames = [frame + "\n\n" for frame in response.text.split("\n\n") if frame]
    assert [parse(frame)[0] for frame in frames] == ["state", "end"]
    _, view = parse(frames[1])
    assert (view["signal"], view["phase"], view["total"], view["session_id"]) == ("no_signal", None, 10, session_id)
    assert harness.runtime.hub.tracked_sessions() == set()


# --- refusals ----------------------------------------------------------------------------------------


async def assert_problem(harness, session_id: str, status: int, type_: str) -> None:
    response = await harness.http.get(f"/ui/sessions/{session_id}/reps")
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert validation.errors(ERROR_SCHEMA, response.json()) == []
    assert response.json()["type"] == type_


@pytest.mark.parametrize("session_id", ["not-a-uuid", UNKNOWN.upper()])
async def test_an_invalid_id_is_422(harness, session_id: str) -> None:
    await assert_problem(harness, session_id, 422, "invalid_request")


async def test_an_unknown_session_is_404(harness) -> None:
    await assert_problem(harness, UNKNOWN, 404, "not_found")


async def test_a_protocol_without_reps_or_a_session_without_imu_l3_is_409_no_reps(harness) -> None:
    await harness.setup_astronaut()
    no_reps = harness.plan(protocol_id="spine_length_rest_v1")
    del no_reps["planned_reps"]
    created = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=no_reps)
    await assert_problem(harness, created.json()["session"]["session_id"], 409, "no_reps")
    await harness.ack(created.json()["command"], "rejected", error={"code": "busy", "message": "busy"})

    plan = harness.plan()
    no_imu = harness.plan(streams=[stream for stream in plan["streams"] if stream["stream_id"] != "imu_l3"])
    created = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=no_imu)
    await assert_problem(harness, created.json()["session"]["session_id"], 409, "no_reps")


# --- a state that does not conform -------------------------------------------------------------------


async def test_a_state_that_does_not_conform_is_never_sent_and_only_its_pointer_is_logged(harness, caplog) -> None:
    await harness.setup_astronaut()
    session = {**harness.example("domain/session.exercise"), "planned_reps": 0, "session_id": UNKNOWN}  # total below the minimum of the schema
    harness.store.insert_session(session, "running")
    events = stream(harness).events(UNKNOWN)

    await assert_closed(events)

    assert harness.runtime.hub.subscriber_count(UNKNOWN) == 0
    assert "/total" in caplog.text
    assert "is less than the minimum" not in caplog.text


# --- leaving ------------------------------------------------------------------------------------------


async def test_a_client_that_leaves_while_the_generator_waits_is_removed_and_the_cancellation_goes_on(harness) -> None:
    session_id, _ = await running(harness)
    hub = harness.runtime.hub
    events = RepStream(hub, tick_s=3600, heartbeat_s=HEARTBEAT_S).events(session_id)  # the real wait; its tick never comes
    await next_frame(events)
    assert hub.subscriber_count(session_id) == 1

    pending = asyncio.ensure_future(anext(events))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert hub.subscriber_count(session_id) == 0


async def test_a_client_that_leaves_while_the_response_is_writing_is_removed(harness) -> None:
    """The generator is stopped at a `yield`, so no exception reaches it: the response has to close it."""
    session_id, _ = await running(harness)
    hub = harness.runtime.hub
    written = asyncio.Event()

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            written.set()
            await asyncio.Event().wait()  # a client that stopped reading

    async def receive() -> dict[str, Any]:
        await written.wait()
        assert hub.subscriber_count(session_id) == 1
        return {"type": "http.disconnect"}

    response = RepEventResponse(stream(harness).events(session_id))
    await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send), 5)

    assert hub.subscriber_count(session_id) == 0


# --- backend shutdown ---------------------------------------------------------------------------------


async def test_closing_the_hub_closes_open_streams_without_end_and_refuses_new_ones(harness) -> None:
    session_id, _ = await running(harness)
    hub = harness.runtime.hub
    events = stream(harness).events(session_id)
    await next_frame(events)

    hub.close()

    await assert_closed(events)  # no `end`: the session goes on, and the client reconnects
    await assert_closed(stream(harness).events(session_id))
    assert hub.tracked_sessions() == set()


async def test_an_exit_signal_during_the_lifespan_closes_open_streams_and_then_runs_the_previous_handler(harness, tmp_path) -> None:
    """uvicorn only runs the lifespan shutdown once every connection is closed: open streams must hear of the exit from the signal."""
    calls: list[int] = []

    def uvicorn_handler(number: int, _frame: object) -> None:
        calls.append(number)

    app = create_app(AppSettings(_env_file=None, db_path=tmp_path / "exit.db"), mqtt_client_factory=harness.mqtt.factory, clock=harness.clock)
    runtime = app.state.runtime
    original = signal.signal(signal.SIGINT, uvicorn_handler)
    try:
        async with app.router.lifespan_context(app):
            runtime.store.insert_session({**harness.example("domain/session.exercise"), "session_id": UNKNOWN}, "running")
            events = runtime.reps.events(UNKNOWN)
            await next_frame(events)

            signal.raise_signal(signal.SIGINT)

            assert calls == [signal.SIGINT]
            await assert_closed(events)
            assert runtime.hub.tracked_sessions() == set()
        assert signal.getsignal(signal.SIGINT) is uvicorn_handler
    finally:
        signal.signal(signal.SIGINT, original)


async def test_a_session_unknown_to_the_hub_closes_the_stream_and_nothing_stays(harness) -> None:
    await assert_closed(stream(harness).events(UNKNOWN))  # the route refuses it first; this is the store changing under it
    harness.runtime.hub.refresh(UNKNOWN)
    harness.runtime.hub.finish(UNKNOWN)
    assert harness.runtime.hub.tracked_sessions() == set()
