"""Edge cases of the UI API, the command tracking and the bus connection."""

import asyncio
import json
import logging

import aiomqtt
import pytest
from fastapi.testclient import TestClient
from monitor.db import Store, utc_key
from monitor.ingest import Bus, Ingest, _as_bytes
from monitor.main import create_app
from monitor.settings import AppSettings
from spine_contracts import topics

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


# --- UI API ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("/ui/crew/AST-01/self-reports", b"{nope"),
        ("/ui/crew/AST-01/self-reports", b"[1, 2]"),
    ],
)
async def test_body_must_be_a_json_object(harness, path, content):
    response = await harness.http.post(path, content=content, headers={"content-type": "application/json"})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    "path",
    [
        "/ui/crew/AST-01/profile",
        "/ui/crew/AST-01/daily-context/2026-11-13",
        "/ui/sessions/3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01",
        "/ui/commands/3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01",
    ],
)
async def test_missing_entities_are_404(harness, path):
    response = await harness.http.get(path)

    assert response.status_code == 404
    assert response.json()["type"] == "not_found"


async def test_steps_of_an_unknown_session_are_404(harness):
    response = await harness.http.post("/ui/sessions/3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01/stop", json={})

    assert response.status_code == 404


async def test_pydantic_errors_are_problem_details(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()

    response = await harness.http.post(f"/ui/sessions/{session_id}/stop", json={"reason": "bored"})

    assert response.status_code == 422
    assert response.json()["errors"][0]["pointer"] == "/reason"


@pytest.mark.parametrize("field", ["session_id", "started_at", "ended_at", "end_reason", "events"])
async def test_app_owned_session_fields_are_refused(harness, field):
    await harness.setup_astronaut()

    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan(**{field: None}))

    assert response.status_code == 422
    assert field in response.json()["detail"]


async def test_session_needs_a_device(harness):
    await harness.setup_astronaut()
    plan = harness.plan()
    del plan["device_id"]

    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=plan)

    assert response.status_code == 422
    assert response.json()["errors"] == [{"pointer": "/device_id", "message": "required"}]


@pytest.mark.parametrize("value", [0, "10", 1.5])
async def test_max_duration_must_be_a_positive_integer(harness, value):
    await harness.setup_astronaut()

    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan(max_duration_s=value))

    assert response.status_code == 422
    assert not harness.mqtt.commands()


async def test_max_duration_and_explicit_mission_context_reach_the_command(harness):
    context = {"phase": "inflight", "gravity_environment": "microgravity", "mission_day": 12}

    response = await harness.http.post(
        f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan(max_duration_s=300, mission_context=context)
    )

    assert response.status_code == 202, response.text
    assert response.json()["session"]["mission_context"] == context
    assert harness.mqtt.commands("start_session")[0]["max_duration_s"] == 300


async def test_session_source_follows_the_device(harness):
    status = harness.example("telemetry/device-status.degraded") | {"source": "simulator"}
    harness.mqtt.deliver(topics.status(harness.astronaut_id, harness.device_id), status)
    await harness.settle()
    await harness.setup_astronaut()

    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan())

    assert response.json()["session"]["source"] == "simulator"


async def test_report_keeps_its_own_mission_day_and_source(harness):
    await harness.setup_astronaut()
    body = {"kind": "daily", "pain_nrs": 1, "stiffness_nrs": 1, "mission_day": 5, "source": "simulator"}

    report = (await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json=body)).json()["report"]

    assert (report["mission_day"], report["source"]) == (5, "simulator")


async def test_report_without_daily_context_has_no_mission_day(harness):
    body = {"kind": "daily", "pain_nrs": 1, "stiffness_nrs": 1}

    report = (await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json=body)).json()["report"]

    assert "mission_day" not in report


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/ui/crew/nope/devices"),
        ("get", "/ui/crew/nope/profile"),
        ("put", "/ui/crew/nope/daily-context/2026-11-13"),
        ("get", "/ui/crew/nope/daily-context/2026-11-13"),
        ("get", "/ui/crew/nope/self-reports/due"),
        ("post", "/ui/crew/nope/sessions"),
    ],
)
async def test_every_crew_route_checks_the_pseudonym(harness, method, path):
    response = await harness.http.request(method, path, json={})

    assert response.status_code == 422


async def test_daily_context_date_comes_from_the_path(harness):
    url = f"/ui/crew/{harness.astronaut_id}/daily-context/2026-11-13"

    response = await harness.http.put(url, json={"date": "2026-11-14", "phase": "inflight", "gravity_environment": "microgravity"})

    assert response.status_code == 422


async def test_invalid_entity_lists_every_schema_error(harness):
    response = await harness.http.put(f"/ui/crew/{harness.astronaut_id}/daily-context/2026-11-13", json={"phase": "nowhere"})

    errors = response.json()["errors"]
    assert response.status_code == 422
    assert {e["pointer"] for e in errors} >= {"/", "/phase"}


# --- commands and acks -----------------------------------------------------------------------------


async def test_ack_from_another_device_is_ignored(harness):
    await harness.setup_astronaut()
    body = (await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan())).json()
    forged = {**body["command"], "device_id": "wb-99"}

    await harness.ack(forged, "accepted")

    assert (await harness.http.get(f"/ui/sessions/{body['session']['session_id']}")).json()["state"] == "starting"


async def test_stop_rejected_with_no_session_ends_it(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()
    await harness.send_chunks(session_id)
    stop = (await harness.http.post(f"/ui/sessions/{session_id}/stop", json={"reason": "aborted_other"})).json()["command"]

    await harness.ack(stop, "rejected", error={"code": "no_session"})

    session = (await harness.http.get(f"/ui/sessions/{session_id}")).json()
    assert session["state"] == "processed"
    assert session["session"]["end_reason"] == "aborted_other"


async def test_stop_rejected_for_another_reason_keeps_it_running(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()
    stop = (await harness.http.post(f"/ui/sessions/{session_id}/stop", json={})).json()["command"]

    await harness.ack(stop, "rejected", error={"code": "busy"})

    assert (await harness.http.get(f"/ui/sessions/{session_id}")).json()["state"] == "running"


async def test_late_start_completion_after_end_is_harmless(harness):
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.send_chunks(session_id)
    await harness.stop_session(session_id, start)
    stop = harness.mqtt.commands("stop_session")[0]

    await harness.ack(stop, "failed", error={"code": "internal"})

    assert len(harness.extractor.requests) == 1


async def test_failed_start_after_accept_rejects_the_session(harness):
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()

    await harness.ack(start, "failed", error={"code": "internal"})

    session = (await harness.http.get(f"/ui/sessions/{session_id}")).json()
    assert (session["state"], session["error"]) == ("rejected", "internal")


async def test_event_note_and_calibration_duration_reach_the_bus(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()

    await harness.http.post(f"/ui/sessions/{session_id}/events", json={"event_type": "note", "note": "itchy electrode"})
    await harness.http.post(
        f"/ui/sessions/{session_id}/calibrations", json={"calibration_type": "imu", "stream_ids": ["imu_l3"], "duration_s": 5}
    )

    assert harness.mqtt.commands("mark_event")[0]["note"] == "itchy electrode"
    assert "rep_index" not in harness.mqtt.commands("mark_event")[0]
    assert harness.mqtt.commands("calibrate")[0]["duration_s"] == 5


async def test_events_are_kept_in_time_order(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()
    first = (await harness.http.post(f"/ui/sessions/{session_id}/events", json={"event_type": "rep_start", "rep_index": 1})).json()
    harness.clock.advance(seconds=2)
    second = (await harness.http.post(f"/ui/sessions/{session_id}/events", json={"event_type": "rep_end", "rep_index": 1})).json()

    await harness.ack(second["command"], "completed")
    await harness.ack(first["command"], "completed")

    events = (await harness.http.get(f"/ui/sessions/{session_id}")).json()["session"]["events"]
    assert [e["event_type"] for e in events] == ["rep_start", "rep_end"]


async def test_calibration_ack_without_result_changes_nothing(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()
    before = (await harness.http.get(f"/ui/sessions/{session_id}")).json()["session"]["streams"]
    body = {"calibration_type": "stretch", "stream_ids": ["stretch_lumbar"]}
    command = (await harness.http.post(f"/ui/sessions/{session_id}/calibrations", json=body)).json()["command"]

    await harness.ack(command, "completed")

    assert (await harness.http.get(f"/ui/sessions/{session_id}")).json()["session"]["streams"] == before


async def test_calibration_of_several_streams_uses_the_known_entity(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()
    body = {"calibration_type": "emg_mvc", "stream_ids": ["emg_lat_left", "imu_l3"]}
    command = (await harness.http.post(f"/ui/sessions/{session_id}/calibrations", json=body)).json()["command"]

    await harness.ack(command, "completed", result={"calibration_id": "0b9e6a10-1111-4222-8333-444455556677"})

    streams = (await harness.http.get(f"/ui/sessions/{session_id}")).json()["session"]["streams"]
    assert all(s.get("calibration_id") != "0b9e6a10-1111-4222-8333-444455556677" for s in streams)


async def test_calibration_of_an_ended_session_is_not_linked(harness):
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.send_chunks(session_id)
    await harness.stop_session(session_id, start)
    calibration = harness.example("domain/calibration.stretch") | {"calibration_id": "0b9e6a10-1111-4222-8333-444455556688", "session_id": session_id}

    harness.mqtt.deliver(topics.calibration(harness.astronaut_id, harness.device_id, "stretch_lumbar"), calibration)
    await harness.settle()

    streams = (await harness.http.get(f"/ui/sessions/{session_id}")).json()["session"]["streams"]
    assert calibration["calibration_id"] not in [s.get("calibration_id") for s in streams]


async def test_calibration_for_another_stream_changes_nothing(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()
    assert not harness.runtime.control.link_calibration(session_id, "not_declared", "0b9e6a10-1111-4222-8333-444455556699")


async def test_timeout_of_a_step_keeps_the_session(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()
    command = (await harness.http.post(f"/ui/sessions/{session_id}/events", json={"event_type": "artifact"})).json()["command"]

    await asyncio.sleep(0.3)

    assert (await harness.http.get(f"/ui/commands/{command['command_id']}")).json()["state"] == "timeout"
    assert (await harness.http.get(f"/ui/sessions/{session_id}")).json()["state"] == "running"


async def test_late_accept_after_a_real_rejection_stays_rejected(harness):
    await harness.setup_astronaut()
    body = (await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=harness.plan())).json()
    await harness.ack(body["command"], "rejected", error={"code": "busy"})

    await harness.ack(body["command"], "accepted")

    assert (await harness.http.get(f"/ui/sessions/{body['session']['session_id']}")).json()["state"] == "rejected"


async def test_bus_down_on_a_step_keeps_the_session(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()
    harness.mqtt.fail_publish = True

    response = await harness.http.post(f"/ui/sessions/{session_id}/events", json={"event_type": "artifact"})

    assert response.status_code == 503
    assert (await harness.http.get(f"/ui/sessions/{session_id}")).json()["state"] == "running"


async def test_failed_background_task_is_logged(harness, caplog):
    async def explode(_: str) -> None:
        raise RuntimeError("analysis crashed")

    control = harness.runtime.control
    control._on_session_ended = explode
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()

    with caplog.at_level(logging.ERROR, logger="monitor.control"):
        await harness.ack(start, "completed")

    assert "background task failed" in caplog.text
    assert (await harness.http.get(f"/ui/sessions/{session_id}")).json()["state"] == "ended"


# --- ingestion and the bus -------------------------------------------------------------------------


async def test_internal_errors_are_recorded_and_ingestion_goes_on(harness, monkeypatch):
    def broken(_: dict) -> bool:
        raise RuntimeError("disk full")

    monkeypatch.setattr(harness.store, "add_chunk", broken)
    chunk = harness.example("telemetry/stream-chunk.stretch")
    harness.mqtt.deliver(topics.stream(harness.astronaut_id, harness.device_id, "stretch_lumbar"), chunk)
    await harness.settle()

    assert harness.runtime.ingest.counts["rejected"] == 1
    assert harness.store.rejected_count() == 1


async def test_chunk_of_a_session_on_another_device_is_rejected(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()
    chunk = harness.example("telemetry/stream-chunk.stretch") | {"session_id": session_id, "device_id": "wb-02"}

    harness.mqtt.deliver(topics.stream(harness.astronaut_id, "wb-02", "stretch_lumbar"), chunk)
    await harness.settle()

    assert harness.store.rejected_count() == 1


async def test_unknown_session_chunks_can_be_dropped(tmp_path):
    store = Store(tmp_path / "x.db")
    ingest = Ingest(store, control=None, store_unknown_session_chunks=False)  # pyright: ignore[reportArgumentType]
    chunk = json.loads(json.dumps(_example_chunk()))

    ingest.handle(topics.stream("AST-01", "wb-01", "stretch_lumbar"), json.dumps(chunk).encode())

    assert ingest.counts["dropped"] == 1
    assert store.session_chunks("AST-01", chunk["session_id"]) == []
    store.close()


def _example_chunk() -> dict:
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "telemetry" / "stream-chunk.stretch.json"
    return json.loads(path.read_text(encoding="utf-8"))


async def test_calibration_without_session_is_only_stored(harness):
    calibration = harness.example("domain/calibration.stretch")
    del calibration["session_id"]

    harness.mqtt.deliver(topics.calibration(harness.astronaut_id, harness.device_id, "stretch_lumbar"), calibration)
    await harness.settle()

    assert harness.store.calibrations({calibration["calibration_id"]}) == [calibration]


@pytest.mark.parametrize(("payload", "expected"), [(None, b""), (b"x", b"x"), (bytearray(b"y"), b"y"), ("z", b"z"), (7, b"7")])
async def test_payload_bytes(payload, expected):
    assert _as_bytes(payload) == expected


async def test_publish_without_a_connection_fails():
    bus = Bus(lambda: None)  # pyright: ignore[reportArgumentType]

    with pytest.raises(ConnectionError):
        await bus.publish("t", {})


class _FlakyClient:
    def __init__(self, attempts: list[int]) -> None:
        self._attempts = attempts

    async def __aenter__(self):
        self._attempts.append(1)
        if len(self._attempts) < 3:
            raise aiomqtt.MqttError("refused")
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def subscribe(self, topic: str, qos: int) -> None:
        return None

    @property
    def messages(self):
        async def forever():
            await asyncio.Event().wait()
            yield

        return forever()


async def test_bus_reconnects_with_backoff():
    attempts: list[int] = []
    bus = Bus(lambda: _FlakyClient(attempts), reconnect_min_s=0.01, reconnect_max_s=0.02)  # pyright: ignore[reportArgumentType]
    task = asyncio.create_task(bus.run(None))  # pyright: ignore[reportArgumentType]

    await asyncio.wait_for(bus.connected.wait(), 2)

    assert len(attempts) == 3
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not bus.connected.is_set()


def test_naive_timestamps_are_refused():
    with pytest.raises(ValueError, match="naive"):
        utc_key("2026-11-13T10:00:00")


def test_lifespan_starts_and_stops_the_bus(tmp_path):
    class IdleClient(_FlakyClient):
        async def __aenter__(self):
            return self

    app = create_app(AppSettings(_env_file=None, db_path=tmp_path / "app.db"), mqtt_client_factory=lambda: IdleClient([]))  # pyright: ignore[reportArgumentType]

    with TestClient(app) as client:
        for _ in range(100):
            if client.get("/health").json()["mqtt_connected"]:
                break
        assert client.get("/health").json()["mqtt_connected"] is True
