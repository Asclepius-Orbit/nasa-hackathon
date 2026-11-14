"""Cord protocols end to end: capture -> persistence -> query, with the real engine answering /stretch-estimate.

Sessions of the two cord protocols are started through the UI API, stopped, analysed (the extractor fakes only the feature
extraction, as in the other flow tests) and then read back through the cord-protocols view, Today and first-day.
"""

from typing import Any

import pytest
from spine_contracts import FeatureSet, validation

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

AST = "AST-01"
STRAIGHT, FLEXION = "spine_resistance_straight_v1", "spine_resistance_max_flexion_v1"
FEATURE = {STRAIGHT: "stretch.full_spine.straight_resistance_ohm", FLEXION: "stretch.full_spine.max_flexion_resistance_ohm"}


def cord_plan(harness, protocol: str) -> dict[str, Any]:
    session = harness.example("domain/session.spine-check")
    return {"device_id": session["device_id"], "session_type": "spine_check", "protocol_id": protocol, "streams": session["streams"]}


def measure_with(harness, ohm: dict[str, float]) -> None:
    """The extractor answers each cord session with the resistance of its protocol (score 0.9)."""

    def extract(request) -> FeatureSet:
        harness.extractor.requests.append(request)
        session = request.session
        payload = harness.example("domain/feature-set.preflight")
        payload.update(
            session_id=session.session_id,
            astronaut_id=session.astronaut_id,
            protocol_id=session.protocol_id,
            gravity_environment=session.mission_context.gravity_environment.value,
        )
        payload.pop("window", None)
        payload["computed_at"] = harness.clock().isoformat()
        payload["features"] = [
            {
                "feature_id": FEATURE[session.protocol_id],
                "value": ohm[session.protocol_id],
                "unit": "ohm",
                "n": 40,
                "quality": {"score": 0.9, "assessed_by": "engine"},
            }
        ]
        payload["skipped"] = []
        return FeatureSet.model_validate(payload)

    harness.extractor.extract = extract


async def capture(harness, protocol: str) -> str:
    session_id, start = await harness.start_session(cord_plan(harness, protocol))
    harness.captures = getattr(harness, "captures", 0) + 1
    await harness.send_chunks(session_id, seq_offset=harness.captures * 10)
    await harness.stop_session(session_id, start)
    state = (await harness.http.get(f"/ui/sessions/{session_id}")).json()
    assert state["state"] == "processed", state
    return session_id


async def view(harness, path: str, schema: str) -> dict[str, Any]:
    response = await harness.http.get(f"/ui/crew/{AST}/views/{path}")
    assert response.status_code == 200, response.text
    assert validation.errors(schema, response.json()) == []
    return response.json()


async def profile(harness, **extra: Any) -> None:
    body = harness.example("domain/astronaut-profile") | extra
    assert (await harness.http.put(f"/ui/crew/{AST}/profile", json=body)).status_code == 200


async def test_earth_reference_then_in_flight_follow_up_with_a_calculated_estimate(harness):
    measure_with(harness, {STRAIGHT: 4500.0, FLEXION: 5650.0})

    # Pre-flight: nothing measured yet, both sessions are offered.
    await harness.setup_astronaut()
    await profile(harness, launch_date="2026-12-13")
    cord = await view(harness, "cord-protocols", "app/ui-cord-protocols")
    assert [(p["protocol_id"], p["due"], p["due_reason"]) for p in cord["postures"]] == [
        (STRAIGHT, True, "CORD_NO_EARTH_REFERENCE"),
        (FLEXION, True, "CORD_NO_EARTH_REFERENCE"),
    ]
    today = await view(harness, "today", "app/ui-today")
    assert today["also_today"][-2:] == ["TASK_CORD_STRAIGHT", "TASK_CORD_MAX_FLEXION"]

    # Capture of the Earth references: the protocol id reaches the engine and the stored session and feature set.
    straight_ref = await capture(harness, STRAIGHT)
    assert [r.session.protocol_id for r in harness.extractor.requests] == [STRAIGHT]
    assert harness.store.feature_set(straight_ref)["protocol_id"] == STRAIGHT
    await capture(harness, FLEXION)

    cord = await view(harness, "cord-protocols", "app/ui-cord-protocols")
    straight, flexion = cord["postures"]
    assert (straight["due"], flexion["due"]) == (False, False)
    assert straight["earth_reference"]["resistance_ohm"] == 4500 and straight["earth_reference"]["session_id"] == straight_ref
    assert flexion["earth_reference"]["resistance_ohm"] == 5650
    assert straight["last_follow_up"] is None and straight["estimate_unavailable"] == "ESTIMATE_NO_FOLLOW_UP"
    first_day = await view(harness, "first-day", "app/ui-first-day")
    assert not {"TASK_CORD_STRAIGHT", "TASK_CORD_MAX_FLEXION"} & set(first_day["todo"])

    # In flight, in microgravity: the follow-ups are told apart from the references.
    harness.clock.advance(days=40)
    await harness.setup_astronaut(phase="inflight", gravity="microgravity")
    await profile(harness, launch_date="2026-11-13", landing_date="2027-06-01")
    measure_with(harness, {STRAIGHT: 5650.0, FLEXION: 5700.0})
    cord = await view(harness, "cord-protocols", "app/ui-cord-protocols")
    assert [(p["due"], p["due_reason"]) for p in cord["postures"]] == [(True, "CORD_NO_FOLLOW_UP")] * 2

    follow_up = await capture(harness, STRAIGHT)
    cord = await view(harness, "cord-protocols", "app/ui-cord-protocols")
    straight, flexion = cord["postures"]
    assert (straight["due"], straight["due_reason"]) == (False, "CORD_UP_TO_DATE")
    assert straight["last_follow_up"]["session_id"] == follow_up and straight["last_follow_up"]["gravity_environment"] == "microgravity"
    assert straight["earth_reference"]["session_id"] == straight_ref
    estimate = straight["estimate"]
    assert (estimate["calculated"], estimate["measured_length"], estimate["clinically_validated"], estimate["affects_triage"]) == (True, False, False, False)
    assert estimate["estimated_length_mm"] == pytest.approx(538.5723, abs=1e-3) and estimate["reference_length_mm"] == 500
    assert flexion["due"] is True and flexion["estimate_unavailable"] == "ESTIMATE_NO_FOLLOW_UP"
    today = await view(harness, "today", "app/ui-today")
    assert "TASK_CORD_STRAIGHT" not in today["also_today"] and "TASK_CORD_MAX_FLEXION" in today["also_today"]

    # Cadence: a week later the straight follow-up is overdue again, and the estimate still names the last valid one.
    harness.clock.advance(days=7, minutes=1)
    cord = await view(harness, "cord-protocols", "app/ui-cord-protocols")
    assert (cord["postures"][0]["due"], cord["postures"][0]["due_reason"]) == (True, "CORD_FOLLOW_UP_OVERDUE")
    today = await view(harness, "today", "app/ui-today")
    assert "TASK_CORD_STRAIGHT" in today["also_today"]
    assert cord["postures"][0]["last_follow_up"]["session_id"] == follow_up


async def test_a_resistance_outside_the_bench_model_has_no_estimate_instead_of_a_made_up_one(harness):
    measure_with(harness, {STRAIGHT: 4500.0, FLEXION: 5650.0})
    await harness.setup_astronaut()
    await profile(harness, launch_date="2026-12-13")
    await capture(harness, STRAIGHT)
    harness.clock.advance(days=40)
    await harness.setup_astronaut(phase="inflight", gravity="microgravity")
    await profile(harness, launch_date="2026-11-13", landing_date="2027-06-01")
    measure_with(harness, {STRAIGHT: 90000.0, FLEXION: 5650.0})
    await capture(harness, STRAIGHT)

    straight = (await view(harness, "cord-protocols", "app/ui-cord-protocols"))["postures"][0]

    assert straight["estimate"] is None and straight["estimate_unavailable"] == "ESTIMATE_OUT_OF_MODEL_RANGE"
    assert straight["last_follow_up"]["resistance_ohm"] == 90000

async def test_history_view_lists_the_stored_measurements_and_honours_the_limit(harness):
    measure_with(harness, {STRAIGHT: 4500.0, FLEXION: 5650.0})
    await harness.setup_astronaut()
    await profile(harness, launch_date="2026-12-13")
    first = await capture(harness, STRAIGHT)
    second = await capture(harness, STRAIGHT)

    history = await view(harness, "cord-history", "app/ui-cord-history")
    straight, flexion = history["postures"]
    assert {m["session_id"] for m in straight["measurements"]} == {first, second} and flexion["measurements"] == []
    assert all(m["valid"] and m["gravity_environment"] == "earth_1g" for m in straight["measurements"])

    limited = (await harness.http.get(f"/ui/crew/{AST}/views/cord-history", params={"limit": 1})).json()
    assert len(limited["postures"][0]["measurements"]) == 1 and limited["limit"] == 1
    assert (await harness.http.get(f"/ui/crew/{AST}/views/cord-history", params={"limit": 0})).status_code == 422
