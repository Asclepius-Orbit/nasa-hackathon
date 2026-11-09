"""Familia ``exercise`` (#41): nº de repeticiones, duración media y variabilidad (CV) de la duración."""

import json
import math
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import FeaturesRequest, StreamKind, dump, validation
from spine_contracts.catalog import unit_for
from spine_contracts.models.domain.feature_set_schema import Reason
from spine_contracts.models.domain.session_schema import Event, EventType
from spine_engine.features.composite import CompositeFeatureExtractor
from spine_engine.features.context import ExtractionContext, build_context
from spine_engine.features.exercise import ExerciseFamily
from spine_engine.features.registry import default_registry
from spine_engine.features.repetitions import RepetitionService, RepWindow, RepWindows

_EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "features-request.json"
T0 = datetime(2026, 11, 13, 10, 3, 10, tzinfo=UTC)
COUNT = "exercise.rep_count"
MEAN = "exercise.rep_duration_s_mean"
CV = "exercise.rep_duration_cv_pct"
ALL_IDS = (COUNT, MEAN, CV)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def ev(kind: str, seconds: float, rep: int | None = None) -> Event:
    return Event(event_type=EventType(kind), timestamp=at(seconds), device_ms=None, boot_id=None, rep_index=rep, note=None)


def events_for(durations: list[float]) -> list[Event]:
    """Eventos rep_start/rep_end consecutivos con 1 s de reposo entre repeticiones."""
    events: list[Event] = []
    cursor = 0.0
    for i, duration in enumerate(durations, start=1):
        events += [ev("rep_start", cursor, i), ev("rep_end", cursor + duration, i)]
        cursor += duration + 1.0
    return events


def make_request(events: list[Event]) -> FeaturesRequest:
    request = FeaturesRequest.model_validate(json.loads(_EXAMPLE.read_text(encoding="utf-8")))
    session = request.session.model_copy(update={"events": events})
    return request.model_copy(update={"session": session})


def make_context(events: list[Event]) -> ExtractionContext:
    return build_context(make_request(events))


def window(index: int, duration: float) -> RepWindow:
    return RepWindow(index, at(index * 10), at(index * 10 + duration), None, None)


class FakeService(RepetitionService):
    """Frontera: devuelve unas ventanas fijas, sin mirar el contexto."""

    def __init__(self, result: RepWindows) -> None:
        super().__init__()
        self._result = result

    def windows(self, context: ExtractionContext) -> RepWindows:
        return self._result


def family_for(durations: list[float], source: Literal["events", "imu", "none"] = "imu") -> ExerciseFamily:
    windows = tuple(window(i, d) for i, d in enumerate(durations, start=1))
    return ExerciseFamily(FakeService(RepWindows(windows, source)))


def computed(family: ExerciseFamily, context: ExtractionContext) -> dict[str, float]:
    return {f.feature_id: f.value for f in family.extract(context).computed}


def test_the_family_declares_its_three_catalog_ids():
    family = ExerciseFamily(RepetitionService())

    assert family.family == "exercise"
    assert tuple(family.feature_ids) == ALL_IDS


def test_known_durations_give_count_mean_and_sample_cv():
    # 3, 4, 5 s -> media 4, desviación muestral 1 -> CV 25 %
    result = computed(family_for([3.0, 4.0, 5.0]), make_context([]))

    assert result == {COUNT: 3.0, MEAN: 4.0, CV: 25.0}


def test_n_is_the_number_of_repetitions():
    result = family_for([3.0, 4.0, 5.0, 4.0]).extract(make_context([]))

    assert {f.n for f in result.computed} == {4}
    assert result.skipped == ()


@pytest.mark.parametrize("durations", [[], [4.0]])
def test_fewer_than_two_repetitions_skip_all_three_features(durations: list[float]):
    result = family_for(durations, "imu" if durations else "none").extract(make_context([]))

    assert result.computed == ()
    assert [(s.feature_id, s.reason) for s in result.skipped] == [(i, Reason.TOO_FEW_REPETITIONS) for i in ALL_IDS]


def test_two_repetitions_are_enough():
    result = computed(family_for([2.0, 4.0]), make_context([]))

    assert result[COUNT] == 2.0
    assert result[MEAN] == 3.0
    assert result[CV] == pytest.approx(100 * statistics.stdev([2.0, 4.0]) / 3.0)


def test_identical_durations_have_zero_cv():
    assert computed(family_for([4.0, 4.0, 4.0]), make_context([]))[CV] == 0.0


def test_a_zero_mean_is_protected_and_does_not_raise():
    family = ExerciseFamily(FakeService(RepWindows((window(1, 0.0), window(2, 0.0)), "imu")))

    result = family.extract(make_context([]))

    assert {f.feature_id: f.value for f in result.computed}[CV] == 0.0


def test_the_order_of_the_output_is_deterministic():
    result = family_for([3.0, 4.0, 5.0]).extract(make_context([]))

    assert [f.feature_id for f in result.computed] == list(ALL_IDS)


def test_event_windows_use_every_declared_stream_for_quality():
    context = make_context(events_for([3.0, 4.0]))
    result = ExerciseFamily(RepetitionService()).extract(context)

    declared = tuple(s.stream_id for s in context.session.streams)
    assert {f.streams for f in result.computed} == {declared}


def test_imu_windows_use_only_the_declared_imu_streams():
    context = make_context([])
    result = family_for([3.0, 4.0], "imu").extract(context)

    imu = tuple(s.stream_id for s in context.session.streams if s.stream_kind is StreamKind.IMU)
    assert imu
    assert {f.streams for f in result.computed} == {imu}


def test_the_real_service_reads_event_windows():
    result = computed(ExerciseFamily(RepetitionService()), make_context(events_for([3.0, 4.0, 5.0])))

    assert result == {COUNT: 3.0, MEAN: 4.0, CV: 25.0}


def test_the_real_service_uses_the_injected_imu_detector_without_events():
    detector_windows = (window(1, 3.0), window(2, 5.0))
    family = ExerciseFamily(RepetitionService(lambda _: detector_windows))

    result = computed(family, make_context([]))

    assert result == {COUNT: 2.0, MEAN: 4.0, CV: pytest.approx(100 * statistics.stdev([3.0, 5.0]) / 4.0)}


def test_the_default_registry_registers_the_exercise_family():
    families = list(default_registry())

    assert families[0].family == "exercise"
    assert tuple(families[0].feature_ids) == ALL_IDS


def extract_json(request: FeaturesRequest) -> dict:
    result = dump(CompositeFeatureExtractor(default_registry()).extract(request))
    assert validation.errors("domain/feature-set", result) == []
    return result


def test_composite_with_enough_reps_returns_values_with_catalog_units():
    result = extract_json(make_request(events_for([3.0, 4.0, 5.0])))

    by_id = {f["feature_id"]: f for f in result["features"]}
    assert [f["feature_id"] for f in result["features"] if f["feature_id"] in ALL_IDS] == sorted(ALL_IDS)
    assert (by_id[COUNT]["value"], by_id[MEAN]["value"], by_id[CV]["value"]) == (3.0, 4.0, 25.0)
    assert all(by_id[i]["unit"] == unit_for(i) for i in ALL_IDS)
    assert all(by_id[i]["n"] == 3 and by_id[i]["quality"]["assessed_by"] == "engine" for i in ALL_IDS)
    assert [s for s in result.get("skipped", []) if s["feature_id"].startswith("exercise.")] == []


def test_composite_with_the_example_single_rep_skips_everything_as_too_few_repetitions():
    request = FeaturesRequest.model_validate(json.loads(_EXAMPLE.read_text(encoding="utf-8")))

    result = extract_json(request)

    assert not [f for f in result["features"] if f["feature_id"].startswith("exercise.")]
    assert sorted((s["feature_id"], s["reason"]) for s in result["skipped"] if s["feature_id"] in ALL_IDS) == sorted(
        (i, "too_few_repetitions") for i in ALL_IDS
    )


def test_composite_without_any_events_does_not_fail():
    result = extract_json(make_request([]))

    assert not [f for f in result["features"] if f["feature_id"].startswith("exercise.")]
    assert {s["reason"] for s in result["skipped"] if s["feature_id"] in ALL_IDS} == {"too_few_repetitions"}


@pytest.mark.property
@given(durations=st.lists(st.floats(min_value=0.05, max_value=60.0, allow_nan=False), min_size=2, max_size=30))
def test_cv_is_non_negative_and_the_mean_lies_between_min_and_max(durations: list[float]):
    result = computed(family_for(durations), make_context([]))
    real = [window(i, d).duration_s for i, d in enumerate(durations, start=1)]  # datetime redondea a microsegundos

    assert result[COUNT] == len(durations)
    assert min(real) - 1e-9 <= result[MEAN] <= max(real) + 1e-9
    assert result[CV] >= 0.0 and math.isfinite(result[CV])
