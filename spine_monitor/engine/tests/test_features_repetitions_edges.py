"""Bordes de ``repetitions`` y ``exercise`` (#41): desempates del orden de eventos y reposo estrictamente posterior."""

import json
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

import spine_contracts
from spine_contracts import FeaturesRequest
from spine_contracts.models.domain.session_schema import Event, EventType
from spine_engine.features.context import ExtractionContext, build_context
from spine_engine.features.exercise import ExerciseFamily
from spine_engine.features.repetitions import RepetitionService, RepWindow, RepWindows, _sort_key, detect_from_events

# vía el paquete instalado, no vía parents[] desde los tests: bajo mutmut los tests se copian a otro árbol
_EXAMPLE = Path(spine_contracts.__file__).resolve().parents[2] / "examples" / "valid" / "engine" / "features-request.json"
T0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def ev(kind: str, seconds: float, rep: int | None = None) -> Event:
    return Event(event_type=EventType(kind), timestamp=at(seconds), device_ms=None, boot_id=None, rep_index=rep, note=None)


def test_sort_key_is_time_then_rep_index_then_start_before_end():
    assert _sort_key(ev("rep_start", 3, 2)) == (at(3), 2, False)
    assert _sort_key(ev("rep_end", 3, 2)) == (at(3), 2, True)
    # sin rep_index cuenta como 0, no como 1 ni como otro valor
    assert _sort_key(ev("rep_start", 3)) == (at(3), 0, False)
    assert _sort_key(ev("rep_end", 3)) == (at(3), 0, True)


def test_a_missing_rep_index_sorts_before_any_real_one_at_the_same_instant():
    keys = sorted([_sort_key(ev("rep_start", 3, 1)), _sort_key(ev("rep_start", 3))])
    assert [k[1] for k in keys] == [0, 1]


def test_on_a_full_tie_the_start_goes_before_the_end_whatever_the_arrival_order():
    # inicio y fin en el instante 5 con el mismo rep_index: el inicio va primero y, con la rep abierta desde 1, es un
    # duplicado; el fin cierra (1, 5) y el fin de 8 queda huérfano. Con el fin primero saldrían dos ventanas.
    for events in (
        [ev("rep_start", 1, 1), ev("rep_end", 5, 1), ev("rep_start", 5, 1), ev("rep_end", 8, 1)],
        [ev("rep_end", 8, 1), ev("rep_start", 5, 1), ev("rep_end", 5, 1), ev("rep_start", 1, 1)],
    ):
        assert [w.active for w in detect_from_events(events)] == [(at(1), at(5))]


def test_rep_index_breaks_ties_before_the_start_end_rule():
    # mismo instante: el fin de la rep 1 (índice menor) va antes que el inicio de la rep 2
    windows = detect_from_events([ev("rep_start", 1, 1), ev("rep_start", 5, 2), ev("rep_end", 5, 1), ev("rep_end", 9, 2)])
    assert [w.active for w in windows] == [(at(1), at(5)), (at(5), at(9))]


def test_a_rest_end_at_the_very_instant_of_the_last_rep_end_is_not_a_rest():
    (window,) = detect_from_events([ev("rep_start", 1), ev("rep_end", 4), ev("rest_end", 4)])
    assert window.rest is None
    assert window.rest_start is None
    (later,) = detect_from_events([ev("rep_start", 1), ev("rep_end", 4), ev("rest_end", 4), ev("rest_end", 6)])
    assert later.rest == (at(4), at(6))


def test_the_rest_end_of_the_last_rep_is_the_first_one_after_it():
    (window,) = detect_from_events([ev("rep_start", 1), ev("rep_end", 4), ev("rest_end", 9), ev("rest_end", 5)])
    assert window.rest == (at(4), at(5))


class _FixedService(RepetitionService):
    """Frontera: ventanas fijas, sin mirar el contexto."""

    def __init__(self, result: RepWindows) -> None:
        super().__init__()
        self._result = result

    def windows(self, context: ExtractionContext) -> RepWindows:
        return self._result


def test_the_duration_cv_is_computed_for_repetitions_shorter_than_one_second():
    durations = [0.4, 0.6]
    windows = tuple(RepWindow(i, at(i * 10), at(i * 10 + d), None, None) for i, d in enumerate(durations, start=1))
    context = build_context(FeaturesRequest.model_validate(json.loads(_EXAMPLE.read_text(encoding="utf-8"))))

    result = ExerciseFamily(_FixedService(RepWindows(windows, "imu"))).extract(context)

    values = {f.feature_id: f.value for f in result.computed}
    assert values["exercise.rep_duration_s_mean"] == 0.5
    assert values["exercise.rep_duration_cv_pct"] == 100.0 * statistics.stdev(durations) / 0.5
    assert values["exercise.rep_duration_cv_pct"] > 0.0
