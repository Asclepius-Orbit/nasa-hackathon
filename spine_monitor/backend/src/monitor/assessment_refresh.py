"""Periodic renewal of stale assessments.

An astronaut with a profile whose latest assessment is missing, or whose `as_of` is older than `stale_hours` (the rule of
the read-only status: strictly older), is handed to `Analysis.request_assess`. The pass only asks: the debounce, the skips
(no baseline, nothing to assess) and the failures are `request_assess`'s, which logs them; an astronaut that could not be
assessed is simply found stale again on the next tick. SQLite is the only state, so a restart goes on from there.

Concurrency: one task on the event loop, like `ProcessingRetrier` and `GroundSender`. A pass does no I/O besides the
store and never suspends, so it runs to the end between two awaits of the loop. An unexpected error ends only that pass:
it is logged (type only: its text may quote health data) and the loop goes on after the wait.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from monitor.analysis_retry import Wait
from monitor.db import Store
from monitor.ground.sender import wake_or_tick

_log = logging.getLogger(__name__)


class AssessmentRequester(Protocol):
    def request_assess(self, astronaut_id: str) -> None: ...


class StaleAssessmentRefresher:
    def __init__(
        self,
        store: Store,
        analysis: AssessmentRequester,
        *,
        clock: Callable[[], datetime],
        stale_hours: float,
        tick_s: float,
        wait: Wait = wake_or_tick,
    ) -> None:
        self._store = store
        self._analysis = analysis
        self._clock = clock
        self._stale = timedelta(hours=stale_hours)
        self._tick_s = tick_s
        self._wait = wait
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Something may have gone stale: do not wait for the tick."""
        self._wake.set()

    async def run(self) -> None:
        """Until cancelled. A pass that fails as a whole (say, a broken database) is tried again after the wait."""
        while True:
            self._wake.clear()  # before the pass: a wake during it makes the wait return at once
            try:
                self.refresh_due()
            except Exception as exc:
                _log.error("assessment refresh pass failed: %s", type(exc).__name__)
            await self._wait(self._wake, self._tick_s)

    def refresh_due(self) -> list[str]:
        """One pass: asks for a new assessment of every astronaut with a profile whose latest is missing or stale; returns their ids."""
        now = self._clock()
        due: list[str] = []
        for astronaut_id in self._store.profile_astronaut_ids():
            latest = self._store.latest_assessment(astronaut_id)
            if latest is not None and now - datetime.fromisoformat(latest["as_of"]) <= self._stale:
                continue
            self._analysis.request_assess(astronaut_id)
            due.append(astronaut_id)
        return due
