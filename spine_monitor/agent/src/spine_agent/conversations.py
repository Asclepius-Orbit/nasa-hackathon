"""The conversations the chat HTTP server keeps in memory: conversation_id -> ChatSession, bounded and with an idle TTL.

One answer at a time per conversation (`claim` marks it busy until `release`). A conversation belongs to the astronaut of
its first question. When the pool is full, the least recently used idle conversation is forgotten; when every one is
answering, a new conversation is refused. An idle conversation is forgotten after `idle_ttl_s`. Nothing is persisted.

`Refused` carries the HTTP status the route answers with (403, 409, 503).
"""

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol

from spine_agent.agent import ReplyChunk


class Session(Protocol):
    """What a conversation needs from a ChatSession."""

    def ask(self, question: str) -> AsyncIterator[ReplyChunk]: ...

    def ask_report(self, question: str) -> AsyncIterator[ReplyChunk]: ...


class Refused(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(eq=False)
class Conversation:
    astronaut_id: str
    session: Session
    last_used: float
    busy: bool = False
    answer: asyncio.Task[None] | None = None  # the task producing the current answer, while busy
    cancel_reason: tuple[str, str] | None = None  # (error code, message) the cancelled answer reports

    def cancel(self, code: str, message: str) -> asyncio.Task[None] | None:
        """Cancel the answer in progress, if any, and say why; returns its task."""
        task = self.answer
        if task is None or task.done():
            return None
        self.cancel_reason = (code, message)
        task.cancel()
        return task


class Conversations:
    def __init__(self, open_session: Callable[[str], Session], *, max_size: int, idle_ttl_s: float, clock: Callable[[], float]) -> None:
        self._open = open_session
        self._max_size = max_size
        self._idle_ttl_s = idle_ttl_s
        self._clock = clock
        self._items: OrderedDict[str, Conversation] = OrderedDict()  # least recently used first

    def claim(self, conversation_id: str, astronaut_id: str) -> Conversation:
        """The conversation, created when unknown, marked busy. Raises Refused (403 other astronaut, 409 busy, 503 full)."""
        now = self._clock()
        self._expire(now)
        conversation = self._items.get(conversation_id)
        if conversation is not None:
            if conversation.astronaut_id != astronaut_id:
                raise Refused(403, "the conversation belongs to another astronaut")
            if conversation.busy:
                raise Refused(409, "the conversation is still answering a question")
            self._items.move_to_end(conversation_id)
        else:
            if len(self._items) >= self._max_size:
                idle = next((key for key, item in self._items.items() if not item.busy), None)
                if idle is None:
                    raise Refused(503, "every conversation is answering a question")
                del self._items[idle]
            conversation = Conversation(astronaut_id, self._open(astronaut_id), now)
            self._items[conversation_id] = conversation
        conversation.busy = True
        conversation.last_used = now
        return conversation

    def release(self, conversation: Conversation) -> None:
        conversation.busy = False
        conversation.answer = None
        conversation.cancel_reason = None
        conversation.last_used = self._clock()

    def forget(self, conversation_id: str, astronaut_id: str) -> None:
        """Drop the conversation (nothing when unknown); an answer in progress is cancelled. Raises Refused(403)."""
        conversation = self._items.get(conversation_id)
        if conversation is None:
            return
        if conversation.astronaut_id != astronaut_id:
            raise Refused(403, "the conversation belongs to another astronaut")
        del self._items[conversation_id]
        conversation.cancel("invalid", "the conversation was forgotten")

    async def close(self) -> None:
        """Cancel every answer in progress (they report `unavailable`), wait for them, and forget everything."""
        tasks = [task for item in self._items.values() if (task := item.cancel("unavailable", "the agent is shutting down"))]
        self._items.clear()
        if tasks:
            await asyncio.wait(tasks)

    def _expire(self, now: float) -> None:
        for key in [key for key, item in self._items.items() if not item.busy and now - item.last_used > self._idle_ttl_s]:
            del self._items[key]
