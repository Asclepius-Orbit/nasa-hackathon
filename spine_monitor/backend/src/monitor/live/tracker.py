"""Live repetition state of each session and who is listening to it.

`RepTracker` turns the detector into the `RepStateView` of one session (`app/ui-rep-state`). `RepHub` keeps the
trackers and their subscribers. Everything runs on the event loop with no `await`, so each method is atomic: a chunk
cannot arrive in the middle of a rebuild or between a subscription and the check of the session state.

A subscriber has a bounded queue. When it is full the oldest message goes, so a slow client never blocks ingestion and
always ends up with the latest state. A tracker lives while its session does: the end of the session removes it, and a
client that connects later gets a view kept in a small LRU, or after a restart a rebuild, once, that goes into it.
"""

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from spine_contracts import SCHEMA_VERSION

from monitor.db import SessionRow
from monitor.live.reps import RepDetector, RepDetectorParams
from monitor.views.protocol_catalog import planned_sets_for, reps_per_set_for, total_reps_for

_log = logging.getLogger(__name__)

REP_STREAM_ID = "imu_t9"
TERMINAL_STATES = frozenset({"ended", "processing", "processed", "processing_failed", "rejected"})
# At most one state per chunk (10 per second): about a second behind; older states are worth nothing to a counter.
_QUEUE_SIZE = 8
# A view is a few hundred bytes; this covers the sessions someone reopens in a sitting, and an older one only costs a rebuild.
_FINAL_STATES_KEPT = 32

type RepStateView = dict[str, Any]


def rep_total(session: Mapping[str, Any]) -> int | None:
    planned = session.get("planned_reps")
    return planned if planned is not None else total_reps_for(session["protocol_id"])


def rep_sets(session: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """(planned sets, repetitions per set) of the session: its own plan, otherwise the protocol's, otherwise null."""
    sets = session.get("planned_sets")
    per_set = session.get("reps_per_set")
    return (
        sets if sets is not None else planned_sets_for(session["protocol_id"]),
        per_set if per_set is not None else reps_per_set_for(session["protocol_id"]),
    )


def set_position(rep: int, reps_per_set: int | None) -> tuple[int, int]:
    """(set, repetition in the set), both 1-based and 0 before the first repetition, of a repetition counted across the session.

    The last repetition of a set stays in it: the pause between two sets is the `resting` phase, not a new set.
    Without a known size of set everything is the first set."""
    if rep == 0:
        return 0, 0
    if reps_per_set is None:
        return 1, rep
    return (rep - 1) // reps_per_set + 1, (rep - 1) % reps_per_set + 1


def has_live_reps(session: Mapping[str, Any]) -> bool:
    """False when the protocol has no repetitions or the session has no stream to count them with."""
    return rep_total(session) is not None and any(stream["stream_id"] == REP_STREAM_ID for stream in session["streams"])


class RepTracker:
    def __init__(
        self,
        session_id: str,
        total: int | None,
        *,
        sets_total: int | None = None,
        reps_per_set: int | None = None,
        clock: Callable[[], datetime],
        no_signal_s: float,
    ) -> None:
        self._session_id = session_id
        self._total = total
        self._sets_total = sets_total
        self._reps_per_set = reps_per_set
        self._clock = clock
        self._no_signal_s = no_signal_s
        self._detector = RepDetector(RepDetectorParams.default())
        self._received_at: datetime | None = None  # local clock, in memory only: unknown after a restart

    def replay(self, chunk: Mapping[str, Any]) -> None:
        """A stored chunk: it counts, but says nothing about the signal now."""
        self._detector.feed(chunk)

    def mark_received(self) -> None:
        self._received_at = self._clock()

    def on_chunk(self, chunk: Mapping[str, Any]) -> None:
        # First the signal: a chunk the detector cannot read still arrived.
        self.mark_received()
        self._detector.feed(chunk)

    def view(self) -> RepStateView:
        now = self._clock()
        snapshot = self._detector.snapshot()
        signal = self._received_at is not None and (now - self._received_at).total_seconds() < self._no_signal_s
        set_index, rep_in_set = set_position(snapshot["rep"], self._reps_per_set)
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": self._session_id,
            "rep": snapshot["rep"],
            "total": self._total,
            "set": set_index,
            "sets_total": self._sets_total,
            "reps_per_set": self._reps_per_set,
            "rep_in_set": rep_in_set,
            "phase": snapshot["phase"] if signal else None,
            "progress_pct": snapshot["progress_pct"] if signal else None,
            "last": snapshot["last"],
            "signal": "ok" if signal else "no_signal",
            "as_of": now.isoformat(),
        }

    def final_view(self) -> RepStateView:
        """The state of a session that is over: nothing is going on, and that is not a failure of the sensor."""
        return {**self.view(), "phase": None, "progress_pct": None, "signal": "ok"}


@dataclass(frozen=True, slots=True)
class RepMessage:
    event: Literal["state", "end", "close"]  # close: the backend stops, the session goes on (no `end`: the client reconnects)
    view: RepStateView | None = None


_CLOSE = RepMessage("close")


@dataclass(eq=False, slots=True)
class Subscription:
    session_id: str
    queue: asyncio.Queue[RepMessage]


@dataclass(eq=False, slots=True)
class _Live:
    tracker: RepTracker
    published: RepStateView  # the last state told to the subscribers, without `as_of`
    subscribers: set[Subscription] = field(default_factory=set[Subscription])


class ChunkSource(Protocol):
    def session(self, session_id: str) -> SessionRow | None: ...

    def stream_chunks(self, astronaut_id: str, device_id: str, session_id: str, stream_id: str) -> Iterable[dict[str, Any]]: ...


def _without_as_of(view: RepStateView) -> RepStateView:
    return {key: value for key, value in view.items() if key != "as_of"}


def _offer(queue: asyncio.Queue[RepMessage], message: RepMessage) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(message)


class RepHub:
    def __init__(self, store: ChunkSource, clock: Callable[[], datetime], *, no_signal_s: float, final_states_kept: int = _FINAL_STATES_KEPT) -> None:
        self._store = store
        self._clock = clock
        self._no_signal_s = no_signal_s
        self._live: dict[str, _Live] = {}
        self._closed = False
        self._final_states: OrderedDict[str, RepStateView] = OrderedDict()
        self._final_states_kept = final_states_kept

    # --- ingestion ---------------------------------------------------------------------------------

    def on_chunk(self, session: SessionRow, chunk: Mapping[str, Any]) -> None:
        """`chunk` was just stored for the first time and belongs to the rep stream of `session`."""
        session_id = chunk["session_id"]
        if session.state in TERMINAL_STATES:
            self._final_states.pop(session_id, None)  # a late chunk is in the store: a rebuild would count it
            return
        if self._closed or not has_live_reps(session.payload):
            return
        if (live := self._live.get(session_id)) is None:
            # The rebuild reads this very chunk from the store: feeding it again would be counting it twice.
            live = self._track(session)
            live.tracker.mark_received()
        else:
            live.tracker.on_chunk(chunk)
        self._publish(live)

    def finish(self, session_id: str) -> None:
        """The session reached a terminal state: its subscribers get `end` and the tracker goes."""
        if (live := self._live.pop(session_id, None)) is None:
            return
        view = live.tracker.final_view()
        self._keep_final_state(session_id, view)
        end = RepMessage("end", view)
        for subscription in live.subscribers:
            _offer(subscription.queue, end)
        live.subscribers.clear()

    # --- subscribers -------------------------------------------------------------------------------

    def subscribe(self, session_id: str) -> Subscription:
        """The queue starts with what the client must receive first, whatever the state of the session is right now."""
        subscription = Subscription(session_id, asyncio.Queue(maxsize=_QUEUE_SIZE))
        row = None if self._closed else self._store.session(session_id)
        if row is None:
            subscription.queue.put_nowait(_CLOSE)
        elif row.state in TERMINAL_STATES:
            view = self._final_view(row)
            subscription.queue.put_nowait(RepMessage("state", view))
            subscription.queue.put_nowait(RepMessage("end", view))
        else:
            live = self._live.get(session_id) or self._track(row)
            self._publish(live)  # whoever was already listening hears of a pending change before the new one starts
            live.subscribers.add(subscription)
            subscription.queue.put_nowait(RepMessage("state", live.tracker.view()))
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        if (live := self._live.get(subscription.session_id)) is not None:
            live.subscribers.discard(subscription)

    def refresh(self, session_id: str) -> None:
        """The periodic check: the only thing that changes with no chunk is the signal going away."""
        if (live := self._live.get(session_id)) is not None:
            self._publish(live)

    def close(self) -> None:
        """The backend stops: the streams are closed without `end`, so that no connection keeps it waiting."""
        self._closed = True
        for live in self._live.values():
            for subscription in live.subscribers:
                _offer(subscription.queue, _CLOSE)
        self._live.clear()

    # --- inspection --------------------------------------------------------------------------------

    def tracked_sessions(self) -> set[str]:
        return set(self._live)

    def subscriber_count(self, session_id: str) -> int:
        live = self._live.get(session_id)
        return 0 if live is None else len(live.subscribers)

    def tracker_view(self, session_id: str) -> RepStateView:
        return self._live[session_id].tracker.view()

    # --- internals ---------------------------------------------------------------------------------

    def _rebuilt(self, session: SessionRow) -> RepTracker:
        payload = session.payload
        sets_total, reps_per_set = rep_sets(payload)
        tracker = RepTracker(
            payload["session_id"], rep_total(payload), sets_total=sets_total, reps_per_set=reps_per_set, clock=self._clock, no_signal_s=self._no_signal_s
        )
        for chunk in self._store.stream_chunks(payload["astronaut_id"], payload["device_id"], payload["session_id"], REP_STREAM_ID):
            try:
                tracker.replay(chunk)
            except Exception:
                # Same as live, where the ingestion logs it and goes on: one bad chunk must not leave the session without a counter.
                _log.exception("rebuild of the repetition state of session %s: chunk skipped", payload["session_id"])
        return tracker

    def _final_view(self, session: SessionRow) -> RepStateView:
        session_id = session.payload["session_id"]
        view = self._final_states.get(session_id)
        if view is None:
            view = self._rebuilt(session).final_view()
        self._keep_final_state(session_id, view)
        return {**view, "as_of": self._clock().isoformat()}

    def _keep_final_state(self, session_id: str, view: RepStateView) -> None:
        self._final_states[session_id] = view
        self._final_states.move_to_end(session_id)
        while len(self._final_states) > self._final_states_kept:
            self._final_states.popitem(last=False)

    def _track(self, session: SessionRow) -> _Live:
        tracker = self._rebuilt(session)
        live = _Live(tracker, _without_as_of(tracker.view()))
        self._live[session.payload["session_id"]] = live
        return live

    def _publish(self, live: _Live) -> None:
        view = live.tracker.view()
        content = _without_as_of(view)
        if content == live.published:
            return
        live.published = content
        state = RepMessage("state", view)
        for subscription in live.subscribers:
            _offer(subscription.queue, state)
