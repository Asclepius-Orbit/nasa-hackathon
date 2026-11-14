"""Control.recover: after a restart the ack timeouts, which lived only in memory, are armed again.

A first Control sends the commands and is closed (its timers die, as in a crash); a second one over the
same store recovers them. The clock is injected; the re-armed timers run on the real loop.
"""

import asyncio
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from monitor.control import Control
from monitor.db import Store

pytestmark = [pytest.mark.anyio, pytest.mark.unit]

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
TIMEOUT_S = 0.4


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class Publisher:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        self.published.append(payload)


class Node:
    """One run of the backend: a Control over the shared store, recording the sessions it closes."""

    def __init__(self, store: Store, clock: Clock) -> None:
        self.closed: list[str] = []
        self.control = Control(store, Publisher(), self._ended, ack_timeout_s=TIMEOUT_S, clock=clock, on_session_closed=self.closed.append)

    async def _ended(self, session_id: str) -> None:
        return None


@pytest.fixture
def store() -> Iterator[Store]:
    opened = Store(":memory:")
    yield opened
    opened.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def restarted(store: Store, clock: Clock) -> Iterator[Node]:
    node = Node(store, clock)
    yield node
    node.control.close()


def session() -> dict[str, Any]:
    return json.loads((EXAMPLES / "domain" / "session.exercise.json").read_text(encoding="utf-8"))


def ack(command: dict[str, Any], status: str, clock: Clock) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0", "command_id": command["command_id"], "astronaut_id": command["astronaut_id"],
        "device_id": command["device_id"], "timestamp": clock().isoformat(), "status": status,
    }  # fmt: skip


async def crashed_after(store: Store, clock: Clock, *, running: bool = False, stop: bool = False) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Sends a start_session (acked as accepted if `running`) and maybe a stop_session, then dies without acks."""
    node = Node(store, clock)
    start = await node.control.start_session(session())
    if running:
        node.control.on_ack(ack(start, "accepted", clock))
    stop_command = await node.control.stop_session(start["session_id"], "completed") if stop else None
    node.control.close()
    return start, stop_command


def state(store: Store, command: dict[str, Any]) -> str:
    row = store.command(command["command_id"])
    assert row is not None
    return row.state


def session_state(store: Store, session_id: str) -> tuple[str, str | None]:
    row = store.session(session_id)
    assert row is not None
    return row.state, row.error


async def test_start_past_its_timeout_expires_and_rejects_the_session(store, clock, restarted):
    start, _ = await crashed_after(store, clock)
    clock.advance(seconds=5)

    restarted.control.recover()

    assert state(store, start) == "timeout"
    assert session_state(store, start["session_id"]) == ("rejected", "ack_timeout")
    assert restarted.closed == [start["session_id"]]


async def test_start_exactly_at_its_timeout_expires_now(store, clock, restarted):
    start, _ = await crashed_after(store, clock)
    clock.advance(seconds=TIMEOUT_S)

    restarted.control.recover()

    assert state(store, start) == "timeout"


async def test_start_within_its_timeout_waits_only_the_remaining_time(store, clock, restarted):
    start, _ = await crashed_after(store, clock)
    clock.advance(seconds=0.3)

    restarted.control.recover()

    assert state(store, start) == "sent"
    assert session_state(store, start["session_id"]) == ("starting", None)
    await asyncio.sleep(0.25)  # past the remaining 0.1 s, well before a full new timeout
    assert state(store, start) == "timeout"
    assert session_state(store, start["session_id"]) == ("rejected", "ack_timeout")
    assert restarted.closed == [start["session_id"]]


async def test_late_accept_after_the_recovered_timeout_revives_the_session(store, clock, restarted):
    start, _ = await crashed_after(store, clock)
    clock.advance(seconds=0.3)
    restarted.control.recover()
    await asyncio.sleep(0.25)
    assert session_state(store, start["session_id"]) == ("rejected", "ack_timeout")

    restarted.control.on_ack(ack(start, "accepted", clock))

    assert state(store, start) == "accepted"
    assert session_state(store, start["session_id"]) == ("running", None)


async def test_ack_after_recovery_cancels_the_rearmed_timer(store, clock, restarted):
    start, _ = await crashed_after(store, clock)
    restarted.control.recover()

    restarted.control.on_ack(ack(start, "accepted", clock))
    await asyncio.sleep(TIMEOUT_S + 0.1)

    assert state(store, start) == "accepted"
    assert session_state(store, start["session_id"]) == ("running", None)


async def test_command_issued_in_the_future_waits_at_most_one_timeout(store, clock, restarted):
    start, _ = await crashed_after(store, clock)
    clock.advance(seconds=-30)  # the wall clock went back across the restart

    restarted.control.recover()

    assert state(store, start) == "sent"
    await asyncio.sleep(TIMEOUT_S + 0.15)
    assert state(store, start) == "timeout"


async def test_stop_past_its_timeout_expires_and_leaves_the_session_running(store, clock, restarted):
    start, stop = await crashed_after(store, clock, running=True, stop=True)
    assert stop is not None
    clock.advance(seconds=5)

    restarted.control.recover()

    assert state(store, stop) == "timeout"
    assert state(store, start) == "accepted"
    assert session_state(store, start["session_id"]) == ("running", None)
    assert restarted.closed == []


async def test_starting_session_without_a_sent_start_is_rejected(store, clock, restarted):
    store.insert_session(session(), "starting")  # the crash came before the command was stored

    restarted.control.recover()

    session_id = session()["session_id"]
    assert session_state(store, session_id) == ("rejected", "ack_timeout")
    assert restarted.closed == [session_id]


async def test_recovery_logs_what_it_did(store, clock, restarted, caplog):
    await crashed_after(store, clock)
    clock.advance(seconds=5)

    with caplog.at_level(logging.INFO, logger="monitor.control"):
        restarted.control.recover()

    assert any("recovered" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)


async def test_recover_with_nothing_pending_does_nothing(store, clock, restarted):
    restarted.control.recover()
    await asyncio.sleep(0)

    assert store.commands_in_state("sent") == []
    assert restarted.closed == []
