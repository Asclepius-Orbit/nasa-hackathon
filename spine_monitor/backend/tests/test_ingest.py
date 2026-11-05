"""Bus ingestion: full schema validation, topic/payload consistency and idempotent storage."""

import pytest
from spine_contracts import topics

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


def stretch_topic(harness) -> str:
    return topics.stream(harness.astronaut_id, harness.device_id, "stretch_lumbar")


async def test_valid_chunk_is_stored_once(harness):
    chunk = harness.example("telemetry/stream-chunk.stretch")
    harness.mqtt.deliver(stretch_topic(harness), chunk)
    harness.mqtt.deliver(stretch_topic(harness), chunk)  # QoS 1 redelivery
    await harness.settle()

    assert harness.store.session_chunks(harness.astronaut_id, chunk["session_id"]) == [chunk]
    assert harness.runtime.ingest.counts["accepted"] == 1
    assert harness.runtime.ingest.counts["duplicate"] == 1


async def test_conditional_schema_rule_is_enforced(harness):
    # length_mm without calibration_id: only the JSON Schema `if/then` catches it, not Pydantic.
    chunk = harness.example("telemetry/stream-chunk.stretch")
    del chunk["calibration_id"]
    harness.mqtt.deliver(stretch_topic(harness), chunk)
    await harness.settle()

    assert harness.store.session_chunks(harness.astronaut_id, chunk["session_id"]) == []
    assert harness.store.rejected_count() == 1


@pytest.mark.parametrize(
    ("topic_ids", "reason"),
    [
        (("AST-02", "wb-01", "stretch_lumbar"), "astronaut_id"),
        (("AST-01", "wb-02", "stretch_lumbar"), "device_id"),
        (("AST-01", "wb-01", "stretch_other"), "stream_id"),
    ],
)
async def test_payload_must_match_its_topic(harness, topic_ids, reason):
    harness.mqtt.deliver(topics.stream(*topic_ids), harness.example("telemetry/stream-chunk.stretch"))
    await harness.settle()

    assert harness.store.rejected_count() == 1
    assert harness.runtime.ingest.counts["accepted"] == 0


async def test_garbage_never_stops_ingestion(harness):
    harness.mqtt.deliver(stretch_topic(harness), b"{not json")
    harness.mqtt.deliver(f"{topics.ROOT}/crew/AST-01/device/wb-01/unknown", b"{}")
    harness.mqtt.deliver(stretch_topic(harness), harness.example("telemetry/stream-chunk.stretch"))
    await harness.settle()

    assert harness.store.rejected_count() == 2
    assert harness.runtime.ingest.counts["accepted"] == 1


async def test_device_status_keeps_the_latest(harness):
    status = harness.example("telemetry/device-status.degraded")
    older = {**status, "timestamp": "2020-01-01T00:00:00Z", "status": "ok"}
    harness.mqtt.deliver(topics.status(harness.astronaut_id, harness.device_id), status)
    harness.mqtt.deliver(topics.status(harness.astronaut_id, harness.device_id), older)
    await harness.settle()

    devices = (await harness.http.get(f"/ui/crew/{harness.astronaut_id}/devices")).json()
    assert devices == [status]


async def test_clock_and_calibration_are_stored(harness):
    anchor = harness.example("telemetry/clock-sync.round-trip")
    calibration = harness.example("domain/calibration.stretch")
    harness.mqtt.deliver(topics.clock(harness.astronaut_id, harness.device_id), anchor)
    harness.mqtt.deliver(topics.calibration(harness.astronaut_id, harness.device_id, "stretch_lumbar"), calibration)
    await harness.settle()

    assert harness.store.clock_syncs(harness.astronaut_id, harness.device_id, {anchor["boot_id"]}) == [anchor]
    assert harness.store.calibrations({calibration["calibration_id"]}) == [calibration]


async def test_ack_for_an_unknown_command_is_harmless(harness):
    ack = harness.example("telemetry/command-ack.accepted")
    harness.mqtt.deliver(topics.ack(ack["astronaut_id"], ack["device_id"]), ack)
    await harness.settle()

    assert harness.store.rejected_count() == 0
