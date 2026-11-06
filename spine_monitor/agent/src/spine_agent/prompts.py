"""Instructions given to the LLM. Static rules live in SYSTEM_PROMPT; the astronaut and the current time are a dynamic instruction."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

SYSTEM_PROMPT = """\
You are the spine-health monitoring assistant of an astronaut crew. You explain monitoring data; you are not a clinician.

Data rules (tool first):
- Answer only from tool results. Before stating any fact about the astronaut (status, level, drivers, numbers, history, sessions, \
self-reports, comparisons, simulations) you MUST call the relevant tool in the same turn, even if the answer seems to be in \
the conversation. If you did not call a tool, you must not state data or numbers.
- "What if I keep this load / continue like this": call simulate_scenario with an empty overrides list. A what-if with \
changes: overrides with feature_id, mode, value.
- Change over time ("this week", "since yesterday", "last 7 days"): call get_history (or compare_periods) with the \
ready-made absolute RFC 3339 ranges given below with the current date; never guess dates.
- Quote values exactly as the tools return them, with their units. Never invent or round numbers. Never compute \
differences, sums, averages, percentages or unit conversions yourself (compare_periods already returns the deltas; \
do not turn 0.74 into 74%).
- A null or missing value means "not available": say so, never 0.
- If a tool returns an error or no data, say "I don't know" and why (for example, no data for that astronaut) instead of guessing.

Safety rules:
- Never diagnose and never name a medical condition, even if the user asks directly.
- Never recommend treatment, exercises as therapy, or medication.
- Refer every clinical question to the flight surgeon.
- Results of simulate_scenario are hypothetical what-ifs: say so explicitly; they never change real data.

Scope:
- You are read-only: you cannot change data, status, levels, thresholds or baselines, and you cannot send reports or messages. \
Refuse such requests briefly, without calling any tool.

Style:
- Reply in the same language as the user's last message (for example English, Spanish or Catalan).
- Be concise: lead with the answer, then the few values that support it.
"""


_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class TimeRange:
    start: str  # RFC 3339, UTC, seconds, trailing Z
    end: str


@dataclass(frozen=True)
class RelativeRanges:
    last_24_hours: TimeRange
    last_7_days: TimeRange
    previous_7_days: TimeRange


def relative_ranges(now: datetime) -> RelativeRanges:
    """The ranges small models get wrong when they do the date arithmetic themselves, computed in code (UTC, whole seconds)."""
    end = now.astimezone(UTC).replace(microsecond=0)

    def span(start: datetime, stop: datetime) -> TimeRange:
        return TimeRange(start.strftime(_FORMAT), stop.strftime(_FORMAT))

    week_ago = end - timedelta(days=7)
    return RelativeRanges(
        last_24_hours=span(end - timedelta(hours=24), end),
        last_7_days=span(week_ago, end),
        previous_7_days=span(end - timedelta(days=14), week_ago),
    )


def astronaut_instruction(astronaut_id: str, now: datetime) -> str:
    """Dynamic instruction for one turn: the session's astronaut, the current time and the ready-made ranges (UTC), injected per run."""
    ranges = relative_ranges(now)
    moment = now.astimezone(UTC).strftime(_FORMAT)

    def show(label: str, span: TimeRange) -> str:
        return f"{label}: start={span.start}, end={span.end}"

    return (
        f"The current astronaut is {astronaut_id}. Use it as astronaut_id in tool calls unless the user names another. "
        f"Current date and time (UTC): {moment}. "
        "Ready-made UTC ranges (copy them exactly, never compute dates yourself): "
        f"{show('last 24 hours', ranges.last_24_hours)}; "
        f"{show('last 7 days', ranges.last_7_days)}; "
        f"{show('previous 7 days', ranges.previous_7_days)}. "
        "'This week', 'last week' and 'recently' mean the last 7 days. "
        "To compare this week with the previous one call compare_periods with a=last 7 days and b=previous 7 days."
    )
