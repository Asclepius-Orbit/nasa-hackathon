"""Tests de los puertos, los errores y los mocks basados en los ejemplos de los contratos."""

import inspect
import json
from datetime import UTC, datetime, timedelta

import pytest
from spine_contracts import AppErrorResponse, History, SelfReportList, SessionList, SimulateRequest, dump
from spine_contracts.validation import validate
from spine_mcp_server.errors import UpstreamError
from spine_mcp_server.mocks import EXAMPLES_DIR as EXAMPLES
from spine_mcp_server.mocks import ExampleAppReader, ExampleAssessBaseSource, ExampleSimulator
from spine_mcp_server.ports import AppReader, AssessBaseSource, Simulator

AST = "AST-01"
ASSESSMENT_ID = "a55e55e0-0000-4000-8000-0000000000a1"
WIDE_FROM = datetime(2027, 1, 1, tzinfo=UTC)
WIDE_TO = datetime(2027, 3, 1, tzinfo=UTC)


def _history_with(n: int) -> History:
    """History del ejemplo ampliado a `n` feature sets y `n` assessments, un minuto de separación, en orden inverso."""
    base = ExampleAppReader().get_history(AST, WIDE_FROM, WIDE_TO)
    t0 = datetime(2027, 2, 9, 8, 0, tzinfo=UTC)
    feature_sets = [base.feature_sets[0].model_copy(update={"computed_at": t0 + timedelta(minutes=i)}) for i in range(n)]
    assessments = [base.assessments[0].model_copy(update={"as_of": t0 + timedelta(minutes=i)}) for i in range(n)]
    return base.model_copy(update={"feature_sets": feature_sets[::-1], "assessments": assessments[::-1]})


SESSION_FROM = datetime(2026, 11, 1, tzinfo=UTC)
SESSION_TO = datetime(2026, 12, 1, tzinfo=UTC)
SESSION_AT = datetime(2026, 11, 13, 10, 2, 31, tzinfo=UTC)


def _sessions_with(n: int) -> SessionList:
    """SessionList del ejemplo ampliado a `n` sesiones, un minuto de separación, en orden inverso."""
    base = ExampleAppReader().get_sessions(AST, SESSION_FROM, SESSION_TO)
    items = [base.items[0].model_copy(update={"started_at": SESSION_AT + timedelta(minutes=i)}) for i in range(n)]
    return base.model_copy(update={"items": items[::-1]})


def _self_reports_with(n: int) -> SelfReportList:
    """SelfReportList del ejemplo ampliado a `n` autoinformes, un minuto de separación, en orden inverso."""
    base = ExampleAppReader().get_self_reports(AST, WIDE_FROM, WIDE_TO)
    t0 = datetime(2027, 2, 9, 8, 30, tzinfo=UTC)
    items = [base.items[0].model_copy(update={"timestamp": t0 + timedelta(minutes=i)}) for i in range(n)]
    return base.model_copy(update={"items": items[::-1]})


def _simulate_request() -> SimulateRequest:
    return SimulateRequest.model_validate(json.loads((EXAMPLES / "engine" / "simulate-request.json").read_text(encoding="utf-8")))


# --- errores ---


def test_upstream_error_str_with_detail():
    assert str(UpstreamError(404, "Not found", "No such thing")) == "Not found: No such thing"


def test_upstream_error_str_without_detail():
    assert str(UpstreamError(500, "Boom")) == "Boom"


def test_upstream_error_from_problem_copies_status_title_detail():
    problem = AppErrorResponse.model_validate({"type": "not_found", "title": "Assessment not found", "status": 404, "detail": "No assessment with id x."})
    err = UpstreamError.from_problem(problem)
    assert (err.status, err.title, err.detail) == (404, "Assessment not found", "No assessment with id x.")
    assert isinstance(err, Exception)


def test_upstream_error_from_problem_without_detail():
    err = UpstreamError.from_problem(AppErrorResponse.model_validate({"type": "internal", "title": "Oops", "status": 500}))
    assert err.detail is None


def test_upstream_error_to_problem_validates_against_schema():
    problem = UpstreamError(422, "Invalid range", "to < from").to_problem("invalid_range")
    validate("app/error", dump(problem))
    assert (problem.status, problem.title, problem.detail) == (422, "Invalid range", "to < from")


# --- get_status ---


def test_get_status_returns_example_for_matching_astronaut():
    status = ExampleAppReader().get_status(AST)
    assert status.astronaut_id == AST
    validate("app/status", dump(status))


def test_get_status_unknown_astronaut_is_404():
    with pytest.raises(UpstreamError) as exc:
        ExampleAppReader().get_status("AST-99")
    assert exc.value.status == 404
    assert "AST-99" in (exc.value.detail or "")


# --- get_history ---


def test_get_history_echoes_request_and_validates():
    from_, to = datetime(2027, 2, 9, 0, 0, tzinfo=UTC), datetime(2027, 2, 9, 23, 59, tzinfo=UTC)
    history = ExampleAppReader().get_history(AST, from_, to, 50)
    assert (history.astronaut_id, history.from_, history.to, history.limit, history.truncated) == (AST, from_, to, 50, False)
    assert len(history.feature_sets) == 1
    assert len(history.assessments) == 1
    validate("app/history", dump(history))


def test_get_history_default_limit_is_100():
    assert ExampleAppReader().get_history(AST, WIDE_FROM, WIDE_TO).limit == 100


def test_get_history_unknown_astronaut_is_404():
    with pytest.raises(UpstreamError) as exc:
        ExampleAppReader().get_history("AST-99", WIDE_FROM, WIDE_TO)
    assert exc.value.status == 404


def test_get_history_reversed_range_is_422_invalid_range():
    with pytest.raises(UpstreamError) as exc:
        ExampleAppReader().get_history(AST, WIDE_TO, WIDE_FROM)
    assert exc.value.status == 422
    assert exc.value.title == "invalid_range"


def test_get_history_equal_bounds_are_a_valid_range():
    instant = datetime(2027, 2, 9, 9, 0, tzinfo=UTC)
    history = ExampleAppReader().get_history(AST, instant, instant)
    assert [a.as_of for a in history.assessments] == [instant]
    assert history.feature_sets == []


def test_get_history_filters_outside_window_out():
    history = ExampleAppReader().get_history(AST, datetime(2027, 3, 1, tzinfo=UTC), datetime(2027, 3, 2, tzinfo=UTC))
    assert history.feature_sets == []
    assert history.assessments == []
    assert history.truncated is False


def test_get_history_filter_is_inclusive_on_both_bounds():
    history = ExampleAppReader().get_history(AST, datetime(2027, 2, 9, 8, 55, tzinfo=UTC), datetime(2027, 2, 9, 9, 0, tzinfo=UTC))
    assert len(history.feature_sets) == 1
    assert len(history.assessments) == 1


def test_get_history_orders_oldest_first():
    history = ExampleAppReader(history=_history_with(5)).get_history(AST, WIDE_FROM, WIDE_TO)
    assert [f.computed_at for f in history.feature_sets] == sorted(f.computed_at for f in history.feature_sets)
    assert [a.as_of for a in history.assessments] == sorted(a.as_of for a in history.assessments)
    assert len(history.feature_sets) == 5
    assert history.truncated is False


def test_get_history_limit_keeps_most_recent_and_flags_truncated():
    reader = ExampleAppReader(history=_history_with(5))
    history = reader.get_history(AST, WIDE_FROM, WIDE_TO, limit=2)
    full = reader.get_history(AST, WIDE_FROM, WIDE_TO)
    assert history.truncated is True
    assert history.limit == 2
    assert history.feature_sets == full.feature_sets[-2:]
    assert history.assessments == full.assessments[-2:]


def test_get_history_limit_equal_to_count_is_not_truncated():
    history = ExampleAppReader(history=_history_with(3)).get_history(AST, WIDE_FROM, WIDE_TO, limit=3)
    assert history.truncated is False
    assert len(history.feature_sets) == 3


def test_get_history_truncated_when_only_one_list_exceeds_limit():
    base = _history_with(3)
    reader = ExampleAppReader(history=base.model_copy(update={"assessments": base.assessments[:1]}))
    history = reader.get_history(AST, WIDE_FROM, WIDE_TO, limit=2)
    assert history.truncated is True
    assert len(history.feature_sets) == 2
    assert len(history.assessments) == 1


# --- get_sessions ---


def test_get_sessions_echoes_request_and_validates():
    sessions = ExampleAppReader().get_sessions(AST, SESSION_FROM, SESSION_TO, 50)
    assert (sessions.astronaut_id, sessions.from_, sessions.to, sessions.limit, sessions.truncated) == (AST, SESSION_FROM, SESSION_TO, 50, False)
    assert len(sessions.items) == 1
    validate("app/session-list", dump(sessions))


def test_get_sessions_default_limit_is_100():
    assert ExampleAppReader().get_sessions(AST, SESSION_FROM, SESSION_TO).limit == 100


def test_get_sessions_unknown_astronaut_is_404():
    with pytest.raises(UpstreamError) as exc:
        ExampleAppReader().get_sessions("AST-99", SESSION_FROM, SESSION_TO)
    assert exc.value.status == 404


def test_get_sessions_reversed_range_is_422_invalid_range():
    with pytest.raises(UpstreamError) as exc:
        ExampleAppReader().get_sessions(AST, SESSION_TO, SESSION_FROM)
    assert exc.value.status == 422
    assert exc.value.title == "invalid_range"


def test_get_sessions_equal_bounds_are_a_valid_range():
    sessions = ExampleAppReader().get_sessions(AST, SESSION_AT, SESSION_AT)
    assert [s.started_at for s in sessions.items] == [SESSION_AT]


def test_get_sessions_filters_by_started_at_outside_window_out():
    sessions = ExampleAppReader().get_sessions(AST, WIDE_FROM, WIDE_TO)
    assert sessions.items == []
    assert sessions.truncated is False


def test_get_sessions_filter_is_inclusive_on_both_bounds():
    reader = ExampleAppReader(sessions=_sessions_with(3))
    sessions = reader.get_sessions(AST, SESSION_AT + timedelta(minutes=1), SESSION_AT + timedelta(minutes=2))
    assert [s.started_at for s in sessions.items] == [SESSION_AT + timedelta(minutes=1), SESSION_AT + timedelta(minutes=2)]


def test_get_sessions_orders_oldest_first():
    sessions = ExampleAppReader(sessions=_sessions_with(5)).get_sessions(AST, SESSION_FROM, SESSION_TO)
    assert [s.started_at for s in sessions.items] == sorted(s.started_at for s in sessions.items)
    assert len(sessions.items) == 5
    assert sessions.truncated is False


def test_get_sessions_limit_keeps_most_recent_and_flags_truncated():
    reader = ExampleAppReader(sessions=_sessions_with(5))
    sessions = reader.get_sessions(AST, SESSION_FROM, SESSION_TO, limit=2)
    full = reader.get_sessions(AST, SESSION_FROM, SESSION_TO)
    assert sessions.truncated is True
    assert sessions.limit == 2
    assert sessions.items == full.items[-2:]


def test_get_sessions_limit_equal_to_count_is_not_truncated():
    sessions = ExampleAppReader(sessions=_sessions_with(3)).get_sessions(AST, SESSION_FROM, SESSION_TO, limit=3)
    assert sessions.truncated is False
    assert len(sessions.items) == 3


# --- get_self_reports ---


def test_get_self_reports_echoes_request_and_validates():
    reports = ExampleAppReader().get_self_reports(AST, WIDE_FROM, WIDE_TO, 50)
    assert (reports.astronaut_id, reports.from_, reports.to, reports.limit, reports.truncated) == (AST, WIDE_FROM, WIDE_TO, 50, False)
    assert len(reports.items) == 1
    validate("app/self-report-list", dump(reports))


def test_get_self_reports_default_limit_is_100():
    assert ExampleAppReader().get_self_reports(AST, WIDE_FROM, WIDE_TO).limit == 100


def test_get_self_reports_unknown_astronaut_is_404():
    with pytest.raises(UpstreamError) as exc:
        ExampleAppReader().get_self_reports("AST-99", WIDE_FROM, WIDE_TO)
    assert exc.value.status == 404


def test_get_self_reports_reversed_range_is_422_invalid_range():
    with pytest.raises(UpstreamError) as exc:
        ExampleAppReader().get_self_reports(AST, WIDE_TO, WIDE_FROM)
    assert exc.value.status == 422
    assert exc.value.title == "invalid_range"


def test_get_self_reports_equal_bounds_are_a_valid_range():
    instant = datetime(2027, 2, 9, 8, 30, tzinfo=UTC)
    assert [r.timestamp for r in ExampleAppReader().get_self_reports(AST, instant, instant).items] == [instant]


def test_get_self_reports_filters_by_timestamp_outside_window_out():
    reports = ExampleAppReader().get_self_reports(AST, datetime(2027, 3, 1, tzinfo=UTC), datetime(2027, 3, 2, tzinfo=UTC))
    assert reports.items == []
    assert reports.truncated is False


def test_get_self_reports_filter_is_inclusive_on_both_bounds():
    t0 = datetime(2027, 2, 9, 8, 30, tzinfo=UTC)
    reader = ExampleAppReader(self_reports=_self_reports_with(3))
    reports = reader.get_self_reports(AST, t0 + timedelta(minutes=1), t0 + timedelta(minutes=2))
    assert [r.timestamp for r in reports.items] == [t0 + timedelta(minutes=1), t0 + timedelta(minutes=2)]


def test_get_self_reports_orders_oldest_first():
    reports = ExampleAppReader(self_reports=_self_reports_with(5)).get_self_reports(AST, WIDE_FROM, WIDE_TO)
    assert [r.timestamp for r in reports.items] == sorted(r.timestamp for r in reports.items)
    assert len(reports.items) == 5
    assert reports.truncated is False


def test_get_self_reports_limit_keeps_most_recent_and_flags_truncated():
    reader = ExampleAppReader(self_reports=_self_reports_with(5))
    reports = reader.get_self_reports(AST, WIDE_FROM, WIDE_TO, limit=2)
    full = reader.get_self_reports(AST, WIDE_FROM, WIDE_TO)
    assert reports.truncated is True
    assert reports.limit == 2
    assert reports.items == full.items[-2:]


def test_get_self_reports_limit_equal_to_count_is_not_truncated():
    reports = ExampleAppReader(self_reports=_self_reports_with(3)).get_self_reports(AST, WIDE_FROM, WIDE_TO, limit=3)
    assert reports.truncated is False
    assert len(reports.items) == 3


# --- get_explanation ---


def test_get_explanation_returns_example_for_matching_id():
    explanation = ExampleAppReader().get_explanation(ASSESSMENT_ID)
    assert explanation.assessment.assessment_id == ASSESSMENT_ID
    validate("app/explanation", dump(explanation))


def test_get_explanation_unknown_id_is_404():
    with pytest.raises(UpstreamError) as exc:
        ExampleAppReader().get_explanation("a55e55e0-0000-4000-8000-0000000000ff")
    assert exc.value.status == 404


# --- simulador y base ---


def test_simulator_returns_hypothetical_example():
    response = ExampleSimulator().simulate(_simulate_request())
    assert response.hypothetical is True
    validate("engine/simulate-response", dump(response))


def test_base_source_returns_example_base_for_matching_astronaut():
    base = ExampleAssessBaseSource().get_base(AST)
    assert base.astronaut_id == AST
    validate("engine/assess-request", dump(base))


def test_base_source_unknown_astronaut_is_404():
    with pytest.raises(UpstreamError) as exc:
        ExampleAssessBaseSource().get_base("AST-99")
    assert exc.value.status == 404


# --- protocolos ---


def test_mocks_are_usable_as_their_ports():
    reader: AppReader = ExampleAppReader()
    simulator: Simulator = ExampleSimulator()
    source: AssessBaseSource = ExampleAssessBaseSource()
    assert reader.get_status(AST).astronaut_id == source.get_base(AST).astronaut_id
    assert simulator.simulate(_simulate_request()).hypothetical


def test_ports_expose_only_read_or_simulate_methods():
    forbidden = ("write", "update", "delete", "create", "set", "put", "post", "patch", "remove", "insert", "save")
    expected = {
        AppReader: {"get_status", "get_history", "get_explanation", "get_sessions", "get_self_reports"},
        Simulator: {"simulate"},
        AssessBaseSource: {"get_base"},
    }
    for port, names in expected.items():
        # mutmut añade a la clase copias renombradas de cada método (`x…__mutmut_orig`, `__mutmut_1`...): no son parte del puerto.
        public = {n for n, _ in inspect.getmembers(port, inspect.isfunction) if not n.startswith("_") and "__mutmut" not in n}
        assert public == names
        assert not [n for n in public if n.startswith(forbidden)]
