"""A stored profile or daily context asks for a debounced reassessment; a refused one does not."""

from datetime import UTC, datetime

import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
def harness_settings():
    # Long enough that two awaited requests in a row land inside one debounce window.
    return {"reassess_debounce_s": 0.3}


def assessments(harness) -> list[dict]:
    return harness.store.assessments_between(harness.astronaut_id, datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))


async def assessed_astronaut(harness) -> int:
    """An astronaut with profile, baseline and features; returns how many assessments exist."""
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.send_chunks(session_id)
    await harness.stop_session(session_id, start)
    await harness.settle()
    count = len(assessments(harness))
    assert count >= 1
    return count


def context(harness, **overrides) -> tuple[str, dict]:
    day = harness.clock().date().isoformat()
    return f"/ui/crew/{harness.astronaut_id}/daily-context/{day}", {
        "mission_day": -30,
        "phase": "preflight",
        "gravity_environment": "earth_1g",
        **overrides,
    }


async def test_daily_context_put_reassesses(harness):
    before = await assessed_astronaut(harness)
    url, body = context(harness, exercise=[{"modality": "ared", "duration_min": 30}])

    assert (await harness.http.put(url, json=body)).status_code == 200
    await harness.settle()

    assert len(assessments(harness)) == before + 1


async def test_profile_put_reassesses(harness):
    before = await assessed_astronaut(harness)

    profile = harness.example("domain/astronaut-profile")
    assert (await harness.http.put(f"/ui/crew/{harness.astronaut_id}/profile", json=profile)).status_code == 200
    await harness.settle()

    assert len(assessments(harness)) == before + 1


async def test_refused_puts_do_not_reassess(harness):
    before = await assessed_astronaut(harness)
    url, body = context(harness, phase="not-a-phase")

    assert (await harness.http.put(url, json=body)).status_code >= 400
    assert (await harness.http.put(f"/ui/crew/{harness.astronaut_id}/profile", json={"astronaut_id": "AST-99"})).status_code >= 400
    await harness.settle()

    assert len(assessments(harness)) == before


async def test_conditional_put_over_a_stored_context_does_not_reassess(harness):
    before = await assessed_astronaut(harness)  # its setup stored today's context
    url, body = context(harness)

    assert (await harness.http.put(url, json=body, headers={"If-None-Match": "*"})).status_code == 412
    await harness.settle()

    assert len(assessments(harness)) == before


async def test_profile_and_context_in_a_burst_make_one_assessment(harness):
    before = await assessed_astronaut(harness)
    url, body = context(harness, exercise=[{"modality": "ared", "duration_min": 45}])

    profile = harness.example("domain/astronaut-profile")
    assert (await harness.http.put(f"/ui/crew/{harness.astronaut_id}/profile", json=profile)).status_code == 200
    assert (await harness.http.put(url, json=body)).status_code == 200
    await harness.settle()

    assert len(assessments(harness)) == before + 1
