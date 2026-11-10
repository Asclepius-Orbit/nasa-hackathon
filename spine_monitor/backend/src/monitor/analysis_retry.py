"""Retries of session analyses that failed for a transient reason (engine unreachable or 5xx).

`Analysis.process_session` decides what is transient and records each such failure with its next retry time
(`backoff_s`); this loop only picks the due ones from SQLite and runs them again, so a restart goes on from there.
A session whose retries reached `max_attempts`, or that failed for a final reason (4xx, contract, no chunks),
stays `processing_failed`. Startup recovery of interrupted sessions is `Analysis.reset_interrupted` and `process_pending`, not this loop.

Concurrency: one task on the event loop. A session is moved `processing_failed -> ended` with a compare-and-set
before it is processed, so a reprocess that races with it runs once. An unexpected error in one session is logged
(type only: its text may quote health data) and the pass goes on; that session may be left `processing` until the
next startup recovery.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from monitor.db import Store
from monitor.ground.sender import wake_or_tick

_log = logging.getLogger(__name__)

# 2.0 ** (attempt - 1) must not overflow a float; any delay reaches its maximum long before.
_MAX_DOUBLINGS = 64

type Wait = Callable[[asyncio.Event, float], Awaitable[None]]


class SessionProcessor(Protocol):
    async def process_session(self, session_id: str) -> None: ...


def backoff_s(attempt: int, min_s: float, max_s: float) -> float:
    """Seconds until the retry after the `attempt`-th failure (1-based): `min_s`, doubling, capped at `max_s`."""
    return min(max_s, min_s * 2.0 ** min(max(attempt - 1, 0), _MAX_DOUBLINGS))


class ProcessingRetrier:
    def __init__(
        self,
        store: Store,
        analysis: SessionProcessor,
        *,
        clock: Callable[[], datetime],
        max_attempts: int,
        tick_s: float,
        wait: Wait = wake_or_tick,
    ) -> None:
        self._store = store
        self._analysis = analysis
        self._clock = clock
        self._max_attempts = max_attempts
        self._tick_s = tick_s
        self._wait = wait
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Something may be due (say, a reprocess was asked for): do not wait for the tick."""
        self._wake.set()

    async def run(self) -> None:
        """Until cancelled. A pass that fails as a whole (say, a broken database) is tried again after the wait."""
        while True:
            self._wake.clear()  # before the pass: a wake during it makes the wait return at once
            try:
                await self.retry_due()
            except Exception as exc:
                _log.error("processing retry pass failed: %s", type(exc).__name__)
            await self._wait(self._wake, self._tick_s)

    async def retry_due(self) -> list[str]:
        """One pass: processes again every due session; returns those whose processing ran without raising."""
        done: list[str] = []
        for session_id in self._store.due_processing_retries(self._clock(), self._max_attempts):
            if not self._store.transition_session(session_id, ("processing_failed",), "ended"):
                continue
            try:
                await self._analysis.process_session(session_id)
            except Exception as exc:
                _log.error("session %s: retry of the analysis failed: %s", session_id, type(exc).__name__)
                continue
            done.append(session_id)
        return done
