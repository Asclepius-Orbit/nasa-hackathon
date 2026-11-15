"""Repetition windows (#41): from the protocol events or, without them, from an injectable IMU detector.

The session's ``rep_start``/``rep_end`` events take precedence; the IMU detector (another module) is only the fallback.
Nothing here raises on incoherent data: what does not fit is discarded.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from spine_contracts.models.domain.session_schema import Event, EventType

from spine_engine.features.context import ExtractionContext

type ImuDetector = Callable[[ExtractionContext], tuple["RepWindow", ...]]


@dataclass(frozen=True, slots=True)
class RepWindow:
    """A repetition: active phase (``start``→``end``) and subsequent rest if known."""

    index: int
    start: datetime
    end: datetime
    rest_start: datetime | None
    rest_end: datetime | None

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def active(self) -> tuple[datetime, datetime]:
        return (self.start, self.end)

    @property
    def rest(self) -> tuple[datetime, datetime] | None:
        if self.rest_start is None or self.rest_end is None:
            return None
        return (self.rest_start, self.rest_end)


@dataclass(frozen=True, slots=True)
class RepWindows:
    """Windows in temporal order and where they come from."""

    windows: tuple[RepWindow, ...]
    source: Literal["events", "imu", "none"]

    def __len__(self) -> int:
        return len(self.windows)

    @property
    def count(self) -> int:
        return len(self.windows)


_SET_BOUNDARY = frozenset({EventType.SET_START, EventType.SET_END, EventType.REST_START})


def _sort_key(event: Event) -> tuple[datetime, int, bool]:
    # time takes precedence; rep_index only breaks ties and, in a total tie, the start goes before the end
    return (event.timestamp, event.rep_index or 0, event.event_type is EventType.REP_END)


def detect_from_events(events: Iterable[Event]) -> tuple[RepWindow, ...]:
    """Pair each ``rep_start`` with the next ``rep_end``; discard orphans, duplicates and end <= start."""
    ordered = sorted(events, key=_sort_key)
    pairs: list[tuple[datetime, datetime]] = []
    open_start: datetime | None = None
    for event in ordered:
        if event.event_type is EventType.REP_START:
            if open_start is None:  # a second consecutive start is a duplicate
                open_start = event.timestamp
        elif event.event_type is EventType.REP_END and open_start is not None:
            if event.timestamp > open_start:
                pairs.append((open_start, event.timestamp))
            open_start = None
    last_rest_end = _rest_end_after(ordered, pairs[-1][1]) if pairs else None
    boundaries = [e.timestamp for e in ordered if e.event_type in _SET_BOUNDARY]
    windows: list[RepWindow] = []
    for i, (start, end) in enumerate(pairs, start=1):
        if i < len(pairs):
            following = pairs[i][0]
            # the gap between sets (set_end/set_start/rest_start in between) is not a rest between repetitions
            between_sets = any(end <= t <= following for t in boundaries)
            rest = (None, None) if between_sets else (end, following)
        else:
            rest = (end, last_rest_end) if last_rest_end is not None else (None, None)
        windows.append(RepWindow(index=i, start=start, end=end, rest_start=rest[0], rest_end=rest[1]))
    return tuple(windows)


def _rest_end_after(ordered: list[Event], moment: datetime) -> datetime | None:
    return next((e.timestamp for e in ordered if e.event_type is EventType.REST_END and e.timestamp > moment), None)


class RepetitionService:
    """Resolve the windows of a request: events, then IMU detector, then nothing; memoized per context."""

    def __init__(self, imu_detector: ImuDetector | None = None) -> None:
        self._imu_detector = imu_detector
        # strong reference to the context: prevents a freed id() from being reused by another context
        self._last: tuple[ExtractionContext, RepWindows] | None = None

    def windows(self, context: ExtractionContext) -> RepWindows:
        if self._last is not None and self._last[0] is context:
            return self._last[1]
        result = self._resolve(context)
        self._last = (context, result)
        return result

    def _resolve(self, context: ExtractionContext) -> RepWindows:
        from_events = detect_from_events(context.session.events or ())
        if from_events:
            return RepWindows(from_events, "events")
        if self._imu_detector is not None:
            from_imu = self._imu_detector(context)
            if from_imu:
                return RepWindows(from_imu, "imu")
        return RepWindows((), "none")
