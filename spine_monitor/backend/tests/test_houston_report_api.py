"""GET /ui/crew/{id}/houston-report.pdf: the stateful wiring of the weekly report."""

import pytest

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
