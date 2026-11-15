"""Emission pacing: as fast as possible (`speed=None`) or real/accelerated (`speed` × real time).

Owner: D1. The first message anchors the simulated time (`Envelope.at`) to the wall clock; each following
message waits until `wall_anchor + (at − sim_anchor) / speed`. It never reorders or alters the messages.
Clock and sleep are injectable for deterministic tests.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from datetime import datetime

from spine_physiology.envelope import Envelope


class Pacer:
    def __init__(
        self,
        speed: float | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`speed`: factor over real time (> 0), or None to never wait."""
        if speed is not None and not speed > 0:
            raise ValueError("speed must be > 0 or None")
        self._speed = speed
        self._clock = clock
        self._sleep = sleep
        self._anchor_sim: datetime | None = None
        self._anchor_wall = 0.0

    async def wait_for(self, at: datetime) -> None:
        if self._speed is None:
            return
        if self._anchor_sim is None:
            self._anchor_sim = at
            self._anchor_wall = self._clock()
            return
        target = self._anchor_wall + (at - self._anchor_sim).total_seconds() / self._speed
        delay = target - self._clock()
        if delay > 0:
            await self._sleep(delay)


async def paced(messages: Iterable[Envelope], pacer: Pacer) -> AsyncIterator[Envelope]:
    """The messages in the same order, each one when its turn comes according to `pacer`."""
    for envelope in messages:
        await pacer.wait_for(envelope.at)
        yield envelope
