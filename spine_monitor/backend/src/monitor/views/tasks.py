"""Task codes of what the astronaut still has to do today."""

from collections.abc import Sequence
from typing import Literal

type TaskCode = Literal["TASK_DAILY_REPORT", "TASK_ODI", "TASK_BASELINE_SESSION", "TASK_SESSION", "TASK_CORD_STRAIGHT", "TASK_CORD_MAX_FLEXION"]

_CORD_TASKS: dict[str, TaskCode] = {"spine_resistance_straight_v1": "TASK_CORD_STRAIGHT", "spine_resistance_max_flexion_v1": "TASK_CORD_MAX_FLEXION"}


def tasks_for(daily_due: bool, weekly_odi_due: bool, baseline_pending: bool, session_today: bool, cord_due: Sequence[str] = ()) -> list[TaskCode]:
    """Pending tasks in a fixed order: daily report, ODI, baseline session, planned session (when none was done today), then the
    cord protocols that are due (back straight, maximum flexion)."""
    pending: list[tuple[bool, TaskCode]] = [
        (daily_due, "TASK_DAILY_REPORT"),
        (weekly_odi_due, "TASK_ODI"),
        (baseline_pending, "TASK_BASELINE_SESSION"),
        (not session_today, "TASK_SESSION"),
        *((protocol in cord_due, code) for protocol, code in _CORD_TASKS.items()),
    ]
    return [code for due, code in pending if due]
