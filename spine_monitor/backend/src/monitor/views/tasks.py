"""Task codes of what the astronaut still has to do today."""

from typing import Literal

type TaskCode = Literal["TASK_DAILY_REPORT", "TASK_ODI", "TASK_BASELINE_SESSION", "TASK_SESSION"]


def tasks_for(daily_due: bool, weekly_odi_due: bool, baseline_pending: bool, session_today: bool) -> list[TaskCode]:
    """Pending tasks in a fixed order: daily report, ODI, baseline session, planned session (when none was done today)."""
    pending: list[tuple[bool, TaskCode]] = [
        (daily_due, "TASK_DAILY_REPORT"),
        (weekly_odi_due, "TASK_ODI"),
        (baseline_pending, "TASK_BASELINE_SESSION"),
        (not session_today, "TASK_SESSION"),
    ]
    return [code for due, code in pending if due]
