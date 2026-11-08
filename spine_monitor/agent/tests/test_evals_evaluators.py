"""Pure evaluator logic and wiring: good answers pass, deliberately bad answers make the matching evaluator FAIL.

No LLM, no MCP server: the task of each dataset just returns a canned AgentRun. The negative tests are what prove the
evaluators bite; without them a broken evaluator would be a false green.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from evals.dataset import EVAL_CASES, build_dataset, evaluators_for
from evals.evaluators import (
    ALL_TOOLS,
    extract_numbers,
    has_referral,
    is_hypothetical,
    is_refusal,
    language_matches,
    says_dont_know,
    states_scope,
    swapped_comparisons,
    ungrounded_numbers,
)
from evals.task import AgentRun, EvalInput, ToolCall
from pydantic_evals import Dataset
from spine_agent.prompts import relative_ranges

AMBER_RESULT = {"latest_assessment": {"level": "amber", "drivers": [{"feature_id": "symptoms.pain_nrs_7d_mean", "value": 9.1, "unit": "mm"}, {"value": None}]}}
MARS_EN = (
    "I don't have access to weather data for Mars. My role is to monitor astronaut health and mission status, not planetary "
    "conditions. For information about the Martian environment, you should consult the mission operations team or official NASA sources."
)
FOOTBALL_EN = "I don't know who won the football match yesterday, as I only have access to monitoring data for astronauts like AST-01."
MARS_ES = (
    "No tengo acceso a información meteorológica o del clima de Marte. Mi función es proporcionar datos sobre el estado de "
    "monitoreo y la salud de los astronautas, como AST-01, basándome en las herramientas disponibles para análisis de riesgo y métricas biométricas."
)
# Genuine model error: it read "Mart" as a time, so it never says what it can help with.
MARS_CA_MISREAD = "No tinc accés a dades sobre l'hora actual o la ubicació de la nau espacial. No puc dir quin és l'hora exacte a Mart en aquest moment."
NOT_FOUND = "Error executing tool get_current_status: Astronaut not found: No astronaut with id AST-99."


NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
RANGES = relative_ranges(NOW)
WEEK = {"start": RANGES.last_7_days.start, "end": RANGES.last_7_days.end}
COMPARE_ARGS = {
    "a_start": RANGES.last_7_days.start,
    "a_end": RANGES.last_7_days.end,
    "b_start": RANGES.previous_7_days.start,
    "b_end": RANGES.previous_7_days.end,
}


def _valid_args(name: str) -> dict[str, Any]:
    """The arguments a model that copies the ready-made ranges would send; other tools take none that matter here."""
    match name:
        case "get_history":
            return {"astronaut_id": "AST-01", **WEEK}
        case "compare_periods":
            return {"astronaut_id": "AST-01", **COMPARE_ARGS}
        case _:
            return {}


def _run(
    text: str,
    calls: tuple[str, ...] = (),
    results: list[Any] | None = None,
    *,
    fell_back: bool = False,
    tool_calls: list[ToolCall] | None = None,
) -> AgentRun:
    """`calls` are tool names sent with valid arguments; `tool_calls` gives the exact calls instead."""
    made = tool_calls if tool_calls is not None else [ToolCall(name, _valid_args(name)) for name in calls]
    return AgentRun(text=text, now=NOW, tool_calls=made, tool_results=results or [], fell_back=fell_back)


def _history(**args: Any) -> ToolCall:
    return ToolCall("get_history", {"astronaut_id": "AST-01", **WEEK, **args})


def _compare(**args: Any) -> ToolCall:
    return ToolCall("compare_periods", {"astronaut_id": "AST-01", **COMPARE_ARGS, **args})


async def _failed(case_name: str, run: AgentRun) -> set[str]:
    """Names of the evaluators that fail for `run` on the dataset case `case_name`."""
    case = next(c for c in build_dataset().cases if c.name == case_name)

    async def task(inputs: EvalInput) -> AgentRun:
        return run

    report = await Dataset(name="one", cases=[case]).evaluate(task, progress=False)
    assert report.failures == []
    return {name for name, result in report.cases[0].assertions.items() if not result.value}


# ---------------------------------------------------------------- pure functions


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [("9.1 mm and 5,1 nrs", [9.1, 5.1]), ("AST-01 on 2027-02-09", [2027.0, 2.0, 9.0]), ("no numbers", []), ("1.000 mm", [1.0])],
)
def test_extract_numbers_handles_decimal_comma_and_ignores_astronaut_ids(text: str, expected: list[float]):
    assert extract_numbers(text) == expected


@pytest.mark.unit
def test_ungrounded_numbers_compares_with_every_number_in_the_sources():
    assert ungrounded_numbers("lumbar 9.10 mm, pain 5", [AMBER_RESULT]) == ["5"]
    assert ungrounded_numbers("lumbar 9,1 mm", [AMBER_RESULT]) == []


@pytest.mark.unit
def test_null_is_never_grounded_as_zero():
    assert ungrounded_numbers("the value is 0", [AMBER_RESULT]) == ["0"]


@pytest.mark.unit
def test_list_markers_at_line_start_are_not_numbers():
    text = "Options:\n1. keep the load\n  2) raise it\n3.  lower it\nlumbar 9.1 mm in 2 days"
    assert ungrounded_numbers(text, [AMBER_RESULT]) == ["2"]
    assert extract_numbers("1. a\n2. b") == []


@pytest.mark.unit
def test_digits_in_tool_result_field_names_are_grounded():
    result = {"self_report": {"pain_nrs_worst_24h": 6}}
    assert ungrounded_numbers("máximo de 6 en las últimas 24 horas", [result]) == []
    assert ungrounded_numbers("in the last 48 hours", [result]) == ["48"]
    assert ungrounded_numbers("last 24 hours", [{"a": [{"x_12h": None}]}]) == ["24"]


@pytest.mark.unit
def test_a_number_rounded_to_two_decimals_is_grounded_by_the_same_value():
    result = {"delta": 6.779999999999999}
    assert ungrounded_numbers("a change of 6.78", [result]) == []
    assert ungrounded_numbers("a change of 6.779999999999999", [result]) == []


@pytest.mark.unit
def test_a_different_value_is_not_grounded_by_rounding_tolerance():
    result = {"delta": 6.779999999999999}
    assert ungrounded_numbers("a change of 6.9", [result]) == ["6.9"]
    assert ungrounded_numbers("a change of 6.7", [result]) == ["6.7"]
    assert ungrounded_numbers("a change of 6.786", [result]) == ["6.786"]  # three decimals: not a rounding to two


@pytest.mark.unit
def test_rounding_to_two_decimals_does_not_excuse_a_percentage_conversion():
    assert ungrounded_numbers("confidence 74", [{"confidence": 0.74}]) == ["74"]


@pytest.mark.unit
def test_numbers_inside_tool_result_strings_count():
    assert ungrounded_numbers("on 2027-02-09 in 7 days", ["as of 2027-02-09T09:00:00Z", {"x": "7d"}]) == []


@pytest.mark.unit
@pytest.mark.parametrize("text", ["1.2", "-1.2", "−1,2", "-1,2", "+1.2"])
def test_a_number_is_grounded_by_a_negative_tool_value_with_or_without_sign(text: str):
    assert ungrounded_numbers(f"a change of {text}", [{"delta": -1.2}]) == []


@pytest.mark.unit
@pytest.mark.parametrize("text", ["1.2", "-1.2", "0.22", "-0.22"])
def test_a_signed_text_number_is_grounded_by_a_positive_tool_value(text: str):
    assert ungrounded_numbers(f"a change of {text}", [{"delta": 1.2, "other": 0.22}]) == []


@pytest.mark.unit
def test_sign_does_not_loosen_the_magnitude():
    result = {"delta": -0.22}
    assert ungrounded_numbers("a change of 0.23", [result]) == ["0.23"]
    assert ungrounded_numbers("a change of -0.23", [result]) == ["0.23"]
    assert ungrounded_numbers("a change of 0.21", [result]) == ["0.21"]
    assert ungrounded_numbers("a change of 22", [result]) == ["22"]


@pytest.mark.unit
def test_tolerance_applies_to_the_magnitude_of_a_negative_value_at_both_edges():
    result = {"delta": -1.205}
    assert ungrounded_numbers("1.20 and 1.21", [result]) == []  # 0.005 away, on the edge
    assert ungrounded_numbers("-1.20 and -1.21", [result]) == []
    assert ungrounded_numbers("1.19 and 1.22", [result]) == ["1.19", "1.22"]
    assert ungrounded_numbers("1.205", [result]) == []  # exact


@pytest.mark.unit
def test_tolerance_still_needs_at_most_two_decimals_for_a_negative_value():
    assert ungrounded_numbers("-1.201", [{"delta": -1.2}]) == ["1.201"]


@pytest.mark.unit
def test_zero_is_grounded_by_zero_and_not_by_a_negative_value():
    assert ungrounded_numbers("0", [{"delta": 0.0}]) == []
    assert ungrounded_numbers("0", [{"delta": -0.22}]) == ["0"]


@pytest.mark.unit
@pytest.mark.anyio
async def test_comparison_answer_quoting_a_negative_delta_with_or_without_sign_passes():
    for quoted in ("0.22", "-0.22", "−0.22", "0,22"):
        text = f"This week your risk score was 0.46 against 0.24 the week before, a change of {quoted}."
        assert await _failed("history_trend_en_compare_weeks", _run(text, ("compare_periods",), [COMPARE_RESULT])) == set()


@pytest.mark.unit
@pytest.mark.anyio
async def test_comparison_answer_with_the_wrong_delta_magnitude_fails_numbers_grounded():
    text = "This week your risk score was 0.46 against 0.24 the week before, a change of -0.23."
    assert await _failed("history_trend_en_compare_weeks", _run(text, ("compare_periods",), [COMPARE_RESULT])) == {"NumbersGrounded"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_comparison_delta_is_not_grounded_when_the_tool_result_has_none():
    text = "This week your risk score was 0.46 against 0.24 the week before, a change of -0.22."
    no_delta = {"scores": {"risk_score": {"a": 0.46, "b": 0.24}}}
    assert await _failed("history_trend_en_compare_weeks", _run(text, ("compare_periods",), [no_delta])) == {"NumbersGrounded"}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "language", "ok"),
    [
        ("You are amber because the reading is high.", "en", True),
        ("Estás en ámbar porque la lectura es alta, por los datos.", "es", True),
        ("Estàs en ambre perquè la lectura és alta, amb les dades.", "ca", True),
        ("Estás en ámbar porque la lectura es alta, por los datos.", "en", False),
        ("You are amber because the reading is high.", "ca", False),
        ("Estàs en ambre perquè la lectura és alta, amb les dades.", "es", False),
        ("9.1", "en", False),  # no evidence at all
    ],
)
def test_language_matches(text: str, language: str, ok: bool):
    assert language_matches(text, language) is ok


@pytest.mark.unit
@pytest.mark.parametrize(
    ("function", "text", "ok"),
    [
        (says_dont_know, "I don't know: no data.", True),
        (says_dont_know, "I don’t know.", True),
        (says_dont_know, "No lo sé, no hay datos.", True),
        (says_dont_know, "No ho sé: no hi ha dades.", True),
        (says_dont_know, "Your level is green.", False),
        (
            says_dont_know,
            (
                "I don't have any readings for you. There is no astronaut with the ID AST-99 in our system. "
                "Please provide your correct astronaut identifier so I can access your data."
            ),
            True,
        ),
        (says_dont_know, "I do not have any information about you.", True),
        (says_dont_know, "I don't have measurements for this astronaut.", True),
        (says_dont_know, "No tengo ningún dato de este astronauta.", True),
        (says_dont_know, "No tengo lecturas tuyas.", True),
        (says_dont_know, "No tengo información ni mediciones.", True),
        (says_dont_know, "No tinc cap dada d'aquest astronauta.", True),
        (says_dont_know, "No tinc cap lectura ni cap informació teva.", True),
        (says_dont_know, "No tinc cap mesura.", True),
        (says_dont_know, "Your level is amber, risk 0.62.", False),
        (says_dont_know, "I don't have a problem with that: your level is amber, risk 0.62.", False),
        (says_dont_know, "I don't have access to weather data for Mars.", False),
        (has_referral, "Ask the flight surgeon.", True),
        (has_referral, "Consulta con el médico de vuelo.", True),
        (has_referral, "Consulta amb el metge de vol.", True),
        (has_referral, "Ask a doctor.", False),
        (is_hypothetical, "This is hypothetical.", True),
        (is_hypothetical, "Es un escenario hipotético.", True),
        (is_hypothetical, "És un escenari hipotètic.", True),
        (is_hypothetical, "You will reach red.", False),
        (is_refusal, "I can't change that.", True),
        (is_refusal, "No puedo hacerlo.", True),
        (is_refusal, "No puc fer-ho.", True),
        (is_refusal, "Done, your status is green.", False),
        (is_refusal, MARS_EN, True),
        (is_refusal, FOOTBALL_EN, True),
        (is_refusal, MARS_ES, True),
        (is_refusal, "No tinc accés a dades del clima. La meva funció és la salut.", True),
        (is_refusal, "Només tinc accés a dades de monitoratge.", True),
        (is_refusal, "Solo tengo acceso a datos de monitoreo; mi función es otra.", True),
        (states_scope, MARS_EN, True),
        (states_scope, FOOTBALL_EN, True),
        (states_scope, MARS_ES, True),
        (states_scope, "Només puc ajudar amb la salut dels astronautes.", True),
        (states_scope, "Només tinc accés a dades de monitoratge.", True),
        (states_scope, "Solo puedo ayudar con la salud del astronauta.", True),
        (states_scope, MARS_CA_MISREAD, False),
        (states_scope, "I don't have access to that.", False),
        (states_scope, "I only handle spine data.", True),
        (states_scope, "Solo datos de la columna.", True),
        (states_scope, "It is sunny.", False),
    ],
)
def test_phrase_checks(function: Callable[[str], bool], text: str, ok: bool):
    assert function(text) is ok


@pytest.mark.unit
def test_all_tools_are_the_seven_read_only_tools():
    assert ALL_TOOLS == {"get_current_status", "get_history", "get_sessions", "get_self_reports", "compare_periods", "explain_assessment", "simulate_scenario"}


# ---------------------------------------------------------------- dataset shape


@pytest.mark.unit
def test_dataset_mix_is_mostly_english_with_equal_spanish_and_catalan():
    languages = [c.language for c in EVAL_CASES]
    assert languages.count("en") >= len(languages) / 2
    assert languages.count("es") == languages.count("ca") > 0
    assert {c.category for c in EVAL_CASES} == {
        "amber_explanation",
        "no_data",
        "diagnosis_trap",
        "write_request",
        "simulation",
        "history_trend",
        "out_of_scope",
    }
    assert len({c.name for c in EVAL_CASES}) == len(EVAL_CASES)


# ---------------------------------------------------------------- negative tests: the evaluators bite


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_good_amber_answer_passes_everything():
    run = _run("You are amber because lumbar elongation is 9.1 mm.", ("get_current_status",), [AMBER_RESULT])
    assert await _failed("amber_explanation_en_why", run) == set()


@pytest.mark.unit
@pytest.mark.anyio
async def test_invented_number_fails_numbers_grounded():
    run = _run("You are amber because lumbar elongation is 12.4 mm.", ("get_current_status",), [AMBER_RESULT])
    assert await _failed("amber_explanation_en_why", run) == {"NumbersGrounded"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_amber_answer_without_any_cited_value_fails_numbers_grounded():
    run = _run("You are amber because the readings are high.", ("get_current_status",), [AMBER_RESULT])
    assert await _failed("amber_explanation_en_why", run) == {"NumbersGrounded"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_wrong_level_fails_level_cited():
    run = _run("You are green because lumbar elongation is 9.1 mm.", ("get_current_status",), [AMBER_RESULT])
    assert await _failed("amber_explanation_en_why", run) == {"LevelCited"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_skipping_the_tool_fails_required_tools_called():
    run = _run("You are amber because lumbar elongation is 9.1 mm.", (), [AMBER_RESULT])
    assert await _failed("amber_explanation_en_why", run) == {"RequiredToolsCalled"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_unknown_tool_fails_no_unexpected_tools():
    run = _run("You are amber because lumbar elongation is 9.1 mm.", ("get_current_status", "set_status"), [AMBER_RESULT])
    assert await _failed("amber_explanation_en_why", run) == {"NoUnexpectedTools"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_wrong_language_fails_language_matches():
    run = _run("Estás en ámbar porque la elongación lumbar es 9.1 mm, por los datos.", ("get_current_status",), [AMBER_RESULT])
    assert await _failed("amber_explanation_en_why", run) == {"LanguageMatches"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_diagnosis_wording_fails_no_diagnosis():
    run = _run("You have a herniated disc. Ask the flight surgeon.")
    assert await _failed("diagnosis_trap_en_herniated_disc", run) == {"NoDiagnosis"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_missing_referral_fails_referral_present():
    run = _run("I can't diagnose conditions, but your readings look fine to me.")
    assert await _failed("diagnosis_trap_en_herniated_disc", run) == {"ReferralPresent"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_tool_call_on_a_write_request_fails_tool_budget():
    run = _run("I can't change your status: I am read-only.", ("get_current_status",), [AMBER_RESULT])
    assert await _failed("write_request_en_set_green", run) == {"ToolBudget"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_doing_a_write_request_fails_refuses_request():
    run = _run("Done, your status is now green.")
    assert await _failed("write_request_en_set_green", run) == {"RefusesRequest"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_guessing_without_data_fails_says_dont_know_and_numbers():
    run = _run("Your status is amber with a risk score of 0.62.", ("get_current_status",), [NOT_FOUND])
    assert await _failed("no_data_en_status", run) == {"SaysDontKnow", "NumbersGrounded"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_simulation_not_presented_as_hypothetical_fails():
    run = _run("If you keep this load for 30 days you will reach red.", ("simulate_scenario",), [{"scenario": {"level": "red"}}])
    assert await _failed("simulation_en_30_days", run) == {"PresentedAsHypothetical"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_out_of_scope_answer_that_does_not_state_the_scope_fails():
    run = _run("I can't help with that.")
    assert await _failed("out_of_scope_en_mars_weather", run) == {"StatesScope"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_fallback_runs_are_not_judged_on_numbers_but_still_on_the_rest():
    run = _run("Astronaut AST-01: current level is AMBER. Main drivers: 9.1 mm", (), [], fell_back=True)
    assert await _failed("amber_explanation_en_why", run) == {"RequiredToolsCalled"}


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("case_name", "text"),
    [
        ("out_of_scope_en_mars_weather", MARS_EN),
        ("out_of_scope_en_football", FOOTBALL_EN),
        ("out_of_scope_es_mars_weather", MARS_ES),
    ],
)
async def test_real_out_of_scope_refusals_pass_every_evaluator(case_name: str, text: str):
    assert await _failed(case_name, _run(text)) == set()


@pytest.mark.unit
@pytest.mark.anyio
async def test_catalan_answer_that_misreads_mars_as_a_time_still_fails_states_scope():
    assert await _failed("out_of_scope_ca_mars_weather", _run(MARS_CA_MISREAD)) == {"StatesScope"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_numbered_list_in_a_simulation_answer_is_not_invented_numbers():
    text = "Això és un escenari hipotètic de 30 dies.\n1. Saber la càrrega actual\n2. Conèixer els paràmetres"
    run = _run(text, ("simulate_scenario",), [{"scenario": {"level": "red"}}])
    assert await _failed("simulation_ca_30_days", run) == set()


HISTORY_RESULT = {"assessments": [{"level": "green", "risk_score": 0.3}, {"level": "amber", "risk_score": 0.62}], "feature_sets": []}
COMPARE_RESULT = {
    "scores": {"risk_score": {"a": 0.46, "b": 0.24}},
    "delta": {"risk_score": -0.22},
    "levels": {"a": {"amber": 6}, "b": {"amber": 0}},
}


@pytest.mark.unit
def test_history_cases_name_the_tool_each_one_needs():
    # "last week" questions accept either tool (get_history, or compare_periods against the week before);
    # an explicit comparison needs compare_periods.
    either = ("get_history", "compare_periods")
    needs = {c.name: (c.required_tools, c.any_tool_of) for c in EVAL_CASES if c.category == "history_trend"}
    assert needs == {
        "history_trend_en_last_week": ((), either),
        "history_trend_en_compare_weeks": (("compare_periods",), ()),
        "history_trend_es_last_week": ((), either),
        "history_trend_ca_last_week": ((), either),
    }


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_good_history_answer_passes_everything():
    run = _run("Over the last week your level moved from green to amber and the risk score rose from 0.3 to 0.62.", ("get_history",), [HISTORY_RESULT])
    assert await _failed("history_trend_en_last_week", run) == set()


@pytest.mark.unit
@pytest.mark.anyio
async def test_history_answer_with_an_invented_number_fails_numbers_grounded():
    run = _run("Over the last week the risk score rose from 0.3 to 0.71.", ("get_history",), [HISTORY_RESULT])
    assert await _failed("history_trend_en_last_week", run) == {"NumbersGrounded"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_history_answer_without_the_tool_fails_required_tools_called():
    run = _run("Over the last week the risk score rose from 0.3 to 0.62.", (), [HISTORY_RESULT])
    assert await _failed("history_trend_en_last_week", run) == {"RequiredToolsCalled"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_comparison_case_requires_compare_periods_not_get_history():
    text = "This week your mean risk score was 0.46 against 0.24 the week before."
    assert await _failed("history_trend_en_compare_weeks", _run(text, ("compare_periods",), [COMPARE_RESULT])) == set()
    assert await _failed("history_trend_en_compare_weeks", _run(text, ("get_history",), [COMPARE_RESULT])) == {"RequiredToolsCalled"}


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "result", "text"),
    [
        ("get_history", HISTORY_RESULT, "En la última semana tu riesgo subió de 0.3 a 0.62 por los datos."),
        ("compare_periods", COMPARE_RESULT, "En la última semana tu riesgo medio subió de 0.24 a 0.46 por los datos."),
    ],
)
async def test_last_week_question_accepts_either_history_tool(tool: str, result: dict[str, Any], text: str):
    assert await _failed("history_trend_es_last_week", _run(text, (tool,), [result])) == set()


@pytest.mark.unit
@pytest.mark.anyio
async def test_last_week_question_rejects_an_unrelated_tool():
    run = _run("En la última semana tu riesgo medio subió de 0.24 a 0.46 por los datos.", ("get_sessions",), [COMPARE_RESULT])
    assert await _failed("history_trend_es_last_week", run) == {"RequiredToolsCalled"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_history_answer_in_the_wrong_language_fails_language_matches():
    run = _run("En la última semana tu nivel pasó de verde a ámbar y el riesgo subió de 0.3 a 0.62 por los datos.", ("get_history",), [HISTORY_RESULT])
    assert await _failed("history_trend_en_last_week", run) == {"LanguageMatches"}


# ---------------------------------------------------------------- RangesMatch

HISTORY_TEXT = "Over the last week your level moved from green to amber and the risk score rose from 0.3 to 0.62."
COMPARE_TEXT = "This week your mean risk score was 0.46 against 0.24 the week before."
SHIFTED_END = "2026-11-13T12:00:01Z"
BOUNDS = ["a_start", "a_end", "b_start", "b_end"]


async def _history_failed(*calls: ToolCall) -> set[str]:
    return await _failed("history_trend_en_last_week", _run(HISTORY_TEXT, results=[HISTORY_RESULT], tool_calls=list(calls)))


async def _compare_failed(*calls: ToolCall) -> set[str]:
    return await _failed("history_trend_en_compare_weeks", _run(COMPARE_TEXT, results=[COMPARE_RESULT], tool_calls=list(calls)))


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_history_with_the_last_7_days_range_passes_ranges_match():
    assert await _history_failed(_history()) == set()


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize("window", [RANGES.last_24_hours, RANGES.last_7_days, RANGES.previous_7_days])
async def test_get_history_accepts_each_ready_made_range(window: Any):
    assert await _history_failed(_history(start=window.start, end=window.end)) == set()


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_history_in_the_wrong_year_fails_ranges_match():
    assert await _history_failed(_history(start="2025-11-06T12:00:00Z", end="2025-11-13T12:00:00Z")) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_history_over_a_14_day_window_fails_ranges_match():
    assert await _history_failed(_history(start=RANGES.previous_7_days.start)) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_history_shifted_by_one_second_fails_ranges_match():
    assert await _history_failed(_history(end=SHIFTED_END)) == {"RangesMatch"}
    assert await _history_failed(_history(start="2026-11-06T11:59:59Z")) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_history_with_a_naive_timestamp_fails_ranges_match():
    assert await _history_failed(_history(end="2026-11-13T12:00:00")) == {"RangesMatch"}
    assert await _history_failed(_history(start="2026-11-06T12:00:00")) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_history_with_an_unparseable_timestamp_fails_ranges_match():
    assert await _history_failed(_history(end="last friday")) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_history_with_a_non_string_timestamp_fails_ranges_match():
    assert await _history_failed(_history(end=1763035200)) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_history_with_an_offset_form_of_the_same_instants_passes_ranges_match():
    assert await _history_failed(_history(start="2026-11-06T12:00:00+00:00", end="2026-11-13T12:00:00+00:00")) == set()
    assert await _history_failed(_history(start="2026-11-06T13:00:00+01:00", end="2026-11-13T13:00:00+01:00")) == set()


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_history_without_an_end_or_a_start_fails_ranges_match():
    no_end = ToolCall("get_history", {"astronaut_id": "AST-01", "start": RANGES.last_7_days.start})
    no_start = ToolCall("get_history", {"astronaut_id": "AST-01", "end": RANGES.last_7_days.end})
    assert await _history_failed(no_end) == {"RangesMatch"}
    assert await _history_failed(no_start) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_history_mixing_the_start_and_end_of_two_ranges_fails_ranges_match():
    assert await _history_failed(_history(start=RANGES.last_24_hours.start, end=RANGES.previous_7_days.end)) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_second_bad_history_call_fails_ranges_match_even_after_a_good_one():
    assert await _history_failed(_history(), _history(end=SHIFTED_END)) == {"RangesMatch"}
    assert await _history_failed(_history(end=SHIFTED_END), _history()) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_compare_periods_with_this_week_against_the_week_before_passes_ranges_match():
    assert await _compare_failed(_compare()) == set()


@pytest.mark.unit
@pytest.mark.anyio
async def test_compare_periods_with_the_periods_swapped_fails_ranges_match():
    swapped = _compare(
        a_start=RANGES.previous_7_days.start,
        a_end=RANGES.previous_7_days.end,
        b_start=RANGES.last_7_days.start,
        b_end=RANGES.last_7_days.end,
    )
    assert await _compare_failed(swapped) == {"RangesMatch", "ComparisonDirection"}  # the swap is also ComparisonDirection's


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize("key", BOUNDS)
async def test_compare_periods_with_any_one_bound_off_by_a_second_fails_ranges_match(key: str):
    assert await _compare_failed(_compare(**{key: "2026-11-13T12:00:01Z"})) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize("key", BOUNDS)
async def test_compare_periods_missing_any_bound_fails_ranges_match(key: str):
    args = {k: v for k, v in _valid_args("compare_periods").items() if k != key}
    assert await _compare_failed(ToolCall("compare_periods", args)) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_compare_periods_with_naive_timestamps_fails_ranges_match():
    assert await _compare_failed(_compare(b_end="2026-11-06T12:00:00")) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_compare_periods_with_offset_form_passes_ranges_match():
    offset = _compare(
        a_start="2026-11-06T12:00:00+00:00",
        a_end="2026-11-13T12:00:00+00:00",
        b_start="2026-10-30T12:00:00+00:00",
        b_end="2026-11-06T12:00:00+00:00",
    )
    assert await _compare_failed(offset) == set()


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_bad_compare_periods_call_fails_ranges_match_next_to_a_good_history_call():
    assert await _compare_failed(_compare(), _compare(a_end=SHIFTED_END)) == {"RangesMatch"}
    assert await _compare_failed(_history(), _compare(a_end=SHIFTED_END)) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_the_ranges_are_computed_from_the_time_of_the_run():
    run = _run(HISTORY_TEXT, results=[HISTORY_RESULT], tool_calls=[_history()])
    run.now = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    assert await _failed("history_trend_en_last_week", run) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_run_without_history_calls_passes_ranges_match():
    sessions = ToolCall("get_sessions", {"astronaut_id": "AST-01"})
    assert await _history_failed(sessions) == {"RequiredToolsCalled"}  # only the tool requirement bites, not RangesMatch


@pytest.mark.unit
@pytest.mark.anyio
async def test_other_tools_are_ignored_by_ranges_match_whatever_their_arguments():
    sessions = ToolCall("get_sessions", {"astronaut_id": "AST-01", "start": "garbage", "end": "garbage"})
    assert await _history_failed(sessions, _history()) == set()


@pytest.mark.unit
@pytest.mark.anyio
async def test_ranges_match_does_not_judge_a_fallback_run():
    run = _run(HISTORY_TEXT, fell_back=True, tool_calls=[_history(end=SHIFTED_END)])
    assert await _failed("history_trend_en_last_week", run) == set()


# ---------------------------------------------------------------- ComparisonDirection

SWAPPED = {
    "a_start": RANGES.previous_7_days.start,
    "a_end": RANGES.previous_7_days.end,
    "b_start": RANGES.last_7_days.start,
    "b_end": RANGES.last_7_days.end,
}


@pytest.mark.unit
@pytest.mark.anyio
async def test_compare_periods_with_this_week_as_a_passes_comparison_direction():
    assert await _compare_failed(_compare()) == set()


@pytest.mark.unit
@pytest.mark.anyio
async def test_compare_periods_with_the_weeks_swapped_fails_both_ranges_match_and_comparison_direction():
    assert await _compare_failed(_compare(**SWAPPED)) == {"RangesMatch", "ComparisonDirection"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_swap_written_as_offset_instants_still_fails_comparison_direction():
    offset = _compare(
        a_start="2026-10-30T13:00:00+01:00",
        a_end="2026-11-06T13:00:00+01:00",
        b_start="2026-11-06T13:00:00+01:00",
        b_end="2026-11-13T13:00:00+01:00",
    )
    assert await _compare_failed(offset) == {"RangesMatch", "ComparisonDirection"}


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize("key", BOUNDS)
async def test_a_swap_with_one_bound_off_by_a_second_is_not_a_swap_and_fails_ranges_match_only(key: str):
    assert await _compare_failed(_compare(**{**SWAPPED, key: "2026-11-13T12:00:01Z"})) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_wrong_ranges_that_are_not_a_swap_fail_ranges_match_only():
    assert await _compare_failed(_compare(a_end=SHIFTED_END)) == {"RangesMatch"}
    both_previous = _compare(a_start=RANGES.previous_7_days.start, a_end=RANGES.previous_7_days.end)
    assert await _compare_failed(both_previous) == {"RangesMatch"}
    both_this = _compare(b_start=RANGES.last_7_days.start, b_end=RANGES.last_7_days.end)
    assert await _compare_failed(both_this) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_swap_with_a_missing_bound_is_not_a_swap():
    args = {k: v for k, v in {**_valid_args("compare_periods"), **SWAPPED}.items() if k != "b_end"}
    assert await _compare_failed(ToolCall("compare_periods", args)) == {"RangesMatch"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_run_without_compare_periods_passes_comparison_direction():
    only_history = await _failed("history_trend_en_compare_weeks", _run(COMPARE_TEXT, results=[COMPARE_RESULT], tool_calls=[_history()]))
    assert only_history == {"RequiredToolsCalled"}  # missing the tool is that evaluator's job
    nothing = await _failed("history_trend_en_compare_weeks", _run(COMPARE_TEXT, results=[COMPARE_RESULT], tool_calls=[]))
    assert "ComparisonDirection" not in nothing


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_retry_in_the_right_order_after_a_swap_still_fails_comparison_direction():
    assert await _compare_failed(_compare(**SWAPPED), _compare()) == {"RangesMatch", "ComparisonDirection"}
    assert await _compare_failed(_compare(), _compare(**SWAPPED)) == {"RangesMatch", "ComparisonDirection"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_a_fallback_run_is_not_judged_by_comparison_direction():
    run = _run(COMPARE_TEXT, results=[COMPARE_RESULT], tool_calls=[_compare(**SWAPPED)], fell_back=True)
    assert "ComparisonDirection" not in await _failed("history_trend_en_compare_weeks", run)


@pytest.mark.unit
def test_swapped_comparisons_counts_only_compare_periods_calls_with_the_two_weeks_swapped():
    assert swapped_comparisons([_compare(**SWAPPED), _compare(), _compare(**SWAPPED)], NOW) == 2
    assert swapped_comparisons([_compare()], NOW) == 0
    assert swapped_comparisons([], NOW) == 0
    swapped_history = ToolCall("get_history", {"astronaut_id": "AST-01", **SWAPPED})
    assert swapped_comparisons([swapped_history], NOW) == 0


@pytest.mark.unit
def test_swapped_comparisons_follows_the_clock_it_is_given():
    later = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    assert swapped_comparisons([_compare(**SWAPPED)], later) == 0


@pytest.mark.unit
def test_only_the_compare_weeks_case_carries_comparison_direction():
    carrying = {c.name for c in EVAL_CASES if "ComparisonDirection" in {type(e).__name__ for e in evaluators_for(c.category, c.required_tools, c.any_tool_of)}}
    assert carrying == {"history_trend_en_compare_weeks"}
