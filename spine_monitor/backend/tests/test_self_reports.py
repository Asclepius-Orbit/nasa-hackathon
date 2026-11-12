"""Tiered self-reports, profile and daily context, and what reaches the bus."""

import json

import pytest
from spine_contracts import validation

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

DAILY = {"kind": "daily", "pain_nrs": 2, "stiffness_nrs": 3, "red_flags": []}


async def assessed_astronaut(harness) -> None:
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.send_chunks(session_id)
    await harness.stop_session(session_id, start)


async def test_daily_report_is_completed_validated_and_assessed(harness):
    await assessed_astronaut(harness)
    before = harness.store.latest_assessment(harness.astronaut_id)

    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json=DAILY)

    assert response.status_code == 201, response.text
    body = response.json()
    report = body["report"]
    assert validation.errors("domain/self-report", report) == []
    assert (report["astronaut_id"], report["source"], report["mission_day"]) == (harness.astronaut_id, "device", -30)
    assert body["assessment_skipped"] is None
    assert body["assessment"]["assessment_id"] != before["assessment_id"]
    assert report["report_id"] in body["assessment"]["based_on"]["self_report_ids"]


async def test_red_flag_is_always_accepted_and_assessed(harness):
    await assessed_astronaut(harness)

    response = await harness.http.post(
        f"/ui/crew/{harness.astronaut_id}/self-reports", json={"kind": "red_flag", "red_flags": ["leg_weakness"]}
    )

    assert response.status_code == 201
    # The app's job is to assess right away with the report in the input; the escalation rule itself
    # belongs to the engine's triage policy.
    body = response.json()
    assert body["assessment"] is not None
    assert body["report"]["report_id"] in body["assessment"]["based_on"]["self_report_ids"]


async def test_report_without_history_is_stored_but_not_assessed(harness):
    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json=DAILY)

    assert response.status_code == 201
    assert response.json()["assessment"] is None
    assert response.json()["assessment_skipped"] == "no_profile"
    assert harness.store.latest_assessment(harness.astronaut_id) is None


async def test_invalid_report_is_refused(harness):
    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json={"kind": "daily", "pain_nrs": 11})

    assert response.status_code == 422
    assert response.json()["type"] == "invalid_request"
    assert validation.errors("app/error", response.json()) == []


async def test_retry_with_the_same_report_id_is_idempotent(harness):
    report = {**DAILY, "report_id": "5e1f0e90-0000-4000-8000-0000000000aa"}
    first = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json=report)
    harness.clock.advance(seconds=5)
    again = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json=report)
    other = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json={**report, "pain_nrs": 9})

    assert (first.status_code, again.status_code, other.status_code) == (201, 200, 409)
    assert again.json()["report"] == first.json()["report"]


async def test_what_is_due(harness):
    url = f"/ui/crew/{harness.astronaut_id}/self-reports/due"
    due = (await harness.http.get(url)).json()
    assert (due["daily_due"], due["weekly_odi_due"], due["red_flag_available"]) == (True, True, True)

    await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json=DAILY)
    odi = {"kind": "weekly_odi", "items": [{"item_id": "pain_intensity", "score": 1}, {"item_id": "lifting", "score": 2}]}
    assert (await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json=odi)).status_code == 201
    due = (await harness.http.get(url)).json()
    assert (due["daily_due"], due["weekly_odi_due"]) == (False, False)

    harness.clock.advance(days=7)
    due = (await harness.http.get(url)).json()
    assert (due["daily_due"], due["weekly_odi_due"]) == (True, True)


async def test_profile_and_daily_context_round_trip(harness):
    profile = harness.example("domain/astronaut-profile")
    assert (await harness.http.put(f"/ui/crew/{harness.astronaut_id}/profile", json=profile)).json() == profile
    assert (await harness.http.get(f"/ui/crew/{harness.astronaut_id}/profile")).json() == profile

    context = harness.example("domain/daily-context")
    url = f"/ui/crew/{harness.astronaut_id}/daily-context/{context['date']}"
    assert (await harness.http.put(url, json=context)).status_code == 200
    assert (await harness.http.get(url)).json() == context


async def test_conditional_daily_context_put_creates_a_missing_day(harness):
    context = harness.example("domain/daily-context")
    url = f"/ui/crew/{harness.astronaut_id}/daily-context/{context['date']}"

    response = await harness.http.put(url, json=context, headers={"If-None-Match": "*"})

    assert (response.status_code, response.json()) == (200, context)
    assert (await harness.http.get(url)).json() == context


async def test_conditional_daily_context_put_leaves_a_stored_day_untouched(harness):
    stored = harness.example("domain/daily-context")
    url = f"/ui/crew/{harness.astronaut_id}/daily-context/{stored['date']}"
    assert (await harness.http.put(url, json=stored)).status_code == 200
    automatic = {key: stored[key] for key in ("astronaut_id", "date", "phase", "gravity_environment")}

    response = await harness.http.put(url, json=automatic, headers={"If-None-Match": "*"})

    assert response.status_code == 412
    assert response.headers["content-type"] == "application/problem+json"
    assert validation.errors("app/error", response.json()) == []
    assert (await harness.http.get(url)).json() == stored


async def test_daily_context_put_only_accepts_the_star_as_if_none_match(harness):
    context = harness.example("domain/daily-context")
    url = f"/ui/crew/{harness.astronaut_id}/daily-context/{context['date']}"

    response = await harness.http.put(url, json=context, headers={"If-None-Match": '"v1"'})

    assert response.status_code == 422
    assert response.json()["errors"] == [{"pointer": "If-None-Match", "message": "only * is accepted"}]
    assert (await harness.http.get(url)).status_code == 404


async def test_path_and_body_must_agree(harness):
    profile = {**harness.example("domain/astronaut-profile"), "astronaut_id": "AST-02"}

    response = await harness.http.put(f"/ui/crew/{harness.astronaut_id}/profile", json=profile)

    assert response.status_code == 422


async def test_ids_must_be_pseudonyms(harness):
    response = await harness.http.post("/ui/crew/Jane Doe/self-reports", json=DAILY)

    assert response.status_code == 422
    assert "pseudonym" in response.json()["detail"]


async def test_no_personal_data_reaches_the_bus(harness):
    await assessed_astronaut(harness)
    profile = harness.example("domain/astronaut-profile")
    personal = [profile["date_of_birth"], profile["gender_identity"], profile["sex_at_birth"]]
    bus = json.dumps([payload for _, payload, _, _ in harness.mqtt.published])

    assert harness.mqtt.published
    for topic, payload, qos, retain in harness.mqtt.published:
        assert topic.endswith("/cmd") and qos == 1 and not retain
        assert validation.errors("telemetry/command", payload) == []
    for value in personal:
        assert value not in bus


async def test_assessment_window_excludes_old_feature_sets(harness):
    await assessed_astronaut(harness)
    harness.clock.advance(days=15)

    outcome = await harness.runtime.analysis.assess(harness.astronaut_id)

    assert outcome.skipped == "no_feature_sets"


async def test_health_reports_the_bus(harness):
    body = (await harness.http.get("/health")).json()

    assert body["status"] == "ok"
    assert body["mqtt_connected"] is True



async def test_daily_context_etag_rejects_stale_update(harness):
    context = harness.example("domain/daily-context")
    url = f"/ui/crew/{harness.astronaut_id}/daily-context/{context['date']}"
    assert (await harness.http.put(url, json=context)).status_code == 200
    first = await harness.http.get(url)
    etag = first.headers["etag"]
    newer = {**context, "mission_day": 42}
    assert (await harness.http.put(url, json=newer, headers={"If-Match": etag})).status_code == 200
    stale = await harness.http.put(url, json=context, headers={"If-Match": etag})
    assert stale.status_code == 412
    assert (await harness.http.get(url)).json()["mission_day"] == 42


async def test_profile_etag_rejects_stale_update_and_create(harness):
    profile = harness.example("domain/astronaut-profile")
    url = f"/ui/crew/{harness.astronaut_id}/profile"
    assert (await harness.http.put(url, json=profile, headers={"If-None-Match": "*"})).status_code == 200
    assert (await harness.http.put(url, json=profile, headers={"If-None-Match": "*"})).status_code == 412
    etag = (await harness.http.get(url)).headers["etag"]
    changed = {**profile, "anthropometry": {**profile["anthropometry"], "body_mass_kg": 75}}
    assert (await harness.http.put(url, json=changed, headers={"If-Match": etag})).status_code == 200
    assert (await harness.http.put(url, json=profile, headers={"If-Match": etag})).status_code == 412
    assert (await harness.http.get(url)).json()["anthropometry"]["body_mass_kg"] == 75
