"""link_state: the periodic ground link window, pure of the clock (`now` is an argument)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from monitor.ground.link import GroundWindows, link_state

ANCHOR = datetime(2027, 1, 1, 12, 0, tzinfo=UTC)
WINDOWS = GroundWindows(anchor=ANCHOR, period_min=90, up_min=20)


def at(minutes: float) -> datetime:
    return ANCHOR + timedelta(minutes=minutes)


def test_without_windows_the_state_is_unknown():
    assert link_state(None, ANCHOR) is None


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (0, {"state": "up"}),
        (19.99, {"state": "up"}),
        (20, {"state": "down", "back_in_min": 70}),
        (20.5, {"state": "down", "back_in_min": 70}),  # 69.5 rounds up
        (89, {"state": "down", "back_in_min": 1}),
        (89.01, {"state": "down", "back_in_min": 1}),
        (90, {"state": "up"}),
        (-1, {"state": "down", "back_in_min": 1}),  # before the anchor it is periodic too
        (-70, {"state": "down", "back_in_min": 70}),
        (-70.0001, {"state": "up"}),
    ],
)
def test_state_at_an_offset_from_the_anchor(minutes, expected):
    assert link_state(WINDOWS, at(minutes)) == expected


def test_an_offset_timezone_is_the_same_instant():
    plus_two = timezone(timedelta(hours=2))

    assert link_state(WINDOWS, at(30).astimezone(plus_two)) == {"state": "down", "back_in_min": 60}


@pytest.mark.parametrize(("period", "up"), [(90, 90), (90, 100), (90, 0), (90, -5), (0, 0)])
def test_windows_need_period_greater_than_up_greater_than_zero(period, up):
    with pytest.raises(ValueError, match="period_min"):
        GroundWindows(anchor=ANCHOR, period_min=period, up_min=up)


def test_an_anchor_without_timezone_is_refused():
    with pytest.raises(ValueError, match="anchor"):
        GroundWindows(anchor=datetime(2027, 1, 1), period_min=90, up_min=20)


seconds_from_anchor = st.integers(min_value=-10**6, max_value=10**6)


def at_s(seconds: int) -> datetime:
    return ANCHOR + timedelta(seconds=seconds)


@settings(max_examples=60)
@given(offset=seconds_from_anchor, period=st.integers(2, 600), data=st.data())
def test_up_iff_the_offset_modulo_the_period_is_inside_the_up_window(offset, period, data):
    up = data.draw(st.integers(1, period - 1))

    state = link_state(GroundWindows(ANCHOR, period, up), at_s(offset))

    assert state is not None
    assert (state["state"] == "up") == (offset % (period * 60) < up * 60)


@settings(max_examples=60)
@given(offset=seconds_from_anchor, period=st.integers(2, 600), data=st.data())
def test_advancing_back_in_min_from_a_down_state_leaves_it_up_and_one_minute_less_does_not(offset, period, data):
    up = data.draw(st.integers(1, period - 1))
    windows = GroundWindows(ANCHOR, period, up)
    state = link_state(windows, at_s(offset))
    assert state is not None
    assume(state["state"] == "down")

    back = state["back_in_min"]
    assert back >= 1
    assert link_state(windows, at_s(offset + back * 60)) == {"state": "up"}
    if back > 1:
        assert link_state(windows, at_s(offset + (back - 1) * 60))["state"] == "down"  # type: ignore[index]
