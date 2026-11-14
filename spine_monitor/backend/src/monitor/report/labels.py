"""Readable labels for the codes of the weekly report. Unknown codes fall back to a readable form of the code itself."""

LEVEL_LABELS = {"green": "Green", "amber": "Amber", "red": "Red", "nodata": "No data"}
END_REASON_LABELS = {
    "completed": "Completed",
    "aborted_pain": "Stopped for pain",
    "aborted_by_user": "Stopped by the astronaut",
    "aborted_other": "Stopped (other reason)",
}
OTHER_SYMPTOM_LABELS = {"very_severe_pain": "Very severe pain", "other": "Other urgent symptom (not specified)"}
SESSION_STATE_LABELS = {
    "starting": "Starting",
    "running": "Running",
    "ended": "Ended",
    "processing": "Processing",
    "processed": "Analysed",
    "processing_failed": "Analysis failed",
    "rejected": "Rejected",
}


def humanize(code: str) -> str:
    """`leg_weakness` -> `Leg weakness`."""
    return code.replace("_", " ").capitalize()
