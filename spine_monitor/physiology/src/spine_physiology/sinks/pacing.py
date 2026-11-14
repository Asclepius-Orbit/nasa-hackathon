"""Ritmo de emisión: tan rápido como se pueda (`speed=None`) o real/acelerado (`speed` × tiempo real).

Dueño: D1. El primer mensaje ancla el tiempo simulado (`Envelope.at`) al reloj de pared; cada mensaje
siguiente espera hasta `ancla_pared + (at − ancla_sim) / speed`. Nunca reordena ni altera los mensajes.
Reloj y sleep inyectables para tests deterministas.
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
        """`speed`: factor sobre el tiempo real (> 0), o None para no esperar nunca."""
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
    """Los mensajes en el mismo orden, cada uno cuando le toca según `pacer`."""
    for envelope in messages:
        await pacer.wait_for(envelope.at)
        yield envelope
