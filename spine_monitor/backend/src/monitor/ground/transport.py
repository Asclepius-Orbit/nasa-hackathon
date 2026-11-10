"""Port towards the ground station and its only adapter today, an in-process fake that the demo also runs on.

The port is idempotent by contract: the same key with the same content is a repetition (the original receipt comes back
and nothing is stored twice), so a send cancelled after it reached the ground can be repeated safely. The same key with
other content is a replacement, with a new receipt.
"""

import copy
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from spine_contracts import validation

from monitor.db import GroundKey


class GroundSendError(Exception):
    """A send that failed. Carries a code, never a text: it ends up in the outbox and in the UI."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GroundReceipt:
    received_at: datetime


class GroundTransport(Protocol):
    async def send(self, key: GroundKey, payload: dict[str, Any]) -> GroundReceipt:
        """Delivers one revision of a summary; raises GroundSendError when the ground did not take it."""
        ...


@dataclass(frozen=True)
class GroundDelivery:
    key: GroundKey
    payload: dict[str, Any]
    received_at: datetime


class FakeGroundTransport:
    """The ground station, in process. `deliveries` lists what it accepted, in order; a repetition adds nothing."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._latest: dict[GroundKey, GroundDelivery] = {}
        self._failures: deque[str] = deque()
        self.deliveries: list[GroundDelivery] = []

    def fail_next(self, code: str, times: int = 1) -> None:
        """The next `times` sends fail with `code`, after any failure already injected."""
        self._failures.extend([code] * times)

    @property
    def received(self) -> dict[GroundKey, dict[str, Any]]:
        """Latest content accepted per key."""
        return {key: delivery.payload for key, delivery in self._latest.items()}

    async def send(self, key: GroundKey, payload: dict[str, Any]) -> GroundReceipt:
        if self._failures:
            raise GroundSendError(self._failures.popleft())
        # Only the verdict: the message of a violation quotes the offending value, which may be health data.
        if validation.errors("app/ground-summary", payload):
            raise GroundSendError("invalid_payload")
        known = self._latest.get(key)
        if known is not None and known.payload == payload:
            return GroundReceipt(known.received_at)
        delivery = GroundDelivery(key, copy.deepcopy(payload), self._clock())
        self._latest[key] = delivery
        self.deliveries.append(delivery)
        return GroundReceipt(delivery.received_at)
