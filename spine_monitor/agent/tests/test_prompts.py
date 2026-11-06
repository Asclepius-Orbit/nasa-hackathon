from datetime import UTC, datetime, timedelta, timezone

import pytest
from spine_agent.prompts import SYSTEM_PROMPT, astronaut_instruction


@pytest.mark.unit
@pytest.mark.parametrize(
    "rule",
    [
        "only from tool results",
        "call a tool",
        "never invent or round numbers",
        "not available",
        "I don't know",
        "never diagnose",
        "flight surgeon",
        "hypothetical",
        "read-only",
        "without calling any tool",
        "same language as the user's last message",
        "concise",
    ],
)
def test_system_prompt_states_each_rule(rule: str):
    assert rule.lower() in SYSTEM_PROMPT.lower()


@pytest.mark.unit
def test_system_prompt_forbids_treating_null_as_zero_and_treatment_advice():
    text = SYSTEM_PROMPT.lower()
    assert "never 0" in text
    assert "treatment" in text
    assert "medication" in text


@pytest.mark.unit
def test_system_prompt_is_static_and_has_no_astronaut_id():
    assert "AST-" not in SYSTEM_PROMPT
    assert "{" not in SYSTEM_PROMPT


@pytest.mark.unit
@pytest.mark.parametrize(
    "rule",
    [
        "before stating any fact",
        "in the same turn",
        "even if the answer seems to be in the conversation",
        "did not call a tool",
        "must not state data or numbers",
        "empty overrides list",
        "feature_id, mode, value",
        "get_history",
        "compare_periods",
        "absolute",
        "current date",
    ],
)
def test_system_prompt_has_the_tool_first_rules(rule: str):
    assert rule.lower() in SYSTEM_PROMPT.lower()


@pytest.mark.unit
def test_system_prompt_stays_compact_because_it_runs_on_cpu():
    assert len(SYSTEM_PROMPT) < 2600


NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@pytest.mark.unit
def test_astronaut_instruction_names_the_astronaut_and_the_current_time():
    text = astronaut_instruction("AST-07", NOW)
    assert text == (
        "The current astronaut is AST-07. Use it as astronaut_id in tool calls unless the user names another. "
        "Current date and time (UTC): 2026-11-13T12:00:00Z. Use it to turn relative ranges such as 'today', "
        "'this week' or 'the last 7 days' into absolute RFC 3339 timestamps (start, end) for the tools; never guess dates."
    )


@pytest.mark.unit
def test_astronaut_instruction_converts_to_utc_and_drops_microseconds():
    local = datetime(2026, 11, 13, 14, 30, 5, 999999, tzinfo=timezone(timedelta(hours=2)))
    assert "2026-11-13T12:30:05Z" in astronaut_instruction("AST-01", local)
