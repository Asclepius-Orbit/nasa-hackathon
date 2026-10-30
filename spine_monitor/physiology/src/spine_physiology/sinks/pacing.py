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
        raise NotImplementedError

    async def wait_for(self, at: datetime) -> None:
        raise NotImplementedError


async def paced(messages: Iterable[Envelope], pacer: Pacer) -> AsyncIterator[Envelope]:
    """Los mensajes en el mismo orden, cada uno cuando le toca según `pacer`."""
    raise NotImplementedError
    yield  # pragma: no cover  (hace de esto un generador asíncrono)
