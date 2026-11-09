"""The server-sent events of one session's repetition counter (`ui-openapi.yaml`, GET /ui/sessions/{id}/reps).

The generator is separate from the route so that it can be driven without a server. It subscribes on its first step, in one
go with the check of the session state (`RepHub.subscribe`), so a session that ends after the route answered is not missed,
and a response that never starts leaves nothing behind. All the waiting goes through `wait`: tests move time themselves.
"""

import asyncio
import json
import logging
import signal
import threading
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import FrameType
from typing import Any

from fastapi.responses import StreamingResponse
from spine_contracts import validation
from starlette.types import Receive, Scope, Send

from monitor.live.tracker import RepHub, RepMessage, RepStateView

_log = logging.getLogger(__name__)

_SCHEMA = "app/ui-rep-state"
_PING = ": ping\n\n"
_EXIT_SIGNALS = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGBREAK") if hasattr(signal, name))

type WaitNext = Callable[[asyncio.Queue[RepMessage], float], Awaitable[RepMessage | None]]


async def next_message[T](queue: asyncio.Queue[T], tick_s: float) -> T | None:
    """The next message, or None when `tick_s` passes first."""
    try:
        async with asyncio.timeout(tick_s):
            return await queue.get()
    except TimeoutError:
        return None


def _frame(event: str, view: RepStateView, session_id: str) -> str | None:
    """The SSE frame of a state, or None when it cannot be sent: a state that does not conform is a bug and is never served."""
    found = validation.errors(_SCHEMA, view)
    if found:
        # Only the JSON pointer: the message of a violation quotes the offending value, which may be health data.
        _log.error("repetition state of session %s does not conform to %s at %s", session_id, _SCHEMA, found[0].split(": ", 1)[0])
        return None
    try:
        data = json.dumps(view, separators=(",", ":"), allow_nan=False)
    except ValueError:  # a non-finite number passes the schema, but it is not JSON and the client could not read it
        _log.error("repetition state of session %s has a non-finite number", session_id)
        return None
    return f"event: {event}\ndata: {data}\n\n"


@dataclass(frozen=True, slots=True)
class RepStream:
    hub: RepHub
    tick_s: float
    heartbeat_s: float
    wait: WaitNext = next_message

    async def events(self, session_id: str) -> AsyncGenerator[str]:
        # One tick early, so that a tick that comes late still keeps the heartbeat within its limit.
        ticks_per_ping = max(1, int(self.heartbeat_s / self.tick_s) - 1)
        idle_ticks = 0
        subscription = self.hub.subscribe(session_id)
        try:
            while True:
                message = await self.wait(subscription.queue, self.tick_s)
                if message is None:
                    self.hub.refresh(session_id)  # if it changes, the state is in the queue for the next wait
                    idle_ticks += 1
                    if idle_ticks >= ticks_per_ping:
                        idle_ticks = 0
                        yield _PING
                    continue
                if message.view is None:
                    return
                frame = _frame(message.event, message.view, session_id)
                if frame is None:
                    return
                idle_ticks = 0
                yield frame
                if message.event == "end":
                    return
        finally:
            self.hub.unsubscribe(subscription)


class RepEventResponse(StreamingResponse):
    media_type = "text/event-stream"

    def __init__(self, events: AsyncGenerator[str]) -> None:
        super().__init__(events, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        self._events = events

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # When the client leaves during a write the generator is stopped at a `yield` and no exception reaches it.
            await self._events.aclose()


@contextmanager
def closing_on_exit_signal(close: Callable[[], Any]) -> Iterator[None]:
    """Runs `close` when an exit signal arrives, then whatever handled it before.

    uvicorn only starts the lifespan shutdown once every connection is closed, and it waits for them with no limit, so open
    streams must be closed as soon as the signal arrives. It installs its handlers before the lifespan starts: they are chained.
    Signals only exist in the main thread; elsewhere (e.g. TestClient) this does nothing.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous: dict[int, Callable[[int, FrameType | None], Any]] = {}

    def on_signal(number: int, frame: FrameType | None) -> None:
        try:
            close()
        finally:
            previous[number](number, frame)

    for number in _EXIT_SIGNALS:
        handler = signal.getsignal(number)
        if callable(handler):
            previous[number] = handler
            signal.signal(number, on_signal)
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
