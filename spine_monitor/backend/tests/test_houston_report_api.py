"""GET /ui/crew/{id}/houston-report.pdf: the stateful wiring of the weekly report."""

from io import BytesIO

import pytest
from pypdf import PdfReader

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

AST = "AST-01"
PATH = f"/ui/crew/{AST}/houston-report.pdf"


async def test_report_is_a_pdf_attachment_of_the_week_ending_today(harness):
    harness.store.put_self_report(harness.example("domain/self-report.daily") | {"timestamp": "2026-11-12T08:00:00Z"})

    response = await harness.http.get(PATH)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == f'attachment; filename="houston-report-{AST}-2026-11-07_2026-11-13.pdf"'
    assert response.content.startswith(b"%PDF")


async def test_report_ends_on_the_given_day_even_without_any_data(harness):
    response = await harness.http.get(PATH, params={"end": "2026-10-31"})

    assert response.status_code == 200
    assert 'filename="houston-report-AST-01-2026-10-25_2026-10-31.pdf"' in response.headers["content-disposition"]


@pytest.mark.parametrize("params", [{"end": "not-a-date"}, {"end": "9999-12-31"}])
async def test_report_refuses_a_bad_end(harness, params):
    response = await harness.http.get(PATH, params=params)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


# --- what goes into the week ---------------------------------------------------------------------------------


def text_of(response) -> str:
    return " ".join(" ".join(page.extract_text().split()) for page in PdfReader(BytesIO(response.content)).pages)


def put_daily(harness, n: int, when: str, astronaut: str = AST) -> None:
    report = harness.example("domain/self-report.daily") | {
        "report_id": f"5e1f0e90-0000-4000-8000-0000000002{n:02d}",
        "timestamp": when,
        "astronaut_id": astronaut,
    }
    harness.store.put_self_report(report)


async def test_the_week_takes_whole_utc_days_of_this_astronaut_only(harness):
    put_daily(harness, 1, "2026-11-06T23:59:59Z")  # the day before the first day
    put_daily(harness, 2, "2026-11-07T00:00:00Z")  # first instant of the week
    put_daily(harness, 3, "2026-11-13T23:59:59Z")  # last instant of the last day
    put_daily(harness, 4, "2026-11-10T08:00:00Z", astronaut="AST-02")

    text = text_of(await harness.http.get(PATH))

    assert "2026-11-07 to 2026-11-13" in text
    assert "Pain was reported on 2 of 7 days" in text


async def test_an_end_in_the_past_moves_the_window_and_today_is_allowed(harness):
    put_daily(harness, 1, "2026-11-05T08:00:00Z")
    past = await harness.http.get(PATH, params={"end": "2026-11-10"})
    today = await harness.http.get(PATH, params={"end": "2026-11-13"})

    assert "2026-11-04 to 2026-11-10" in text_of(past)
    assert "Pain was reported on 1 of 7 days" in text_of(past)
    assert today.status_code == 200 and "Pain was reported" not in text_of(today)


async def test_an_end_in_the_future_is_an_invalid_range_problem(harness):
    response = await harness.http.get(PATH, params={"end": "2026-11-14"})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "invalid_range"
    assert "2026-11-14" in body["detail"] and "future" in body["detail"]


async def test_the_astronaut_id_must_be_a_pseudonym(harness):
    response = await harness.http.get("/ui/crew/not-an-id/houston-report.pdf")

    assert response.status_code == 422
    assert response.json()["type"] == "invalid_request"


async def test_mission_days_come_from_the_launch_date_and_are_left_out_without_one(harness):
    without = text_of(await harness.http.get(PATH))
    profile = harness.example("domain/astronaut-profile") | {"launch_date": "2026-11-03"}
    assert (await harness.http.put(f"/ui/crew/{AST}/profile", json=profile)).status_code == 200

    with_launch = text_of(await harness.http.get(PATH))

    assert "Mission day" not in without
    assert "Mission day D5 to D11" in with_launch


async def test_assessments_sessions_and_their_measures_of_the_week_are_in_the_report(harness):
    harness.store.put_assessment(harness.example("domain/assessment.amber") | {"as_of": "2026-11-09T09:00:00Z"})
    harness.store.put_assessment(
        harness.example("domain/assessment.amber") | {"assessment_id": "a55e55e0-0000-4000-8000-0000000009b2", "as_of": "2026-11-06T09:00:00Z", "level": "red"}
    )
    starts = {"in-slop-before": "2026-11-07T00:30:00Z", "in-slop-after": "2026-11-13T09:00:00Z", "outside-slop": "2026-11-08T09:00:00Z"}
    anchors = {"in-slop-before": "2026-11-06T12:00:00Z", "in-slop-after": "2026-11-14T08:00:00Z", "outside-slop": "2026-11-03T09:00:00Z"}
    for n, (name, started) in enumerate(starts.items(), start=1):
        session = harness.example("domain/session.exercise") | {"session_id": f"3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a0{n}", "started_at": started}
        harness.store.insert_session(session, "processed")
        feature_set = harness.example("domain/feature-set.inflight") | {"session_id": session["session_id"]}
        for feature in feature_set["features"]:
            if feature["feature_id"] == "exercise.rep_count":
                feature["value"] = 10 + n
        harness.store.put_feature_set(feature_set, phase="inflight", anchor_at=anchors[name])

    text = text_of(await harness.http.get(PATH))

    assert "The latest engine assessment (2026-11-09 09:00 UTC) is AMBER" in text
    assert "It was" not in text  # the red one of the 6th is outside the week
    assert "3 session(s)" in text
    assert "11 / 60" in text and "12 / 60" in text and "13 / 60" not in text
