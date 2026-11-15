"""Ingest drops (and says so) whatever does not come from the configured telemetry source, so real and simulated data never mix."""

import logging
from typing import Any

import pytest
from spine_contracts import topics

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture(params=["simulator", "device"])
def configured(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def harness_settings(configured: str) -> dict[str, Any]:
    return {"telemetry_source": configured}


def deliveries(harness, source: str) -> dict[str, tuple[str, dict[str, Any]]]:
    a, d = harness.astronaut_id, harness.device_id
    chunk = {**harness.example("telemetry/stream-chunk.stretch"), "source": source}
    return {
        "stream": (topics.stream(a, d, "stretch_spine"), chunk),
        "status": (topics.status(a, d), {**harness.example("telemetry/device-status.degraded"), "source": source}),
        "calibration": (topics.calibration(a, d, "stretch_spine"), {**harness.example("domain/calibration.stretch"), "source": source}),
    }


@pytest.mark.parametrize("kind", ["stream", "status", "calibration"])
@pytest.mark.parametrize("foreign", ["other", "replay"])
async def test_foreign_source_is_dropped_and_logged(harness, configured, kind, foreign, caplog):
    source = {"other": "device" if configured == "simulator" else "simulator", "replay": "replay"}[foreign]
    topic, payload = deliveries(harness, source)[kind]
    with caplog.at_level(logging.WARNING, logger="monitor.ingest"):
        harness.mqtt.deliver(topic, payload)
        await harness.settle()

    counts = harness.runtime.ingest.counts
    assert counts["wrong_source"] == 1
    assert counts["accepted"] == 0
    assert counts["rejected"] == 0
    assert harness.store.rejected_count() == 0
    assert harness.store.session_chunks(harness.astronaut_id, payload.get("session_id", "")) == []
    assert any(topic in r.getMessage() and source in r.getMessage() and configured in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("kind", ["stream", "status", "calibration"])
async def test_configured_source_is_accepted(harness, configured, kind):
    topic, payload = deliveries(harness, configured)[kind]
    harness.mqtt.deliver(topic, payload)
    await harness.settle()

    assert harness.runtime.ingest.counts["accepted"] == 1
    assert harness.runtime.ingest.counts["wrong_source"] == 0


async def test_ack_and_clock_carry_no_source_and_are_not_dropped(harness):
    anchor = harness.example("telemetry/clock-sync.round-trip")
    harness.mqtt.deliver(topics.clock(harness.astronaut_id, harness.device_id), anchor)
    await harness.settle()

    assert harness.runtime.ingest.counts["accepted"] == 1
    assert harness.runtime.ingest.counts["wrong_source"] == 0
