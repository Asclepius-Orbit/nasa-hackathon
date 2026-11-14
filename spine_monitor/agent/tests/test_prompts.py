from datetime import UTC, datetime, timedelta, timezone

import pytest
from spine_agent.prompts import SYSTEM_PROMPT, TimeRange, astronaut_instruction, relative_ranges


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
def test_system_prompt_forbids_computing_or_converting_numbers():
    text = SYSTEM_PROMPT.lower()
    for rule in ("never compute", "differences", "sums", "averages", "percentages", "unit conversions", "0.74", "74%", "compare_periods already"):
        assert rule in text


@pytest.mark.unit
def test_system_prompt_tells_the_model_to_use_the_ready_made_ranges():
    assert "ready-made" in SYSTEM_PROMPT.lower()


@pytest.mark.unit
def test_relative_ranges_for_a_fixed_now():
    ranges = relative_ranges(NOW)
    assert ranges.last_24_hours == TimeRange("2026-11-12T12:00:00Z", "2026-11-13T12:00:00Z")
    assert ranges.last_7_days == TimeRange("2026-11-06T12:00:00Z", "2026-11-13T12:00:00Z")
    assert ranges.previous_7_days == TimeRange("2026-10-30T12:00:00Z", "2026-11-06T12:00:00Z")


@pytest.mark.unit
def test_relative_ranges_roll_over_months_and_years_and_keep_minutes_and_seconds():
    ranges = relative_ranges(datetime(2027, 1, 3, 0, 5, 9, tzinfo=UTC))
    assert ranges.last_24_hours == TimeRange("2027-01-02T00:05:09Z", "2027-01-03T00:05:09Z")
    assert ranges.last_7_days == TimeRange("2026-12-27T00:05:09Z", "2027-01-03T00:05:09Z")
    assert ranges.previous_7_days == TimeRange("2026-12-20T00:05:09Z", "2026-12-27T00:05:09Z")


@pytest.mark.unit
def test_relative_ranges_normalise_to_utc_and_drop_microseconds():
    local = datetime(2026, 11, 13, 14, 30, 5, 999999, tzinfo=timezone(timedelta(hours=2)))
    ranges = relative_ranges(local)
    assert ranges.last_7_days == TimeRange("2026-11-06T12:30:05Z", "2026-11-13T12:30:05Z")


@pytest.mark.unit
def test_astronaut_instruction_names_the_astronaut_and_the_current_time():
    text = astronaut_instruction("AST-07", NOW)
    assert text == (
        "The current astronaut is AST-07. Use it as astronaut_id in tool calls unless the user names another. "
        "Current date and time (UTC): 2026-11-13T12:00:00Z. "
        "Ready-made UTC ranges (copy them exactly, never compute dates yourself): "
        "last 24 hours: start=2026-11-12T12:00:00Z, end=2026-11-13T12:00:00Z; "
        "last 7 days: start=2026-11-06T12:00:00Z, end=2026-11-13T12:00:00Z; "
        "previous 7 days: start=2026-10-30T12:00:00Z, end=2026-11-06T12:00:00Z. "
        "'This week', 'last week' and 'recently' mean the last 7 days. "
        "To compare this week with the previous one call compare_periods with a=last 7 days and b=previous 7 days."
    )


@pytest.mark.unit
def test_astronaut_instruction_converts_to_utc_and_drops_microseconds():
    local = datetime(2026, 11, 13, 14, 30, 5, 999999, tzinfo=timezone(timedelta(hours=2)))
    text = astronaut_instruction("AST-01", local)
    assert "2026-11-13T12:30:05Z" in text
    assert "last 24 hours: start=2026-11-12T12:30:05Z" in text

@pytest.mark.unit
def test_prompt_requires_retrieval_and_citation_or_insufficient_evidence():
    assert 'search_evidence' in SYSTEM_PROMPT
    assert 'insufficient evidence' in SYSTEM_PROMPT
    assert 'locator' in SYSTEM_PROMPT
