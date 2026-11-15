"""Gateway ports: what the app needs from the Arduino line source.

Types only, no logic. The real implementation is ``serial_source.SerialLineSource`` (pyserial in a thread) and the app tests
use a fake source that hands out ready-made lines. The MQTT ports (client, incoming messages) live in
``app`` because only it uses them.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SerialLine:
    """A raw line received from the Arduino (without the terminator) and the UTC instant at which it reached the gateway."""

    data: bytes
    received_at: datetime


@dataclass(frozen=True, slots=True)
class LinkUp:
    """The serial port has been opened (or reopened after an outage)."""

    at: datetime


@dataclass(frozen=True, slots=True)
class LinkDown:
    """The serial port was lost or could not be opened; the source retries on its own."""

    at: datetime
    reason: str


type SerialEvent = SerialLine | LinkUp | LinkDown


class LineSource(Protocol):
    """Source of serial link events.

    ``events()`` is an async generator that does not end by itself: it emits ``LinkUp`` on open, ``SerialLine`` for each line
    and ``LinkDown`` when the port is lost, and retries opening with backoff. It ends when the task consuming it is cancelled.
    """

    def events(self) -> AsyncIterator[SerialEvent]: ...
