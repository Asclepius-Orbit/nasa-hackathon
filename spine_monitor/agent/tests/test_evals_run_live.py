"""Offline tests of the pure parts of evals/run_live.py: gate flag, argument and model-list parsing, statistics, tables,
selection rule, preflight messages and the usage recorder. The benchmark itself (real Ollama) is never run by pytest."""

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pydantic_ai
import pytest
from evals import run_live
from evals.run_live import (
    Check,
    ModelSummary,
    RunRecord,
    UsageRecorder,
    build_quiet_agent,
    default_models,
    diagnose,
    diagnostic_json_line,
    format_comparison,
    format_failures,
    format_selection,
    format_summary,
    is_enabled,
    median,
    models_from_env_example,
    parse_args,
    percentile,
    preflight,
    select_winner,
    silence_noise,
    summarize,
    tokens_per_second,
)
from evals.task import AgentRun, EvalInput, ToolCall
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from spine_agent.agent import ChatSession, build_agent
from spine_agent.settings import AgentSettings

EXAMPLE = '# models\nSPINE_AGENT_LLM_MODELS="qwen3.5:4b qwen3.5:9b gemma4:e4b"\nSPINE_AGENT_MODEL=qwen3.5:4b\n'


def _record(
    category: str = "no_data", passed: bool = True, ttft: float | None = 1.0, total: float = 2.0, tps: float | None = 10.0, fell_back: bool = False
) -> RunRecord:
    return RunRecord(category=category, passed=passed, ttft_s=ttft, total_s=total, tokens_per_s=tps, fell_back=fell_back)


def _summary(model: str, passed: int, total: int, ttft: float | None) -> ModelSummary:
    records = [_record(passed=i < passed, ttft=ttft) for i in range(total)]
    return summarize(model, records)


# ---------------------------------------------------------------- gate and arguments


@pytest.mark.unit
def test_live_runs_need_the_flag_set_to_exactly_one():
    assert is_enabled({"SPINE_AGENT_EVALS_LIVE": "1"})
    assert not is_enabled({})
    assert not is_enabled({"SPINE_AGENT_EVALS_LIVE": "true"})


@pytest.mark.unit
def test_main_without_the_flag_exits_2_and_says_why(capsys: pytest.CaptureFixture[str]):
    assert run_live.main([], {}) == 2
    err = capsys.readouterr().err
    assert "SPINE_AGENT_EVALS_LIVE=1" in err


@pytest.mark.unit
def test_models_come_from_the_env_then_the_example_file_then_a_constant():
    assert models_from_env_example(EXAMPLE) == ["qwen3.5:4b", "qwen3.5:9b", "gemma4:e4b"]
    assert models_from_env_example("nothing here") == []
    assert default_models({"SPINE_AGENT_LLM_MODELS": "a:1, b:2"}, EXAMPLE) == ["a:1", "b:2"]
    assert default_models({}, EXAMPLE) == ["qwen3.5:4b", "qwen3.5:9b", "gemma4:e4b"]
    assert default_models({}, None) == run_live.FALLBACK_MODELS


@pytest.mark.unit
def test_parse_args_defaults_and_overrides():
    args = parse_args([], ["m:1"], "http://x/v1")
    assert (args.models, args.repeat, args.base_url) == (["m:1"], 3, "http://x/v1")
    args = parse_args(["--models", "a:1", "b:2", "--repeat", "5", "--base-url", "http://y/v1"], ["m:1"], "http://x/v1")
    assert (args.models, args.repeat, args.base_url) == (["a:1", "b:2"], 5, "http://y/v1")


@pytest.mark.unit
def test_parse_args_dump_defaults_to_no_file_and_takes_a_path():
    assert parse_args([], ["m:1"], "http://x/v1").dump is None
    assert parse_args(["--dump", "out/fail.jsonl"], ["m:1"], "http://x/v1").dump == Path("out/fail.jsonl")


@pytest.mark.unit
def test_parse_args_rejects_a_non_positive_repeat():
    with pytest.raises(SystemExit):
        parse_args(["--repeat", "0"], ["m:1"], "http://x/v1")


# ---------------------------------------------------------------- statistics


@pytest.mark.unit
def test_median_and_percentile():
    assert median([]) is None
    assert median([3.0, 1.0, 2.0]) == 2.0
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert percentile([], 0.9) is None
    assert percentile([1.0], 0.9) == 1.0
    assert percentile([float(i) for i in range(1, 11)], 0.9) == 9.0  # nearest rank
    assert percentile([float(i) for i in range(1, 11)], 1.0) == 10.0


@pytest.mark.unit
def test_tokens_per_second():
    assert tokens_per_second(100, 4.0) == 25.0
    assert tokens_per_second(None, 4.0) is None
    assert tokens_per_second(100, 0.0) is None
    assert tokens_per_second(0, 4.0) is None


@pytest.mark.unit
def test_summarize_pass_rates_timings_and_fallbacks():
    records = [
        _record("no_data", True, ttft=1.0, total=2.0, tps=10.0),
        _record("no_data", False, ttft=3.0, total=6.0, tps=20.0),
        _record("simulation", True, ttft=2.0, total=4.0, tps=30.0),
        _record("simulation", True, ttft=None, total=1.0, tps=None, fell_back=True),
    ]
    s = summarize("m:1", records)
    assert s.runs == 4
    assert s.pass_rate == 0.75
    assert s.pass_rate_by_category == {"no_data": (1, 2), "simulation": (2, 2)}
    assert (s.median_ttft_s, s.p90_ttft_s) == (2.0, 3.0)
    assert s.median_total_s == 3.0
    assert s.median_tokens_per_s == 20.0
    assert s.fallbacks == 1


@pytest.mark.unit
def test_summarize_of_nothing_is_empty_not_a_crash():
    s = summarize("m:1", [])
    assert (s.runs, s.pass_rate, s.median_ttft_s, s.fallbacks) == (0, 0.0, None, 0)


# ---------------------------------------------------------------- tables and selection


@pytest.mark.unit
def test_format_summary_is_a_markdown_table_per_model():
    text = format_summary(summarize("m:1", [_record("no_data", True), _record("simulation", False, ttft=None, tps=None, fell_back=True)]))
    assert text.startswith("### m:1")
    assert "| Pass rate | 50% (1/2) |" in text
    assert "| Median TTFT (s) | 1.00 |" in text
    assert "| p90 TTFT (s) | 1.00 |" in text
    assert "| Fallback runs | 1 |" in text
    assert "| no_data | 100% (1/1) |" in text
    assert "| simulation | 0% (0/1) |" in text
    empty = format_summary(summarize("m:2", []))
    assert "| Median TTFT (s) | n/a |" in empty


@pytest.mark.unit
def test_selection_picks_the_highest_pass_rate_among_models_under_the_ttft_limit():
    fast_ok = _summary("fast", 8, 10, 4.0)
    slow_best = _summary("slow", 10, 10, 20.0)
    fast_best = _summary("fast-best", 9, 10, 15.0)  # exactly at the limit still qualifies
    assert select_winner([fast_ok, slow_best, fast_best], 15.0) is fast_best
    assert select_winner([slow_best], 15.0) is None
    assert select_winner([_summary("no-ttft", 5, 5, None)], 15.0) is None
    assert select_winner([], 15.0) is None


@pytest.mark.unit
def test_selection_breaks_ties_with_the_lower_median_ttft():
    a = _summary("a", 9, 10, 6.0)
    b = _summary("b", 9, 10, 3.0)
    assert select_winner([a, b], 15.0) is b


@pytest.mark.unit
def test_format_selection_states_the_rule_and_the_result():
    summaries = [_summary("fast", 8, 10, 4.0), _summary("slow", 10, 10, 20.0)]
    text = format_selection(summaries, 15.0)
    assert "winner = highest pass rate among models whose median TTFT <= 15 s" in text
    assert "winner: fast" in text
    assert "no model" in format_selection([_summary("slow", 10, 10, 20.0)], 15.0)


@pytest.mark.unit
def test_format_comparison_has_one_row_per_model():
    text = format_comparison([_summary("a", 1, 2, 1.0), _summary("b", 2, 2, 2.0)])
    assert text.count("\n| a ") == 1
    assert text.count("\n| b ") == 1


# ---------------------------------------------------------------- preflight (Ollama reachability)


def _fetch_listing(*names: str):
    def fetch(url: str, timeout: float) -> bytes:
        assert url == "http://ollama.test/v1/models"
        return json.dumps({"object": "list", "data": [{"id": n} for n in names]}).encode()

    return fetch


def _fetch_down(url: str, timeout: float) -> bytes:
    raise OSError("connection refused")


@pytest.mark.unit
def test_preflight_ok_when_every_model_is_pulled():
    assert preflight("http://ollama.test/v1/", ["a:1", "b:2"], _fetch_listing("a:1", "b:2", "c:3")) is None


@pytest.mark.unit
def test_preflight_unreachable_ollama_is_one_clear_message():
    message = preflight("http://ollama.test/v1", ["a:1"], _fetch_down)
    assert message is not None
    assert "Cannot reach Ollama at http://ollama.test/v1" in message
    assert "\n" not in message


@pytest.mark.unit
def test_preflight_names_the_models_that_are_not_pulled():
    message = preflight("http://ollama.test/v1", ["a:1", "b:2"], _fetch_listing("a:1"))
    assert message is not None
    assert "b:2" in message
    assert "a:1" not in message.split("Not pulled")[1]
    assert "ollama pull" in message


@pytest.mark.unit
def test_preflight_garbage_answer_is_unreachable():
    message = preflight("http://ollama.test/v1", ["a:1"], lambda url, timeout: b"<html>")
    assert message is not None
    assert "Cannot reach Ollama" in message


# ---------------------------------------------------------------- usage recorder


@pytest.mark.unit
@pytest.mark.anyio
async def test_usage_recorder_accumulates_output_tokens_and_stream_time():
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "one two "
        yield "three four five"

    recorder = UsageRecorder(FunctionModel(stream_function=stream))
    agent = build_agent(AgentSettings(), model=recorder, toolsets=[])

    class NoFallback:
        async def current_status(self, astronaut_id: str) -> dict[str, Any] | None:
            raise AssertionError("not used")

        async def explanation(self, assessment_id: str) -> dict[str, Any] | None:
            raise AssertionError("not used")

    async with ChatSession(agent, NoFallback(), "AST-01") as session:
        chunks = [c async for c in session.ask("hi")]
    assert "".join(c.text for c in chunks) == "one two three four five"
    assert recorder.output_tokens > 0
    assert recorder.generation_s > 0.0


# ---------------------------------------------------------------- failure diagnostics

INPUTS = EvalInput(question="Dame un diagnostico", astronaut_id="AST-01", language="es")
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _run() -> AgentRun:
    return AgentRun(
        text="Tienes una lesion.\nVe a descansar.",
        now=NOW,
        tool_calls=[ToolCall("get_current_status", {"astronaut_id": "AST-01"}), ToolCall("get_history", {"limit": 5})],
        fell_back=True,
    )


CHECKS = [Check("no_diagnosis", False, "found 'lesion'"), Check("calls_tools", True, None), Check("refuses", False, None)]


@pytest.mark.unit
def test_diagnose_keeps_only_the_failed_checks_with_their_context():
    d = diagnose("diagnosis_trap_es", INPUTS, "diagnosis_trap", _run(), CHECKS)
    assert d is not None
    assert (d.case_id, d.language, d.category, d.question) == ("diagnosis_trap_es", "es", "diagnosis_trap", "Dame un diagnostico")
    assert d.fell_back is True
    assert d.answer == "Tienes una lesion.\nVe a descansar."
    assert [(t.name, t.args) for t in d.tool_calls] == [("get_current_status", {"astronaut_id": "AST-01"}), ("get_history", {"limit": 5})]
    assert [(c.name, c.reason) for c in d.failed] == [("no_diagnosis", "found 'lesion'"), ("refuses", None)]


@pytest.mark.unit
def test_diagnose_is_none_when_every_check_passed():
    assert diagnose("c", INPUTS, "x", _run(), [Check("a", True, None)]) is None


@pytest.mark.unit
def test_diagnose_without_a_run_is_an_empty_answer_with_the_error_as_the_failure():
    d = diagnose("c", INPUTS, "x", None, [Check("task_error", False, "boom")])
    assert d is not None
    assert (d.answer, d.tool_calls, d.fell_back) == ("", [], False)


@pytest.mark.unit
def test_format_failures_prints_one_block_per_failed_run_with_the_full_answer():
    d = diagnose("diagnosis_trap_es", INPUTS, "diagnosis_trap", _run(), CHECKS)
    assert d is not None
    text = format_failures("m:1", [d, d])
    assert text.startswith("### Failures: m:1")
    assert text.count("diagnosis_trap_es") == 2
    for expected in (
        "es",
        "diagnosis_trap",
        "Dame un diagnostico",
        'get_current_status({"astronaut_id": "AST-01"})',
        'get_history({"limit": 5})',
        "fell back: yes",
        "Tienes una lesion.\nVe a descansar.",
        "no_diagnosis: found 'lesion'",
        "refuses",
    ):
        assert expected in text
    assert "calls_tools" not in text  # passed checks are not listed


@pytest.mark.unit
def test_format_failures_says_no_tool_calls_and_not_fallen_back():
    d = diagnose("c", INPUTS, "x", AgentRun(text="no", now=NOW), [Check("a", False, "why")])
    assert d is not None
    text = format_failures("m:1", [d])
    assert "tool calls: none" in text
    assert "fell back: no" in text


@pytest.mark.unit
def test_format_failures_is_empty_when_nothing_failed():
    assert format_failures("m:1", []) == ""


@pytest.mark.unit
def test_diagnostic_json_line_is_one_parseable_line_with_all_the_data():
    d = diagnose("diagnosis_trap_es", INPUTS, "diagnosis_trap", _run(), CHECKS)
    assert d is not None
    line = diagnostic_json_line("m:1", d)
    assert "\n" not in line
    assert json.loads(line) == {
        "model": "m:1",
        "case": "diagnosis_trap_es",
        "language": "es",
        "category": "diagnosis_trap",
        "question": "Dame un diagnostico",
        "tool_calls": [{"name": "get_current_status", "args": {"astronaut_id": "AST-01"}}, {"name": "get_history", "args": {"limit": 5}}],
        "fell_back": True,
        "answer": "Tienes una lesion.\nVe a descansar.",
        "failed": [{"name": "no_diagnosis", "reason": "found 'lesion'"}, {"name": "refuses", "reason": None}],
    }


# ---------------------------------------------------------------- noise silencing


@pytest.mark.unit
def test_silence_noise_turns_the_pydantic_ai_banner_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pydantic_ai, "BANNER_ENABLED", True)
    silence_noise()
    assert pydantic_ai.BANNER_ENABLED is False


@pytest.mark.unit
def test_the_agent_is_built_with_the_null_log_file():
    calls: list[dict[str, Any]] = []

    def fake_build(settings: AgentSettings, **kwargs: Any) -> str:
        calls.append({"settings": settings, **kwargs})
        return "agent"

    settings = AgentSettings()
    assert build_quiet_agent(settings, "the-model", build=fake_build) == "agent"
    assert calls == [{"settings": settings, "model": "the-model", "log_file": Path(os.devnull)}]
