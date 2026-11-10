"""GroundSender: sends the outbox while the link is up, backs off per entry, loses nothing on a failure, a cancel or a restart.

No wall clock: the clock is injected, `pump` is one pass of the loop, and `run` parks in an injected wait driven by the test.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from monitor.db import GroundKey, GroundOutboxItem, Store, utc_key
from monitor.ground.link import GroundWindows
from monitor.ground.sender import GroundSender, retry_delay_s, wake_or_tick
from monitor.ground.transport import FakeGroundTransport, GroundReceipt
from monitor.views.ground_summary import build_ground_summary

pytestmark = pytest.mark.anyio

NOW = datetime(2027, 2, 9, 9, 0, tzinfo=UTC)
D1, D2 = date(2027, 2, 8), date(2027, 2, 9)
AST, OTHER = "AST-01", "AST-02"
K1, K2 = GroundKey(AST, D1, 1), GroundKey(AST, D2, 1)
UP_NOW = GroundWindows(NOW, period_min=90, up_min=10)
MARKER = "MARKER-pain-in-the-lower-back"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class Spy(FakeGroundTransport):
    """The real fake with hooks around the delivery, to fail or to stop in the middle of a send."""

    def __init__(self, clock: Clock) -> None:
        super().__init__(clock)
        self.calls: list[GroundKey] = []
        self.before: Callable[[], Awaitable[None]] | None = None
        self.after: Callable[[], Awaitable[None]] | None = None

    async def send(self, key: GroundKey, payload: dict[str, Any]) -> GroundReceipt:
        self.calls.append(key)
        if self.before is not None:
            await self.before()
        receipt = await super().send(key, payload)
        if self.after is not None:
            await self.after()
        return receipt


class Steps:
    """Injected wait of `run`: the loop parks here until the test lets it go, and reports what it saw."""

    def __init__(self) -> None:
        self.parked: asyncio.Queue[tuple[bool, float]] = asyncio.Queue()
        self._go = asyncio.Event()

    async def __call__(self, wake: asyncio.Event, tick_s: float) -> None:
        self.parked.put_nowait((wake.is_set(), tick_s))
        await self._go.wait()
        self._go.clear()

    def go(self) -> None:
        self._go.set()


@pytest.fixture
def store() -> Iterator[Store]:
    opened = Store(":memory:")
    yield opened
    opened.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ground(clock: Clock) -> Spy:
    return Spy(clock)


def queue(store: Store, day: date = D1, mission_day: int | None = None, astronaut: str = AST) -> int:
    return store.upsert_ground_summary(astronaut, day, mission_day, lambda rev: build_ground_summary(astronaut, day, mission_day, rev, NOW, None, [], []), NOW)


def sender(
    store: Store, ground: FakeGroundTransport, clock: Clock, windows: GroundWindows | None = UP_NOW, wait: Any = wake_or_tick, send_timeout_s: float = 30
) -> GroundSender:
    return GroundSender(store, ground, windows, clock=clock, retry_min_s=1, retry_max_s=300, tick_s=5, send_timeout_s=send_timeout_s, wait=wait)


def item(store: Store, key: GroundKey = K1) -> GroundOutboxItem:
    (found,) = [i for i in store.ground_outbox_items(key.astronaut_id, 50) if i.key == key]
    return found


@contextlib.asynccontextmanager
async def running(loop: GroundSender) -> AsyncIterator[asyncio.Task[None]]:
    task = asyncio.create_task(loop.run())
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def hanging(entered: asyncio.Event) -> Callable[[], Awaitable[None]]:
    async def hang() -> None:
        entered.set()
        await asyncio.Event().wait()

    return hang


# --- the link ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("windows", [None, GroundWindows(NOW - timedelta(minutes=30), period_min=90, up_min=10)], ids=["not_configured", "down"])
async def test_without_a_link_it_queues_and_never_attempts(store, ground, clock, windows):
    queue(store)

    await sender(store, ground, clock, windows).pump()

    assert ground.calls == []
    assert (item(store).state, item(store).attempts) == ("queued", 0)


async def test_it_sends_when_the_window_opens_as_the_clock_advances(store, ground, clock):
    queue(store)
    loop = sender(store, ground, clock, GroundWindows(NOW + timedelta(minutes=30), period_min=90, up_min=10))
    await loop.pump()
    assert ground.calls == []

    clock.advance(minutes=30)
    await loop.pump()

    assert ground.calls == [K1]
    assert (item(store).state, item(store).received_at) == ("sent", utc_key(clock()))


async def test_it_stops_sending_when_the_window_closes_during_a_pass(store, ground, clock):
    queue(store, D1)
    queue(store, D2)

    async def close_the_window() -> None:
        clock.advance(minutes=10)

    ground.after = close_the_window

    await sender(store, ground, clock).pump()

    assert ground.calls == [K1]
    assert item(store, K2).state == "queued"


# --- failures ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("attempts", "min_s", "max_s", "delay"),
    [(0, 1, 300, 1), (1, 1, 300, 2), (2, 1, 300, 4), (8, 1, 300, 256), (9, 1, 300, 300), (5000, 1, 300, 300), (0, 3, 10, 3), (1, 3, 10, 6), (2, 3, 10, 10)],
)
def test_the_delay_doubles_per_attempt_up_to_the_maximum(attempts, min_s, max_s, delay):
    assert retry_delay_s(attempts, min_s, max_s) == delay


async def test_failures_back_off_exponentially_and_the_retry_does_not_duplicate(store, ground, clock):
    queue(store)
    ground.fail_next("no_carrier", times=3)
    loop = sender(store, ground, clock)

    for attempts, wait_s in ((1, 1), (2, 2), (3, 4)):
        await loop.pump()
        found = item(store)
        assert (found.state, found.attempts, found.last_error) == ("queued", attempts, "no_carrier")
        assert found.next_attempt_at == utc_key(clock() + timedelta(seconds=wait_s))
        clock.advance(seconds=wait_s - 0.5)
        calls = len(ground.calls)
        await loop.pump()
        assert len(ground.calls) == calls, "retried before its time"
        clock.advance(seconds=0.5)

    await loop.pump()

    assert item(store).state == "sent"
    assert [d.key for d in ground.deliveries] == [K1]


async def test_an_unexpected_exception_is_a_failure_with_a_code_and_without_its_text(store, ground, clock, caplog):
    queue(store)

    async def boom() -> None:
        raise RuntimeError(MARKER)

    ground.before = boom

    with caplog.at_level(logging.DEBUG):
        await sender(store, ground, clock).pump()

    found = item(store)
    assert (found.state, found.attempts, found.last_error) == ("queued", 1, "unexpected")
    assert found.next_attempt_at == utc_key(clock() + timedelta(seconds=1))
    assert "RuntimeError" in caplog.text
    assert MARKER not in caplog.text


async def test_a_send_that_never_answers_times_out_as_a_failure_with_backoff(store, ground, clock):
    queue(store)
    ground.before = hanging(asyncio.Event())

    await asyncio.wait_for(sender(store, ground, clock, send_timeout_s=0.05).pump(), 5)

    found = item(store)
    assert (found.state, found.attempts, found.last_error) == ("queued", 1, "timeout")
    assert found.next_attempt_at == utc_key(clock() + timedelta(seconds=1))


async def test_a_receipt_that_cannot_be_recorded_is_a_failure_and_the_repeat_is_idempotent(store, ground, clock):
    queue(store)

    class NaiveReceipt(Spy):
        async def send(self, key: GroundKey, payload: dict[str, Any]) -> GroundReceipt:
            receipt = await super().send(key, payload)
            return GroundReceipt(received_at=receipt.received_at.replace(tzinfo=None))

    naive = NaiveReceipt(clock)
    loop = sender(store, naive, clock)

    await loop.pump()

    found = item(store)
    assert (found.state, found.attempts, found.last_error) == ("queued", 1, "bad_receipt")
    assert found.next_attempt_at == utc_key(clock() + timedelta(seconds=1))
    assert len(naive.calls) == 1, "retried before its time"

    clock.advance(seconds=1)
    await loop.pump()

    assert (item(store).attempts, len(naive.calls)) == (2, 2)
    assert [d.key for d in naive.deliveries] == [K1], "the repeat reached the ground as a repetition, not as a second delivery"


async def test_a_failing_entry_does_not_hold_another_astronaut(store, ground, clock):
    queue(store, D1)
    queue(store, D2, astronaut=OTHER)
    ground.fail_next("no_carrier")

    await sender(store, ground, clock).pump()

    assert ground.calls == [K1, GroundKey(OTHER, D2, 1)]
    assert item(store, GroundKey(OTHER, D2, 1)).state == "sent"


async def test_a_pass_over_a_long_outbox_lets_the_event_loop_run_between_entries(store, ground, clock):
    for day in (D1, D2, date(2027, 2, 10)):
        queue(store, day)
    turns = 0

    async def other_work() -> None:
        nonlocal turns
        while True:
            turns += 1
            await asyncio.sleep(0)

    other = asyncio.create_task(other_work())
    await asyncio.sleep(0)
    turns = 0
    await sender(store, ground, clock).pump()
    other.cancel()
    await asyncio.gather(other, return_exceptions=True)

    assert len(ground.calls) == 3
    assert turns >= 3


# --- order and revisions ---------------------------------------------------------------------------------


async def test_the_oldest_day_goes_first_and_a_failed_one_is_not_overtaken(store, ground, clock):
    queue(store, D2)
    queue(store, D1)
    ground.fail_next("no_carrier")
    loop = sender(store, ground, clock)

    await loop.pump()
    assert ground.calls == [K1]

    clock.advance(seconds=1)
    await loop.pump()

    assert ground.calls == [K1, K1, K2]


async def test_a_change_after_the_send_goes_as_a_new_revision(store, ground, clock):
    queue(store, D1)
    loop = sender(store, ground, clock)
    await loop.pump()

    assert queue(store, D1, mission_day=7) == 2
    await loop.pump()

    second = GroundKey(AST, D1, 2)
    assert (item(store).state, item(store, second).state) == ("sent", "sent")
    assert (ground.received[K1]["mission_day"], ground.received[second]["mission_day"]) == (None, 7)


async def test_an_update_during_the_send_is_sent_again_at_once_without_backoff(store, ground, clock):
    queue(store, D1)

    async def update() -> None:
        ground.before = None
        queue(store, D1, mission_day=7)

    ground.before = update

    await sender(store, ground, clock).pump()

    found = item(store)
    assert (found.state, found.attempts, found.next_attempt_at, found.payload["mission_day"]) == ("sent", 0, None, 7)
    assert [d.payload["mission_day"] for d in ground.deliveries] == [None, 7]


# --- the loop --------------------------------------------------------------------------------------------


async def test_the_loop_survives_a_broken_store_and_logs_no_text(store, ground, clock, monkeypatch, caplog):
    queue(store)
    steps = Steps()
    real = store.next_ground_item

    def broken(_: datetime) -> None:
        monkeypatch.setattr(store, "next_ground_item", real)
        raise RuntimeError(MARKER)

    monkeypatch.setattr(store, "next_ground_item", broken)

    with caplog.at_level(logging.DEBUG):
        async with running(sender(store, ground, clock, wait=steps)):
            await steps.parked.get()
            assert ground.calls == []
            steps.go()
            await steps.parked.get()

    assert ground.calls == [K1]
    assert item(store).state == "sent"
    assert "RuntimeError" in caplog.text
    assert MARKER not in caplog.text


async def test_a_wake_before_a_pass_is_consumed_and_one_during_it_is_kept(store, ground, clock):
    steps = Steps()
    loop = sender(store, ground, clock, wait=steps)
    loop.wake()

    async def enqueued_meanwhile() -> None:
        ground.before = None
        loop.wake()

    async with running(loop):
        assert await steps.parked.get() == (False, 5)
        queue(store)
        ground.before = enqueued_meanwhile
        steps.go()
        assert await steps.parked.get() == (True, 5)


async def test_the_default_wait_ends_on_the_wake() -> None:
    wake = asyncio.Event()
    wake.set()

    await wake_or_tick(wake, 3600)  # would hit the pytest timeout if it waited for the tick


async def test_the_default_wait_ends_on_the_tick() -> None:
    await wake_or_tick(asyncio.Event(), 0)


# --- cancellation and restart ------------------------------------------------------------------------------


async def test_a_cancel_during_the_send_leaves_the_entry_queued_and_untouched(store, ground, clock):
    queue(store)
    entered = asyncio.Event()
    ground.before = hanging(entered)

    task = asyncio.create_task(sender(store, ground, clock).run())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    found = item(store)
    assert (found.state, found.attempts, found.last_error, found.next_attempt_at) == ("queued", 0, None, None)


async def test_a_send_cancelled_after_reaching_the_ground_is_repeated_idempotently(store, ground, clock):
    queue(store)
    entered = asyncio.Event()
    ground.after = hanging(entered)
    task = asyncio.create_task(sender(store, ground, clock).run())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert item(store).state == "queued"

    ground.after = None
    clock.advance(minutes=1)
    await sender(store, ground, clock).pump()

    assert (item(store).state, item(store).received_at) == ("sent", utc_key(NOW))
    assert [d.key for d in ground.deliveries] == [K1]


async def test_after_a_restart_it_goes_on_from_sqlite_keeping_attempts_and_backoff(tmp_path: Path, ground, clock):
    path = tmp_path / "app.db"
    before = Store(path)
    queue(before, D1)
    queue(before, D2)
    ground.fail_next("no_carrier")
    steps = Steps()
    async with running(sender(before, ground, clock, wait=steps)):
        await steps.parked.get()
    assert item(before).attempts == 1
    before.close()

    after = Store(path)
    try:
        other_ground = Spy(clock)
        loop = sender(after, other_ground, clock)
        await loop.pump()
        assert other_ground.calls == [], "the backoff of the entry did not survive"

        clock.advance(seconds=1)
        await loop.pump()

        assert other_ground.calls == [K1, K2]
        assert (item(after).state, item(after).attempts, item(after, K2).state) == ("sent", 1, "sent")
    finally:
        after.close()
