"""FakeGroundTransport: idempotent by key and content, validates what it accepts, fails on demand."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from monitor.db import GroundKey
from monitor.ground.transport import FakeGroundTransport, GroundSendError
from monitor.views.ground_summary import build_ground_summary

pytestmark = pytest.mark.anyio

NOW = datetime(2027, 2, 9, 9, 0, tzinfo=UTC)
DAY = date(2027, 2, 9)
KEY = GroundKey("AST-01", DAY, 1)


class Ticking:
    """A clock that moves one minute per reading, so a second receipt can never equal the first by accident."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


def summary(mission_day: int | None = None) -> dict[str, Any]:
    return build_ground_summary("AST-01", DAY, mission_day, 1, NOW, None, [], [])


@pytest.fixture
def transport() -> FakeGroundTransport:
    return FakeGroundTransport(Ticking())


async def test_a_new_key_is_stored_and_receipted_with_the_clock(transport):
    receipt = await transport.send(KEY, summary())

    assert receipt.received_at == NOW + timedelta(minutes=1)
    assert transport.received == {KEY: summary()}


async def test_same_key_and_content_is_a_repetition_with_the_original_receipt(transport):
    first = await transport.send(KEY, summary())

    again = await transport.send(KEY, summary())

    assert again == first
    assert [d.received_at for d in transport.deliveries] == [first.received_at]


async def test_same_key_and_other_content_replaces_with_a_new_receipt(transport):
    first = await transport.send(KEY, summary())

    second = await transport.send(KEY, summary(mission_day=5))

    assert second.received_at > first.received_at
    assert transport.received == {KEY: summary(mission_day=5)}
    assert [d.payload["mission_day"] for d in transport.deliveries] == [None, 5]


async def test_an_invalid_payload_is_refused_with_a_code_and_not_stored(transport):
    with pytest.raises(GroundSendError) as refused:
        await transport.send(KEY, summary() | {"notes": "must not be accepted"})

    assert refused.value.code == "invalid_payload"
    assert "must not be accepted" not in str(refused.value)
    assert transport.received == {}


async def test_injected_failures_are_raised_in_order_and_store_nothing(transport):
    transport.fail_next("no_carrier", times=2)
    transport.fail_next("busy")

    codes = []
    for _ in range(3):
        with pytest.raises(GroundSendError) as failed:
            await transport.send(KEY, summary())
        codes.append(failed.value.code)

    assert codes == ["no_carrier", "no_carrier", "busy"]
    assert transport.received == {}
    await transport.send(KEY, summary())
    assert transport.received == {KEY: summary()}


async def test_what_was_received_does_not_change_when_the_sender_changes_its_payload(transport):
    payload = summary()
    await transport.send(KEY, payload)

    payload["sessions"].append("changed by the sender")

    assert transport.received == {KEY: summary()}
