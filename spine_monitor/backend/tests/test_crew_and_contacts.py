"""Crew registry, medical contacts, stops for pain and urgent symptoms outside the red-flag catalogue (#63, #65)."""

import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
def requested(harness, monkeypatch):
    """The astronauts whose (debounced) reassessment was requested; nothing is left waiting at teardown."""
    calls: list[str] = []
    monkeypatch.setattr(harness.runtime.analysis, "request_assess", calls.append)
    return calls


async def houston_summary(harness) -> dict:
    response = await harness.http.get(f"/ui/crew/{harness.astronaut_id}/houston")
    assert response.status_code == 200, response.text
    return response.json()["summary"]


async def test_crew_lists_registered_ids_and_what_is_stored_for_each(harness):
    assert (await harness.http.put("/ui/crew/AST-02")).status_code == 201
    assert (await harness.http.put("/ui/crew/AST-02")).status_code == 200
    await harness.setup_astronaut()

    crew = (await harness.http.get("/ui/crew")).json()["crew"]

    assert crew == [
        {"astronaut_id": "AST-01", "registered": False, "has_profile": True, "has_baseline": False, "sessions": 0},
        {"astronaut_id": "AST-02", "registered": True, "has_profile": False, "has_baseline": False, "sessions": 0},
    ]


async def test_registering_a_name_instead_of_a_pseudonym_is_refused(harness):
    assert (await harness.http.put("/ui/crew/Ana")).status_code == 422


async def test_a_contact_is_listed_and_reaches_the_ground_without_its_note(harness):
    body = {"contact": "crew_medical_officer", "text": "Called after the stop."}

    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/contacts", json=body)

    assert response.status_code == 201, response.text
    contact = response.json()
    listed = (await harness.http.get(f"/ui/crew/{harness.astronaut_id}/contacts")).json()["contacts"]
    assert listed == [contact]
    assert (await houston_summary(harness))["contacts"] == [{"contact_id": contact["contact_id"], "at": contact["at"], "contact": "crew_medical_officer"}]


async def test_a_contact_with_an_unknown_role_is_refused(harness):
    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/contacts", json={"contact": "neighbour"})

    assert response.status_code == 422


async def test_a_stop_for_pain_keeps_the_rep_reaches_the_ground_and_requests_an_assessment(harness, requested):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()
    requested.clear()  # the profile and the daily context ask for their own

    response = await harness.http.post(f"/ui/sessions/{session_id}/stop", json={"reason": "aborted_pain", "rep_index": 6})

    assert response.status_code == 202, response.text
    issued_at = response.json()["command"]["issued_at"]
    events = harness.store.session(session_id).payload["events"]
    assert {"event_type": "pain_reported", "timestamp": issued_at, "rep_index": 6} in events
    assert (await houston_summary(harness))["sessions"][0]["pain_stop"] == {"rep_index": 6, "at": issued_at}
    assert requested == [harness.astronaut_id]


async def test_a_rep_is_only_accepted_with_a_stop_for_pain(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()

    response = await harness.http.post(f"/ui/sessions/{session_id}/stop", json={"reason": "completed", "rep_index": 6})

    assert response.status_code == 422


async def test_an_urgent_symptom_outside_the_catalogue_reaches_the_ground(harness):
    report = {"kind": "red_flag", "red_flags": [], "other_symptoms": ["very_severe_pain"]}

    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json=report)

    assert response.status_code == 201, response.text
    assert (await houston_summary(harness))["other_symptoms"] == ["very_severe_pain"]
