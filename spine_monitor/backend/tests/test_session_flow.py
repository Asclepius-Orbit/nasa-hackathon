"""Protocol driven from the UI API, acks from the gateway, and the engine calls that follow."""

import asyncio

import pytest
from spine_contracts import topics, validation

pytestmark = pytest.mark.anyio


async def test_subscribes_to_every_bus_topic(harness):
    subscribed = {topic for topic, qos in harness.mqtt.subscriptions if qos == 1}

    assert subscribed == {
        topics.SUBSCRIBE_ALL_STREAMS,
        topics.SUBSCRIBE_ALL_STATUS,
        topics.SUBSCRIBE_ALL_ACKS,
        topics.SUBSCRIBE_ALL_CLOCKS,
        topics.SUBSCRIBE_ALL_CALIBRATIONS,
    }


async def test_full_preflight_session_builds_features_baseline_and_assessment(harness):
    await harness.setup_astronaut()
    calibration = harness.example("domain/calibration.stretch")
    harness.mqtt.deliver(topics.calibration(harness.astronaut_id, harness.device_id, "stretch_lumbar"), calibration)
    anchor = harness.example("telemetry/clock-sync.round-trip")
    harness.mqtt.deliver(topics.clock(harness.astronaut_id, harness.device_id), anchor)

    session_id, start = await harness.start_session()
    [published] = harness.mqtt.commands("start_session")
    assert validation.errors("telemetry/command", published) == []
    assert published["stream_ids"] == ["emg_lat_left", "imu_l3", "stretch_lumbar"]
    assert (await harness.http.get(f"/ui/sessions/{session_id}")).json()["state"] == "running"

    await harness.send_chunks(session_id)
    event = await harness.http.post(f"/ui/sessions/{session_id}/events", json={"event_type": "rep_start", "rep_index": 1})
    assert event.status_code == 202
    await harness.ack(event.json()["command"], "accepted")
    await harness.ack(event.json()["command"], "completed")

    await harness.stop_session(session_id, start)

    body = (await harness.http.get(f"/ui/sessions/{session_id}")).json()
    assert body["state"] == "processed", body
    session = body["session"]
    assert validation.errors("domain/session", session) == []
    assert session["end_reason"] == "completed"
    assert session["ended_at"] is not None
    assert [e["event_type"] for e in session["events"]] == ["rep_start"]
    assert body["feature_set"]["session_id"] == session_id

    [request] = harness.extractor.requests
    assert len(request.chunks) == 3
    assert [c.calibration_id for c in request.calibrations] == [calibration["calibration_id"]]
    assert len(request.clock_syncs) == 1
    assert request.profile is not None

    baseline = harness.store.latest_baseline(harness.astronaut_id)
    assert baseline is not None and baseline["version"] == 1
    assert baseline["built_from_session_ids"] == [session_id]
    assessment = harness.store.latest_assessment(harness.astronaut_id)
    assert assessment is not None
    assert validation.errors("domain/assessment", assessment) == []
    assert assessment["based_on"]["baseline_id"] == baseline["baseline_id"]


async def test_each_preflight_session_rebuilds_the_baseline_with_a_new_version(harness):
    await harness.setup_astronaut()
    sessions = []
    for n in range(2):
        session_id, start = await harness.start_session()
        await harness.send_chunks(session_id, seq_offset=n * 10)
        await harness.stop_session(session_id, start)
        sessions.append(session_id)

    baseline = harness.store.latest_baseline(harness.astronaut_id)
    assert baseline["version"] == 2
    assert sorted(baseline["built_from_session_ids"]) == sorted(sessions)


async def test_inflight_session_is_assessed_but_does_not_touch_the_baseline(harness):
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.send_chunks(session_id)
    await harness.stop_session(session_id, start)

    day = harness.clock().date().isoformat()
    await harness.http.put(f"/ui/crew/{harness.astronaut_id}/daily-context/{day}", json={"phase": "inflight", "gravity_environment": "microgravity"})
    session_id, start = await harness.start_session()
    await harness.send_chunks(session_id, seq_offset=10)
    await harness.stop_session(session_id, start)

    assert harness.store.latest_baseline(harness.astronaut_id)["version"] == 1
    assert harness.store.latest_assessment(harness.astronaut_id)["based_on"]["session_ids"]


async def test_session_without_chunks_is_marked_failed(harness):
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.stop_session(session_id, start)

    body = (await harness.http.get(f"/ui/sessions/{session_id}")).json()
    assert body["state"] == "processing_failed"
    assert "no chunks" in body["error"]


async def test_max_duration_end_without_stop(harness):
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.send_chunks(session_id)

    await harness.ack(start, "completed")

    session = (await harness.http.get(f"/ui/sessions/{session_id}")).json()["session"]
    assert session["end_reason"] == "completed"


async def test_aborted_for_pain_keeps_the_reason(harness):
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.send_chunks(session_id)
    await harness.stop_session(session_id, start, reason="aborted_pain")

    session = (await harness.http.get(f"/ui/sessions/{session_id}")).json()["session"]
    assert session["end_reason"] == "aborted_pain"


async def test_rejected_start_marks_the_session_and_frees_the_device(harness):
    await harness.setup_astronaut()
    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan())
    body = response.json()
    await harness.ack(body["command"], "rejected", error={"code": "busy"})

    session = (await harness.http.get(f"/ui/sessions/{body['session']['session_id']}")).json()
    assert (session["state"], session["error"]) == ("rejected", "busy")
    command = (await harness.http.get(f"/ui/commands/{body['command']['command_id']}")).json()
    assert command["state"] == "rejected"
    assert (await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan())).status_code == 202


async def test_second_session_on_a_busy_device_is_refused(harness):
    await harness.setup_astronaut()
    await harness.start_session()

    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan())

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_ack_timeout_then_late_accept(harness):
    await harness.setup_astronaut()
    body = (await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan())).json()
    session_id, command_id = body["session"]["session_id"], body["command"]["command_id"]

    await asyncio.sleep(0.3)
    assert (await harness.http.get(f"/ui/commands/{command_id}")).json()["state"] == "timeout"
    assert (await harness.http.get(f"/ui/sessions/{session_id}")).json()["error"] == "ack_timeout"

    await harness.ack(body["command"], "accepted")
    assert (await harness.http.get(f"/ui/sessions/{session_id}")).json()["state"] == "running"
    assert (await harness.http.get(f"/ui/commands/{command_id}")).json()["state"] == "accepted"


async def test_duplicate_ack_is_ignored(harness):
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.send_chunks(session_id)
    await harness.stop_session(session_id, start)
    await harness.ack(start, "completed")  # QoS 1 redelivery

    assert len(harness.extractor.requests) == 1
    assert len((await harness.http.get(f"/ui/commands/{start['command_id']}")).json()["acks"]) == 2


async def test_calibration_links_its_id_to_the_stream(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()
    response = await harness.http.post(
        f"/ui/sessions/{session_id}/calibrations", json={"calibration_type": "stretch", "stream_ids": ["stretch_lumbar"]}
    )
    assert response.status_code == 202
    command = response.json()["command"]
    calibration = harness.example("domain/calibration.stretch")
    calibration.update(calibration_id="0b9e6a10-1111-4222-8333-444455556666", session_id=session_id)
    harness.mqtt.deliver(topics.calibration(harness.astronaut_id, harness.device_id, "stretch_lumbar"), calibration)
    await harness.ack(command, "accepted")
    await harness.ack(command, "completed", result={"calibration_id": calibration["calibration_id"]})

    streams = (await harness.http.get(f"/ui/sessions/{session_id}")).json()["session"]["streams"]
    stretch = next(s for s in streams if s["stream_id"] == "stretch_lumbar")
    assert stretch["calibration_id"] == calibration["calibration_id"]


async def test_calibrate_rejects_undeclared_streams(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()

    response = await harness.http.post(
        f"/ui/sessions/{session_id}/calibrations", json={"calibration_type": "imu", "stream_ids": ["imu_t12"]}
    )

    assert response.status_code == 422


async def test_steps_need_a_running_session(harness):
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.send_chunks(session_id)
    await harness.stop_session(session_id, start)

    response = await harness.http.post(f"/ui/sessions/{session_id}/events", json={"event_type": "rep_end"})

    assert response.status_code == 409


async def test_exercise_session_needs_a_declared_load(harness):
    # Conditional rule of the schema (if exercise then load_declared): Pydantic alone does not catch it.
    await harness.setup_astronaut()
    plan = harness.plan()
    del plan["load_declared"]

    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=plan)

    assert response.status_code == 422
    assert not harness.mqtt.commands()


async def test_session_needs_a_mission_context(harness):
    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan())

    assert response.status_code == 422
    assert "mission_context" in response.json()["detail"]


async def test_offline_device_refuses_new_sessions(harness):
    await harness.setup_astronaut()
    status = harness.example("telemetry/device-status.degraded")
    status["status"] = "offline"
    harness.mqtt.deliver(topics.status(harness.astronaut_id, harness.device_id), status)
    await harness.settle()

    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan())

    assert response.status_code == 409


async def test_bus_down_fails_the_start(harness):
    await harness.setup_astronaut()
    harness.mqtt.fail_publish = True

    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan())

    assert response.status_code == 503
    assert harness.store.active_session(harness.astronaut_id, harness.device_id) is None
