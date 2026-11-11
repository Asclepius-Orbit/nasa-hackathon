"""ProcessingRetrier: analyses that failed for a transient reason are tried again with backoff, up to a limit.

No wall clock: the clock is injected, `retry_due` is one pass of the loop, and `run` parks in an injected wait driven by the test.
"""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from monitor.analysis import Analysis
from monitor.analysis_retry import ProcessingRetrier, backoff_s
from monitor.db import Store
from monitor.engine import EngineError

pytestmark = pytest.mark.anyio

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
NOW = datetime(2026, 11, 13, 10, 30, tzinfo=UTC)
FIRST, SECOND = "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class Engine:
    """Answers with the examples; `down` makes it unreachable, `rejecting` makes it answer 422, `broken` raises an unexpected error for those sessions."""

    def __init__(self) -> None:
        self.down = False
        self.rejecting = False
        self.broken: set[str] = set()
        self.features_calls: list[str] = []

    async def features(self, request: dict[str, Any]) -> dict[str, Any]:
        session = request["session"]
        self.features_calls.append(session["session_id"])
        if session["session_id"] in self.broken:
            raise RuntimeError("bug")
        if self.rejecting:
            raise EngineError("/features: rejected", status=422)
        if self.down:
            raise EngineError("/features: engine unreachable") from httpx.ConnectError("refused")
        return example("domain/feature-set.preflight") | {"session_id": session["session_id"], "astronaut_id": session["astronaut_id"]}

    async def baseline(self, request: dict[str, Any]) -> dict[str, Any]:
        return example("domain/baseline") | {"version": request.get("previous_baseline_version", 0) + 1, "baseline_id": str(uuid.uuid4())}

    async def assess(self, request: dict[str, Any]) -> dict[str, Any]:
        return example("domain/assessment.amber")


class Steps:
    """Injected wait of `run`: parks until the test lets it go, and reports whether a wake was pending."""

    def __init__(self) -> None:
        self.parked: asyncio.Queue[bool] = asyncio.Queue()
        self._go = asyncio.Event()

    async def __call__(self, wake: asyncio.Event, tick_s: float) -> None:
        self.parked.put_nowait(wake.is_set())
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
def engine() -> Engine:
    return Engine()


@pytest.fixture
def analysis(store: Store, engine: Engine, clock: Clock) -> Analysis:
    return Analysis(store, engine, window_days=14, clock=clock, retry_min_s=30, retry_max_s=900)  # pyright: ignore[reportArgumentType]


def retrier(store: Store, analysis: Analysis, clock: Clock, *, max_attempts: int = 5, wait: Any = None) -> ProcessingRetrier:
    extra = {} if wait is None else {"wait": wait}
    return ProcessingRetrier(store, analysis, clock=clock, max_attempts=max_attempts, tick_s=5, **extra)


async def failed_session(store: Store, analysis: Analysis, engine: Engine, session_id: str = FIRST) -> str:
    """A session whose analysis failed once because the engine was down; the engine is left down."""
    session = example("domain/session.exercise") | {"session_id": session_id}
    store.insert_session(session, "ended")
    # Another seq per session: chunks are keyed by device, stream, boot and seq.
    store.add_chunk(example("telemetry/stream-chunk.emg") | {"session_id": session_id, "seq": int(session_id[-4:], 16)})
    engine.down = True
    await analysis.process_session(session_id)
    assert store.session(session_id).state == "processing_failed"
    return session_id


@contextlib.asynccontextmanager
async def running(loop: ProcessingRetrier) -> AsyncIterator[asyncio.Task[None]]:
    task = asyncio.create_task(loop.run())
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.property
@given(attempt=st.integers(1, 10_000), min_s=st.floats(0.001, 1e4), spread=st.floats(1, 1e3))
def test_backoff_grows_within_its_bounds(attempt: int, min_s: float, spread: float) -> None:
    max_s = min_s * spread
    now, after = backoff_s(attempt, min_s, max_s), backoff_s(attempt + 1, min_s, max_s)
    assert min_s <= now <= after <= max_s


def test_backoff_starts_at_the_minimum_and_doubles_until_the_cap() -> None:
    assert [backoff_s(n, 30, 900) for n in range(1, 8)] == [30, 60, 120, 240, 480, 900, 900]


async def test_due_session_is_processed_once_the_engine_is_back(store, engine, analysis, clock):
    session_id = await failed_session(store, analysis, engine)
    engine.down = False
    clock.advance(seconds=30)

    assert await retrier(store, analysis, clock).retry_due() == [session_id]

    assert store.session(session_id).state == "processed"
    assert store.processing_attempt(session_id) is None


async def test_nothing_is_retried_before_its_time(store, engine, analysis, clock):
    session_id = await failed_session(store, analysis, engine)
    engine.down = False
    clock.advance(seconds=29)

    assert await retrier(store, analysis, clock).retry_due() == []

    assert store.session(session_id).state == "processing_failed"
    assert engine.features_calls == [session_id]


async def test_retries_stop_at_the_limit(store, engine, analysis, clock):
    session_id = await failed_session(store, analysis, engine)
    loop = retrier(store, analysis, clock, max_attempts=2)
    clock.advance(hours=1)
    await loop.retry_due()
    clock.advance(hours=1)

    assert await loop.retry_due() == []

    assert store.session(session_id).state == "processing_failed"
    assert engine.features_calls == [session_id, session_id]


async def test_a_final_failure_after_a_transient_one_is_not_retried_again(store, engine, analysis, clock):
    session_id = await failed_session(store, analysis, engine)
    engine.rejecting = True
    loop = retrier(store, analysis, clock)
    clock.advance(seconds=30)
    assert await loop.retry_due() == [session_id]
    clock.advance(hours=1)

    assert await loop.retry_due() == []

    assert store.session(session_id).state == "processing_failed"
    assert store.processing_attempt(session_id) is None
    assert engine.features_calls == [session_id, session_id]


async def test_an_unexpected_error_in_one_session_does_not_stop_the_others(store, engine, analysis, clock):
    await failed_session(store, analysis, engine, FIRST)
    await failed_session(store, analysis, engine, SECOND)
    engine.down = False
    engine.broken = {FIRST}
    clock.advance(seconds=30)

    assert await retrier(store, analysis, clock).retry_due() == [SECOND]

    assert store.session(SECOND).state == "processed"


async def test_run_retries_on_wake_and_stops_on_cancel(store, engine, analysis, clock):
    session_id = await failed_session(store, analysis, engine)
    engine.down = False
    steps = Steps()
    loop = retrier(store, analysis, clock, wait=steps)

    async with running(loop) as task:
        assert await asyncio.wait_for(steps.parked.get(), 5) is False  # first pass: nothing due yet
        clock.advance(seconds=30)
        loop.wake()
        steps.go()
        assert await asyncio.wait_for(steps.parked.get(), 5) is False  # the wake was consumed by the pass
        assert store.session(session_id).state == "processed"
    assert task.cancelled()
