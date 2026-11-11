"""StaleAssessmentRefresher: an astronaut whose latest assessment is missing or too old is reassessed, one pass per tick.

No wall clock: the clock is injected, `refresh_due` is one pass of the loop, and `run` parks in an injected wait driven by the test.
The analysis is replaced by a spy at the boundary: what matters here is whom the loop asks it to assess.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from monitor.assessment_refresh import StaleAssessmentRefresher
from monitor.db import Store

pytestmark = pytest.mark.anyio

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
NOW = datetime(2026, 11, 13, 10, 30, tzinfo=UTC)
STALE_HOURS = 24.0


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


class SpyAnalysis:
    """Records `request_assess`; `failing` makes it raise an error whose text quotes health data."""

    def __init__(self) -> None:
        self.requested: list[str] = []
        self.failing = False

    def request_assess(self, astronaut_id: str) -> None:
        if self.failing:
            raise RuntimeError("pain 9/10 in L4")
        self.requested.append(astronaut_id)


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
def analysis() -> SpyAnalysis:
    return SpyAnalysis()


@pytest.fixture
def make_refresher(store: Store, analysis: SpyAnalysis, clock: Clock) -> Callable[..., StaleAssessmentRefresher]:
    def make(*, wait: Any = None) -> StaleAssessmentRefresher:
        extra = {} if wait is None else {"wait": wait}
        return StaleAssessmentRefresher(store, analysis, clock=clock, stale_hours=STALE_HOURS, tick_s=5, **extra)  # pyright: ignore[reportArgumentType]

    return make


@pytest.fixture
def add_astronaut(store: Store) -> Callable[..., None]:
    def add(astronaut_id: str, *, age: timedelta | None, profile: bool = True) -> None:
        """An astronaut with a profile (unless `profile` is False) and, when `age` is given, an assessment that old."""
        if profile:
            store.put_profile(example("domain/astronaut-profile") | {"astronaut_id": astronaut_id})
        if age is not None:
            as_of = (NOW - age).isoformat()
            store.put_assessment(example("domain/assessment.amber") | {"astronaut_id": astronaut_id, "assessment_id": f"as-{astronaut_id}", "as_of": as_of})

    return add


@contextlib.asynccontextmanager
async def running(loop: StaleAssessmentRefresher) -> AsyncIterator[asyncio.Task[None]]:
    task = asyncio.create_task(loop.run())
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_an_astronaut_without_any_assessment_is_requested(make_refresher, add_astronaut):
    add_astronaut("AST-01", age=None)

    assert make_refresher().refresh_due() == ["AST-01"]


def test_a_fresh_assessment_is_left_alone(make_refresher, analysis, add_astronaut):
    add_astronaut("AST-01", age=timedelta(hours=1))

    assert make_refresher().refresh_due() == []
    assert analysis.requested == []


def test_an_assessment_exactly_at_the_threshold_is_not_stale(make_refresher, add_astronaut):
    add_astronaut("AST-01", age=timedelta(hours=STALE_HOURS))

    assert make_refresher().refresh_due() == []


def test_an_assessment_just_past_the_threshold_is_requested(make_refresher, analysis, add_astronaut):
    add_astronaut("AST-01", age=timedelta(hours=STALE_HOURS, seconds=1))

    assert make_refresher().refresh_due() == ["AST-01"]
    assert analysis.requested == ["AST-01"]


def test_the_latest_assessment_decides_not_an_older_one(make_refresher, store, add_astronaut):
    add_astronaut("AST-01", age=timedelta(days=3))
    newer = {"astronaut_id": "AST-01", "assessment_id": "newer", "as_of": (NOW - timedelta(hours=2)).isoformat()}
    store.put_assessment(example("domain/assessment.amber") | newer)

    assert make_refresher().refresh_due() == []


def test_an_astronaut_without_a_profile_is_not_requested(make_refresher, add_astronaut):
    add_astronaut("AST-01", age=timedelta(days=3), profile=False)

    assert make_refresher().refresh_due() == []


def test_only_the_stale_astronauts_are_requested_in_id_order(make_refresher, analysis, add_astronaut):
    add_astronaut("AST-03", age=None)
    add_astronaut("AST-02", age=timedelta(hours=1))
    add_astronaut("AST-01", age=timedelta(days=2))

    assert make_refresher().refresh_due() == ["AST-01", "AST-03"]
    assert analysis.requested == ["AST-01", "AST-03"]


def test_an_assessment_becomes_stale_as_the_clock_moves(make_refresher, clock, add_astronaut):
    add_astronaut("AST-01", age=timedelta(hours=23))
    refresher = make_refresher()
    assert refresher.refresh_due() == []

    clock.advance(hours=1, seconds=1)

    assert refresher.refresh_due() == ["AST-01"]


async def test_run_does_a_pass_per_tick_and_stops_on_cancel(make_refresher, clock, analysis, add_astronaut):
    add_astronaut("AST-01", age=timedelta(hours=23))
    steps = Steps()

    async with running(make_refresher(wait=steps)) as task:
        assert await asyncio.wait_for(steps.parked.get(), 5) is False  # first pass: still fresh
        assert analysis.requested == []
        clock.advance(hours=2)
        steps.go()
        assert await asyncio.wait_for(steps.parked.get(), 5) is False
        assert analysis.requested == ["AST-01"]
    assert task.cancelled()


async def test_a_failing_pass_is_logged_by_type_only_and_the_loop_goes_on(make_refresher, analysis, add_astronaut, caplog):
    add_astronaut("AST-01", age=None)
    analysis.failing = True
    steps = Steps()

    with caplog.at_level(logging.ERROR, logger="monitor.assessment_refresh"):
        async with running(make_refresher(wait=steps)):
            await asyncio.wait_for(steps.parked.get(), 5)  # the first pass raised
            analysis.failing = False
            steps.go()
            await asyncio.wait_for(steps.parked.get(), 5)
            assert analysis.requested == ["AST-01"]

    assert "RuntimeError" in caplog.text
    assert "pain" not in caplog.text


async def test_wake_triggers_a_pass_without_waiting_for_the_tick(make_refresher, analysis, add_astronaut):
    steps = Steps()
    refresher = make_refresher(wait=steps)

    async with running(refresher):
        assert await asyncio.wait_for(steps.parked.get(), 5) is False
        add_astronaut("AST-01", age=None)
        refresher.wake()
        steps.go()
        assert await asyncio.wait_for(steps.parked.get(), 5) is False  # the wake was consumed by the pass
        assert analysis.requested == ["AST-01"]
