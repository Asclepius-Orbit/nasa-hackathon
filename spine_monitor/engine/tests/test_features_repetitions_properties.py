"""Propiedades de la detección de repeticiones desde eventos (#41)."""

import random
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from spine_contracts.models.domain.session_schema import Event, EventType
from spine_engine.features.repetitions import detect_from_events

T0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)

events = st.lists(
    st.builds(
        Event,
        event_type=st.sampled_from(list(EventType)),
        timestamp=st.integers(0, 50).map(lambda s: T0 + timedelta(seconds=s)),
        rep_index=st.none() | st.integers(1, 5),
    ),
    max_size=30,
)


def _at(event_type: EventType, second: int) -> Event:
    return Event(event_type=event_type, timestamp=T0 + timedelta(seconds=second), device_ms=None, set_index=None, rep_index=None)


# Un set_end entre dos repeticiones separa series: el hueco no es un reposo entre repeticiones (#12). Hypothesis lo
# generaba solo de vez en cuando y el test fallaba de forma intermitente; el ejemplo lo fija.
_BETWEEN_SETS = [
    _at(EventType.REP_START, 0),
    _at(EventType.REP_END, 1),
    _at(EventType.SET_END, 1),
    _at(EventType.REP_START, 2),
    _at(EventType.REP_END, 3),
]
_REP_EVENTS = frozenset({EventType.REP_START, EventType.REP_END})


@pytest.mark.property
@example(_BETWEEN_SETS, random.Random(0))
@given(events, st.randoms(use_true_random=False))
def test_windows_are_ordered_disjoint_positive_and_consecutive(items: list[Event], rng):
    shuffled = list(items)
    rng.shuffle(shuffled)

    windows = detect_from_events(shuffled)

    assert [w.index for w in windows] == list(range(1, len(windows) + 1))
    for window in windows:
        assert window.duration_s > 0
    for before, after in zip(windows, windows[1:], strict=False):
        assert before.end <= after.start
        if before.rest_start is None:  # entre series: sin reposo entre repeticiones
            assert before.rest_end is None
            assert any(e.event_type not in _REP_EVENTS for e in items)
        else:
            assert before.rest_start == before.end
            assert before.rest_end == after.start
    if windows:
        assert windows[-1].rest_start is None or windows[-1].rest_start == windows[-1].end


@pytest.mark.property
@given(events, st.randoms(use_true_random=False))
def test_detection_does_not_depend_on_arrival_order(items: list[Event], rng):
    shuffled = list(items)
    rng.shuffle(shuffled)

    assert detect_from_events(shuffled) == detect_from_events(items)
