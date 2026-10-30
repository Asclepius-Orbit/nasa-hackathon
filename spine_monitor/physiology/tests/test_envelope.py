from datetime import UTC, datetime, timedelta, timezone

import pytest
from spine_contracts import ClockSync, topics, validation

from spine_physiology.envelope import Envelope, EnvelopeKind

AT = datetime(2026, 11, 1, 7, 0, tzinfo=UTC)


def clock_sync() -> ClockSync:
    return ClockSync(
        schema_version="1.0.0", astronaut_id="AST-01", device_id="sim-01", boot_id=1,
        device_ms=0, utc=AT, uncertainty_ms=10.0, method="simulated",
    )  # fmt: skip


def test_of_dumps_a_valid_payload():
    env = Envelope.of(
        EnvelopeKind.CLOCK_SYNC, clock_sync(), at=AT, astronaut_id="AST-01", day=0, topic=topics.clock("AST-01", "sim-01")
    )
    assert validation.errors("telemetry/clock-sync", env.payload) == []
    assert env.payload["utc"] == "2026-11-01T07:00:00Z"
    assert not env.retained


def test_at_must_be_aware_utc():
    with pytest.raises(ValueError, match="UTC"):
        Envelope.of(EnvelopeKind.CLOCK_SYNC, clock_sync(), at=AT.replace(tzinfo=None), astronaut_id="AST-01", day=0, topic="t")
    with pytest.raises(ValueError, match="UTC"):
        Envelope.of(
            EnvelopeKind.CLOCK_SYNC, clock_sync(), at=AT.astimezone(timezone(timedelta(hours=2))), astronaut_id="AST-01", day=0, topic="t"
        )


def test_topic_only_for_mqtt_kinds():
    with pytest.raises(ValueError, match="need a topic"):
        Envelope(EnvelopeKind.STREAM_CHUNK, None, {}, AT, False, "AST-01", 0)
    with pytest.raises(ValueError, match="must not have"):
        Envelope(EnvelopeKind.SESSION, "asclepius/v1/x", {}, AT, False, "AST-01", 0)
    Envelope(EnvelopeKind.GROUND_TRUTH, None, {"type": "latent_state"}, AT, False, "AST-01", 0)


def test_calibrations_travel_over_mqtt_and_must_be_retained():
    topic = topics.calibration("AST-01", "sim-01", "stretch_lumbar")
    Envelope(EnvelopeKind.CALIBRATION, topic, {}, AT, True, "AST-01", 0)
    with pytest.raises(ValueError, match="need a topic"):
        Envelope(EnvelopeKind.CALIBRATION, None, {}, AT, True, "AST-01", 0)
    with pytest.raises(ValueError, match="retained"):
        Envelope(EnvelopeKind.CALIBRATION, topic, {}, AT, False, "AST-01", 0)
