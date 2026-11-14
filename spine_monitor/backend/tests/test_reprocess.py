"""POST /ui/sessions/{id}/reprocess: the operator asks again for the analysis of a `processing_failed` session."""

import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def failed_session(harness) -> str:
    """A session whose analysis failed for a final reason (no chunks), with chunks that arrived afterwards."""
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.stop_session(session_id, start)
    assert harness.store.session(session_id).state == "processing_failed"
    harness.store.add_chunk(harness.example("telemetry/stream-chunk.emg") | {"session_id": session_id})
    return session_id


async def test_unknown_session_is_404(harness):
    response = await harness.http.post("/ui/sessions/00000000-0000-4000-8000-000000000000/reprocess")

    assert response.status_code == 404
    assert response.json()["type"].endswith("not_found")


@pytest.mark.parametrize("state", ["running", "ended", "processing", "processed"])
async def test_session_not_failed_is_409_and_untouched(harness, state):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()
    harness.store.transition_session(session_id, ("running",), state)

    response = await harness.http.post(f"/ui/sessions/{session_id}/reprocess")

    assert response.status_code == 409
    assert harness.store.session(session_id).state == state


async def test_reprocess_answers_202_and_ends_processed_with_attempts_reset(harness):
    session_id = await failed_session(harness)
    harness.store.record_processing_failure(session_id, "engine unreachable", harness.clock())

    response = await harness.http.post(f"/ui/sessions/{session_id}/reprocess")
    await harness.settle()

    assert response.status_code == 202
    assert response.json() == {"session": session_id, "state": "ended"}
    assert harness.store.session(session_id).state == "processed"
    assert harness.store.processing_attempt(session_id) is None


async def test_second_reprocess_after_success_is_409(harness):
    session_id = await failed_session(harness)
    await harness.http.post(f"/ui/sessions/{session_id}/reprocess")
    await harness.settle()

    again = await harness.http.post(f"/ui/sessions/{session_id}/reprocess")

    assert again.status_code == 409
    assert len(harness.extractor.requests) == 1


async def test_reprocess_that_fails_again_is_logged_not_lost(harness):
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.stop_session(session_id, start)  # still no chunks: it fails again

    response = await harness.http.post(f"/ui/sessions/{session_id}/reprocess")
    await harness.settle()

    assert response.status_code == 202
    row = harness.store.session(session_id)
    assert row.state == "processing_failed"
    assert "no chunks" in row.error
