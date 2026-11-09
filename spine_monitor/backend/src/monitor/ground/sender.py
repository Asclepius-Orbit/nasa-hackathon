"""Background loop that empties the ground outbox while the link is up. SQLite is its only state, so a restart goes on from there.

Concurrency: it runs on the event loop as one task, and it is the only sender. The store is used from the loop, like everywhere
else in the app (its single connection is not for other threads), and only the transport suspends. Between the end of a send and
its `mark_ground_sent` there is no suspension point, so a cancel lands either during the send, leaving the entry queued (the port
is idempotent, so repeating it is safe), or at the yield between entries, with nothing in flight. Whatever changes an entry during
a send is detected by `mark_ground_sent`, which compares the payload that was sent.

Logs and stored errors carry codes and exception types only: the text of an exception may quote health data.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from monitor.db import GroundOutboxItem, Store
from monitor.ground.link import GroundWindows, link_state
from monitor.ground.transport import GroundSendError, GroundTransport

_log = logging.getLogger(__name__)

UNEXPECTED_ERROR = "unexpected"
# Attempts have no limit: 2.0 ** attempts must not overflow a float after days without ground. Any delay reaches its maximum long before.
_MAX_DOUBLINGS = 64

type Wait = Callable[[asyncio.Event, float], Awaitable[None]]


def retry_delay_s(attempts: int, min_s: float, max_s: float) -> float:
    """Seconds until the retry of an entry that had failed `attempts` times before this failure."""
    return min(max_s, min_s * 2.0 ** min(attempts, _MAX_DOUBLINGS))


async def wake_or_tick(wake: asyncio.Event, tick_s: float) -> None:
    """Default wait of the loop: until something is queued or the tick, which notices opened windows and elapsed backoffs."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(wake.wait(), tick_s)


class GroundSender:
    def __init__(
        self,
        store: Store,
        transport: GroundTransport,
        windows: GroundWindows | None,
        *,
        clock: Callable[[], datetime],
        retry_min_s: float,
        retry_max_s: float,
        tick_s: float,
        wait: Wait = wake_or_tick,
    ) -> None:
        self._store = store
        self._transport = transport
        self._windows = windows
        self._clock = clock
        self._retry_min_s = retry_min_s
        self._retry_max_s = retry_max_s
        self._tick_s = tick_s
        self._wait = wait
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Something was queued: do not wait for the tick."""
        self._wake.set()

    async def run(self) -> None:
        """Until cancelled. Nothing else ends it: a pass that fails (say, a broken database) is tried again after the wait."""
        while True:
            self._wake.clear()  # before the pass: a wake during it means the wait below returns at once, so it is never lost
            try:
                await self.pump()
            except Exception as exc:
                _log.error("ground outbox pass failed: %s", type(exc).__name__)
            await self._wait(self._wake, self._tick_s)

    async def pump(self) -> None:
        """One pass: sends every entry that is due while the link stays up."""
        while self._link_up():
            await asyncio.sleep(0)  # a long outbox and an instant transport must not hold the loop, and a cancel can land here
            item = self._store.next_ground_item(self._clock())
            if item is None:
                return
            await self._send(item)

    def _link_up(self) -> bool:
        link = link_state(self._windows, self._clock())
        return link is not None and link["state"] == "up"

    async def _send(self, item: GroundOutboxItem) -> None:
        try:
            receipt = await self._transport.send(item.key, item.payload)
        except GroundSendError as exc:
            self._failed(item, exc.code)
        except Exception as exc:
            _log.error("ground send failed unexpectedly: %s", type(exc).__name__)
            self._failed(item, UNEXPECTED_ERROR)
        else:
            # False: it was updated during the send, so it is still queued and the next turn of `pump` sends the new content.
            self._store.mark_ground_sent(item.key, receipt.received_at, item.payload)

    def _failed(self, item: GroundOutboxItem, code: str) -> None:
        delay = retry_delay_s(item.attempts, self._retry_min_s, self._retry_max_s)
        self._store.mark_ground_failed(item.key, code, self._clock() + timedelta(seconds=delay))
