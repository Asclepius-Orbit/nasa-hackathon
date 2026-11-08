"""Ventanas de repetición (#41): detección desde eventos del protocolo y servicio con respaldo IMU inyectable."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from spine_contracts import FeaturesRequest
from spine_contracts.models.domain.session_schema import Event, EventType
from spine_engine.features.context import ExtractionContext, build_context
from spine_engine.features.repetitions import RepetitionService, RepWindow, RepWindows, detect_from_events

_EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "features-request.json"
T0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def ev(kind: str, seconds: float, rep: int | None = None) -> Event:
    return Event(event_type=EventType(kind), timestamp=at(seconds), device_ms=None, boot_id=None, rep_index=rep, note=None)


def make_context(events: list[Event]) -> ExtractionContext:
    request = FeaturesRequest.model_validate(json.loads(_EXAMPLE.read_text(encoding="utf-8")))
    session = request.session.model_copy(update={"events": events})
    return build_context(request.model_copy(update={"session": session}))


def test_a_start_end_pair_becomes_one_active_window():
    (window,) = detect_from_events([ev("rep_start", 1), ev("rep_end", 4)])

    assert window.index == 1
    assert window.active == (at(1), at(4))
    assert window.duration_s == 3.0
    assert window.rest is None
    assert window.rest_start is None
    assert window.rest_end is None


def test_rest_goes_from_each_rep_end_to_the_next_rep_start():
    first, second = detect_from_events([ev("rep_start", 1), ev("rep_end", 4), ev("rep_start", 6), ev("rep_end", 9)])

    assert (first.index, second.index) == (1, 2)
    assert first.rest == (at(4), at(6))
    assert second.rest is None


def test_the_last_rep_rests_until_a_later_rest_end_event():
    *_, last = detect_from_events([ev("rep_start", 1), ev("rep_end", 4), ev("rest_end", 10)])

    assert last.rest == (at(4), at(10))


def test_a_rest_end_before_the_last_rep_end_is_ignored():
    (window,) = detect_from_events([ev("rest_end", 0.5), ev("rep_start", 1), ev("rep_end", 4)])

    assert window.rest is None


def test_events_are_sorted_by_timestamp_not_by_arrival_or_rep_index():
    windows = detect_from_events([ev("rep_end", 9, 1), ev("rep_start", 6, 1), ev("rep_end", 4, 2), ev("rep_start", 1, 2)])

    assert [w.active for w in windows] == [(at(1), at(4)), (at(6), at(9))]
    assert [w.index for w in windows] == [1, 2]


def test_rep_index_only_breaks_timestamp_ties():
    windows = detect_from_events([ev("rep_end", 5, 1), ev("rep_start", 5, 2), ev("rep_start", 1, 1)])

    assert [w.active for w in windows] == [(at(1), at(5))]


def test_orphan_ends_and_unclosed_starts_are_dropped():
    windows = detect_from_events([ev("rep_end", 0), ev("rep_start", 1), ev("rep_end", 3), ev("rep_start", 5)])

    assert [w.active for w in windows] == [(at(1), at(3))]


def test_consecutive_duplicate_starts_and_ends_collapse_into_one_window():
    windows = detect_from_events([ev("rep_start", 1), ev("rep_start", 2), ev("rep_end", 4), ev("rep_end", 5)])

    assert [w.active for w in windows] == [(at(1), at(4))]


@pytest.mark.parametrize("end_at", [1.0, 0.5])
def test_a_window_that_ends_at_or_before_its_start_is_dropped(end_at: float):
    # el fin llega "después" de un rep_start posterior en el orden por tiempo: nunca fin <= inicio
    assert detect_from_events([ev("rep_start", 1), ev("rep_end", end_at)]) == ()


def test_unrelated_events_are_ignored_and_empty_input_gives_nothing():
    assert detect_from_events([]) == ()
    assert detect_from_events([ev("note", 1), ev("pain_reported", 2), ev("mvc_start", 3)]) == ()


def test_repwindows_reports_its_count_and_source():
    windows = RepWindows(detect_from_events([ev("rep_start", 1), ev("rep_end", 2)]), "events")

    assert len(windows) == windows.count == 1
    assert windows.source == "events"


def test_the_service_prefers_events_over_the_imu_detector():
    calls: list[ExtractionContext] = []

    def imu(context: ExtractionContext) -> tuple[RepWindow, ...]:
        calls.append(context)
        return (RepWindow(1, at(0), at(1), None, None),)

    result = RepetitionService(imu).windows(make_context([ev("rep_start", 1), ev("rep_end", 3)]))

    assert result.source == "events"
    assert result.windows[0].active == (at(1), at(3))
    assert calls == []


def test_the_service_falls_back_to_the_imu_detector_without_event_windows():
    imu_window = RepWindow(1, at(0), at(1), None, None)

    result = RepetitionService(lambda _: (imu_window,)).windows(make_context([]))

    assert result.source == "imu"
    assert result.windows == (imu_window,)


def test_the_service_reports_none_when_nothing_detects_reps():
    context = make_context([])

    assert RepetitionService().windows(context) == RepWindows((), "none")
    assert RepetitionService(lambda _: ()).windows(context) == RepWindows((), "none")


def test_the_service_memoizes_per_context_identity():
    calls: list[ExtractionContext] = []

    def imu(context: ExtractionContext) -> tuple[RepWindow, ...]:
        calls.append(context)
        return (RepWindow(1, at(0), at(1), None, None),)

    service = RepetitionService(imu)
    first_context, second_context = make_context([]), make_context([])

    first = service.windows(first_context)
    assert service.windows(first_context) is first
    assert len(calls) == 1

    service.windows(second_context)
    assert len(calls) == 2
    assert service.windows(first_context) == first
