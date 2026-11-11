from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import SCHEMA_VERSION, SelfReport
from spine_contracts.messages import self_report_adapter
from spine_contracts.models.domain.feature_set_schema import Reason
from spine_engine.features.symptoms import WINDOW, symptom_feature_set

pytestmark = pytest.mark.property

AS_OF = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
AST = "AST-01"
RED_FLAGS = [
    "radiating_leg_pain",
    "numbness_or_tingling",
    "leg_weakness",
    "bladder_or_bowel_dysfunction",
    "saddle_numbness",
    "severe_night_pain",
    "fever_with_back_pain",
    "trauma_event",
]
ALL_IDS = {
    "symptoms.pain_nrs_7d_mean",
    "symptoms.stiffness_nrs_7d_mean",
    "symptoms.sleep_hours_7d_mean",
    "symptoms.red_flag_count_7d",
}

inside_offsets = st.integers(min_value=0, max_value=int(WINDOW.total_seconds() * 1_000_000)).map(lambda us: AS_OF - timedelta(microseconds=us))
# fuera de la ventana: estrictamente antes de start o después de end
outside_offsets = st.one_of(
    st.integers(min_value=1, max_value=10**12).map(lambda us: AS_OF - WINDOW - timedelta(microseconds=us)),
    st.integers(min_value=1, max_value=10**12).map(lambda us: AS_OF + timedelta(microseconds=us)),
)


def _base(at: datetime) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "report_id": "11111111-1111-4111-8111-111111111111",
        "astronaut_id": AST,
        "timestamp": at.isoformat(),
        "source": "simulator",
    }


@st.composite
def dailies(draw, at=inside_offsets) -> SelfReport:
    body = _base(draw(at)) | {
        "kind": "daily",
        "pain_nrs": draw(st.integers(0, 10)),
        "stiffness_nrs": draw(st.integers(0, 10)),
    }
    hours = draw(st.one_of(st.none(), st.floats(0, 24, allow_nan=False)))
    if hours is not None:
        body["sleep"] = {"hours": hours}
    flags = draw(st.one_of(st.none(), st.lists(st.sampled_from(RED_FLAGS), unique=True, max_size=8)))
    if flags is not None:
        body["red_flags"] = flags
    return self_report_adapter.validate_python(body)


@st.composite
def red_flag_reports(draw, at=inside_offsets) -> SelfReport:
    body = _base(draw(at)) | {"kind": "red_flag", "red_flags": draw(st.lists(st.sampled_from(RED_FLAGS), unique=True, min_size=1))}
    return self_report_adapter.validate_python(body)


@st.composite
def odis(draw, at=inside_offsets) -> SelfReport:
    body = _base(draw(at)) | {"kind": "weekly_odi", "items": [{"item_id": "lifting", "score": draw(st.integers(0, 5))}]}
    return self_report_adapter.validate_python(body)


reports_in_window = st.lists(st.one_of(dailies(), red_flag_reports(), odis()), max_size=12)
reports_out_of_window = st.lists(st.one_of(dailies(outside_offsets), red_flag_reports(outside_offsets), odis(outside_offsets)), max_size=8)
nonempty_dailies = st.lists(dailies(), min_size=1, max_size=12)


def run(reports):
    return symptom_feature_set(reports, [], AS_OF, AST)


def values(fs):
    return {f.feature_id: f.value for f in fs.features}


@given(inside=reports_in_window, outside=reports_out_of_window)
def test_only_reports_inside_the_window_count(inside, outside):
    assert run(inside + outside) == run(inside)


@given(outside=reports_out_of_window)
def test_without_reports_in_window_everything_is_skipped_never_zero(outside):
    fs = run(outside)
    assert fs.features == []
    assert {(s.feature_id, s.reason) for s in fs.skipped or []} == {(i, Reason.NO_DATA) for i in ALL_IDS}


@given(odi_only=st.lists(odis(), max_size=5))
def test_weekly_odi_alone_gives_no_values(odi_only):
    assert run(odi_only).features == []


@given(reports=nonempty_dailies)
def test_mean_is_within_min_and_max_and_retired_max_is_never_produced(reports):
    result = run(reports)
    vals = values(result)
    pains = [r.pain_nrs for r in reports]
    assert "symptoms.pain_nrs_7d_max" not in vals
    assert "symptoms.pain_nrs_7d_max" not in {s.feature_id for s in result.skipped or []}
    assert min(pains) <= vals["symptoms.pain_nrs_7d_mean"] <= max(pains)
    stiff = [r.stiffness_nrs for r in reports]
    assert min(stiff) <= vals["symptoms.stiffness_nrs_7d_mean"] <= max(stiff)


@given(reports=nonempty_dailies)
def test_sleep_mean_is_within_the_reported_hours(reports):
    hours = [r.sleep.hours for r in reports if r.sleep is not None]
    vals = values(run(reports))
    if hours:
        assert min(hours) - 1e-9 <= vals["symptoms.sleep_hours_7d_mean"] <= max(hours) + 1e-9
    else:
        assert "symptoms.sleep_hours_7d_mean" not in vals


@given(reports=reports_in_window)
def test_red_flag_count_is_total_flags_and_present_iff_answered(reports):
    expected = sum(len(r.red_flags or []) for r in reports if r.kind in ("daily", "red_flag"))
    present = any(r.kind == "red_flag" or (r.kind == "daily" and r.red_flags is not None) for r in reports)
    vals = values(run(reports))
    if present:
        assert vals["symptoms.red_flag_count_7d"] == expected
    else:
        assert "symptoms.red_flag_count_7d" not in vals


@given(reports=reports_in_window, data=st.data())
def test_result_is_independent_of_report_order(reports, data):
    shuffled = data.draw(st.permutations(reports))
    assert run(shuffled) == run(reports)
