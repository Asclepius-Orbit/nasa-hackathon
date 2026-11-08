"""tasks_for: the codes of what is still to do today, in a fixed order."""

import itertools

import pytest
from monitor.views.tasks import tasks_for


def test_all_pending_in_fixed_order() -> None:
    assert tasks_for(True, True, True, False) == ["TASK_DAILY_REPORT", "TASK_ODI", "TASK_BASELINE_SESSION", "TASK_SESSION"]


def test_nothing_pending() -> None:
    assert tasks_for(False, False, False, True) == []


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ((True, False, False, True), ["TASK_DAILY_REPORT"]),
        ((False, True, False, True), ["TASK_ODI"]),
        ((False, False, True, True), ["TASK_BASELINE_SESSION"]),
        ((False, False, False, False), ["TASK_SESSION"]),
    ],
)
def test_each_task_alone(flags: tuple[bool, bool, bool, bool], expected: list[str]) -> None:
    assert tasks_for(*flags) == expected


def test_every_combination_keeps_order_and_has_no_duplicates() -> None:
    order = ["TASK_DAILY_REPORT", "TASK_ODI", "TASK_BASELINE_SESSION", "TASK_SESSION"]
    for daily, odi, baseline, session_today in itertools.product([True, False], repeat=4):
        result = tasks_for(daily, odi, baseline, session_today)
        assert result == [code for code, on in zip(order, (daily, odi, baseline, not session_today), strict=True) if on]
