"""State of the link with the ground station, from periodic windows. Pure: the clock is an argument.

The link is up during the first `up_min` minutes of every `period_min`, counted from `anchor` in both directions
(it also works for instants before the anchor). Returns the shape of `app/ui-common#ground_link`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict

_MINUTE = timedelta(minutes=1)


class LinkUp(TypedDict):
    state: Literal["up"]


class LinkDown(TypedDict):
    state: Literal["down"]
    back_in_min: int


@dataclass(frozen=True)
class GroundWindows:
    anchor: datetime
    period_min: int
    up_min: int

    def __post_init__(self) -> None:
        if self.anchor.tzinfo is None:
            raise ValueError(f"anchor must have a timezone: {self.anchor!r}")
        if not self.period_min > self.up_min > 0:
            raise ValueError(f"need period_min > up_min > 0, got period_min={self.period_min}, up_min={self.up_min}")
        object.__setattr__(self, "anchor", self.anchor.astimezone(UTC))


def link_state(windows: GroundWindows | None, now: datetime) -> LinkUp | LinkDown | None:
    """None when no windows are configured. When down, `back_in_min` is rounded up, so it is at least 1."""
    if windows is None:
        return None
    period = timedelta(minutes=windows.period_min)
    phase = (now - windows.anchor) % period
    if phase < timedelta(minutes=windows.up_min):
        return {"state": "up"}
    return {"state": "down", "back_in_min": -((phase - period) // _MINUTE)}
