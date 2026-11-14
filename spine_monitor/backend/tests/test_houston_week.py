"""Weekly Houston report: the week data (pure) and the PDF rendered from it."""

import json
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from monitor.db import SessionRow
from monitor.report.houston_pdf import render_pdf
from monitor.report.week import build_week_report
from pypdf import PdfReader

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
END = date(2027, 2, 10)
START = date(2027, 2, 4)
NOW = datetime(2027, 2, 10, 18, 0, tzinfo=UTC)
NOTE = "SECRET-FREE-TEXT-NOTE"


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def daily(n: int, day: str, pain: int, worst: int | None = None, **extra: Any) -> dict[str, Any]:
    report = (
        example("domain/self-report.daily")
        | {"report_id": f"5e1f0e90-0000-4000-8000-0000000001{n:02d}", "timestamp": f"{day}T08:00:00Z", "pain_nrs": pain}
        | extra
    )
    if worst is not None:
        report["pain_nrs_worst_24h"] = worst
    return report


def session(n: int, started: str, state: str = "processed", end_reason: str = "completed", pain_rep: int | None = None) -> SessionRow:
    payload = example("domain/session.exercise") | {"session_id": f"3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a{n:02d}", "started_at": started, "end_reason": end_reason}
    if pain_rep is not None:
        payload["events"] = [*payload["events"], {"event_type": "pain_reported", "timestamp": started, "device_ms": 1, "boot_id": 1, "rep_index": pain_rep}]
    return SessionRow(payload, state, None)


def features(row: SessionRow, reps: float, elongation: float) -> dict[str, Any]:
    fs = example("domain/feature-set.inflight") | {"session_id": row.payload["session_id"]}
    for f in fs["features"]:
        if f["feature_id"] == "exercise.rep_count":
            f["value"] = reps
        if f["feature_id"] == "stretch.full_spine.peak_elongation_mm":
            f["value"] = elongation
    return fs


def assessment(n: int, as_of: str, level: str) -> dict[str, Any]:
    return example("domain/assessment.amber") | {"assessment_id": f"a55e55e0-0000-4000-8000-0000000001{n:02d}", "as_of": f"{as_of}T09:00:00Z", "level": level}


def week(**overrides: Any) -> dict[str, Any]:
    odi_old = example("domain/self-report.weekly-odi") | {"timestamp": "2027-01-20T08:00:00Z"}
    s1, s2, old = (
        session(1, "2027-02-05T10:00:00Z"),
        session(2, "2027-02-07T10:00:00Z", "processed", "aborted_pain", pain_rep=7),
        session(3, "2027-01-20T10:00:00Z"),
    )
    args: dict[str, Any] = {
        "assessments": [
            assessment(1, "2027-02-05", "green"),
            assessment(2, "2027-02-05", "amber"),
            assessment(3, "2027-02-09", "red"),
            assessment(4, "2027-01-15", "red"),
        ],
        "self_reports": [
            daily(1, "2027-02-05", 3, 4),
            daily(2, "2027-02-06", 5, 6, red_flags=["leg_weakness"]),
            daily(3, "2027-01-30", 9, 9),
            example("domain/self-report.urgent-other") | {"timestamp": "2027-02-10T14:15:00Z", "note": NOTE},
            example("domain/self-report.weekly-odi"),
            odi_old,
        ],
        "sessions": [s1, s2, old],
        "feature_sets": [features(s1, 12, 8.0), features(s2, 6, 9.5), features(old, 99, 1.0)],
    }
    args |= overrides
    return build_week_report("AST-01", START, END, NOW, lambda d: (d - date(2026, 11, 14)).days + 1, **args)


# --- week data ---------------------------------------------------------------------------------------------


def test_week_covers_seven_utc_days_with_the_latest_level_of_each_and_nodata_elsewhere():
    report = week()

    assert [d["date"] for d in report["days"]] == [f"2027-02-{n:02d}" for n in range(4, 11)]
    levels = {d["date"]: d["level"] for d in report["days"]}
    assert levels["2027-02-05"] == "amber" and levels["2027-02-09"] == "red"
    assert levels["2027-02-04"] == "nodata"
    assert report["latest_assessment"]["level"] == "red"
    assert report["mission_day"] == {"start": 83, "end": 89}


def test_week_ignores_other_days_and_keeps_pain_reports_symptoms_and_the_week_odi():
    report = week()

    assert [(d["date"], d["pain_nrs"], d["pain_nrs_worst_24h"]) for d in report["days"] if d["pain_nrs"] is not None] == [
        ("2027-02-05", 3, 4),
        ("2027-02-06", 5, 6),
    ]
    assert report["symptoms"]["red_flags"] == ["leg_weakness"]
    assert report["symptoms"]["other_symptoms"] == ["very_severe_pain"]
    assert report["odi"]["date"] == "2027-02-07" and report["odi"]["items"][0] == {"item_id": "pain_intensity", "score": 2}
    assert report["odi"]["percent"] == pytest.approx(8 / 20 * 100)
    assert NOTE not in json.dumps(report)


def test_week_lists_only_this_weeks_sessions_with_their_metrics_and_pain_stop():
    report = week()

    sessions = report["sessions"]
    assert [(s["date"], s["end_reason"], s["pain_stop_rep"], s["reps"], s["elongation_mm"]) for s in sessions] == [
        ("2027-02-05", "completed", None, 12, 8.0),
        ("2027-02-07", "aborted_pain", 7, 6, 9.5),
    ]


def test_empty_week_is_explicit_and_never_green():
    report = week(assessments=[], self_reports=[], sessions=[], feature_sets=[])

    assert report["has_data"] is False
    assert {d["level"] for d in report["days"]} == {"nodata"}
    assert report["latest_assessment"] is None and report["odi"] is None and report["sessions"] == []


# --- pdf ---------------------------------------------------------------------------------------------------


def text_of(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)


def test_pdf_has_the_week_in_it_and_no_free_text():
    pdf = render_pdf(week())

    assert pdf.startswith(b"%PDF")
    text = text_of(pdf)
    assert "AST-01" in text and "2027-02-04" in text and "2027-02-10" in text
    assert "Leg weakness" in text and "REC_LUMBAR_STAB_01" in text
    assert NOTE not in text and NOTE.encode() not in pdf
    assert "2027-01-30" not in text


def test_empty_week_pdf_says_there_is_no_data():
    pdf = render_pdf(week(assessments=[], self_reports=[], sessions=[], feature_sets=[]))

    assert pdf.startswith(b"%PDF")
    assert "No data" in text_of(pdf)
