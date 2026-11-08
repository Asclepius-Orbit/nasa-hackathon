"""Propiedades de la detección de repeticiones desde eventos (#41)."""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
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


@pytest.mark.property
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
