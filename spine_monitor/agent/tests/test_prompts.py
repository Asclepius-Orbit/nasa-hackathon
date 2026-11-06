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
def test_astronaut_instruction_names_the_astronaut():
    assert astronaut_instruction("AST-07") == ("The current astronaut is AST-07. Use it as astronaut_id in tool calls unless the user names another.")
