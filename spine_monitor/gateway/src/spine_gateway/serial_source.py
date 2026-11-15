"""Real source of Arduino lines: pyserial in a thread, splitting on ``\\n`` and reconnection with backoff.

Concurrency decision: each blocking read (``to_thread``) is a short call with a 0.1 s timeout, so that
there is no dedicated thread nor queue between thread and loop; the buffer is touched only by the ``events()`` coroutine (a single writer) and the
thread only returns ``(bytes, instant)``. The instant is taken in the thread, right when the read returns, so that it does not depend on
when the consumer attends to the event. If the consumer is cancelled during a read, it waits (at most for the timeout)
for the thread to finish before closing the port, so as not to close it under its feet nor leave threads hanging.

The line is delivered unprocessed (without the terminator, tolerating ``\\r\\n``): parsing and CRC are done by ``protocol``.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Protocol

import serial

from spine_gateway.ports import LinkDown, LinkUp, SerialEvent, SerialLine

# Pending bytes without ``\n`` beyond which it is considered unsynchronised noise and discarded.
MAX_PENDING_BYTES = 4096
# Timeout (s) of each read: bounds how long the thread takes to exit after cancelling and how long to wait before closing.
READ_TIMEOUT_S = 0.1


class SerialPort(Protocol):
    """The minimum needed from a serial port (``serial.Serial`` satisfies it)."""

    @property
    def in_waiting(self) -> int: ...

    def read(self, size: int = 1, /) -> bytes: ...

    def close(self) -> None: ...


type PortOpener = Callable[[str, int], SerialPort]


def open_serial_port(url: str, baud: int) -> SerialPort:
    """Opens ``COM3``, ``/dev/ttyACM0``, ``socket://host:port`` or ``loop://`` with a short read timeout."""
    return serial.serial_for_url(url, baudrate=baud, timeout=READ_TIMEOUT_S)


def _describe(error: OSError) -> str:
    return f"{type(error).__name__}: {error}"


class SerialLineSource:
    """Implements ``ports.LineSource`` over a serial port (see ``events()``)."""

    def __init__(
        self,
        url: str,
        baud: int,
        clock: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        opener: PortOpener = open_serial_port,
        *,
        reconnect_initial_s: float,
        reconnect_factor: float,
        reconnect_max_s: float,
    ) -> None:
        self._url = url
        self._baud = baud
        self._clock = clock
        self._sleep = sleep
        self._opener = opener
        self._initial = reconnect_initial_s
        self._factor = reconnect_factor
        self._max = reconnect_max_s

    async def events(self) -> AsyncIterator[SerialEvent]:
        """Emits ``LinkUp`` on open, ``SerialLine`` per line and ``LinkDown`` once per outage; does not end by itself."""
        delay = self._initial
        down_reported = False
        while True:
            port: SerialPort | None = None
            reason: str
            try:
                port = await asyncio.to_thread(self._opener, self._url, self._baud)
                delay = self._initial
                down_reported = False
                yield LinkUp(self._clock())
                pending = bytearray()
                while True:
                    data, at = await self._read_chunk(port)
                    pending += data
                    *complete, pending = pending.split(b"\n")
                    for raw in complete:
                        line = raw.removesuffix(b"\r")
                        if line:
                            yield SerialLine(bytes(line), at)
                    if len(pending) > MAX_PENDING_BYTES:
                        pending.clear()
            except OSError as error:  # SerialException is a subclass of OSError
                reason = _describe(error)
            finally:
                if port is not None:
                    with contextlib.suppress(OSError):
                        port.close()
            if not down_reported:
                down_reported = True
                yield LinkDown(self._clock(), reason)
            await self._sleep(delay)
            delay = min(delay * self._factor, self._max)

    async def _read_chunk(self, port: SerialPort) -> tuple[bytes, datetime]:
        read = asyncio.ensure_future(asyncio.to_thread(self._read, port))
        try:
            return await asyncio.shield(read)
        except asyncio.CancelledError:
            # The thread cannot be interrupted: wait for its short read to finish before `events` closes the port.
            await asyncio.wait({read})
            read.exception()  # the shield prevents `read` from being cancelled; the exception is marked as retrieved (no GC warning)
            raise

    def _read(self, port: SerialPort) -> tuple[bytes, datetime]:
        data = port.read(max(1, port.in_waiting))
        return data, self._clock()
