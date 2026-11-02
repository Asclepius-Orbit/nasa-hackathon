import asyncio
import dataclasses
import importlib.util
import json
from pathlib import Path

import pytest
from spine_physiology import MissionGenerator
from spine_physiology.envelope import MQTT_KINDS
from spine_physiology.sinks.mqtt import QOS, MqttSink, encode, publish_all
from spine_physiology.sinks.pacing import Pacer, paced

_spec = importlib.util.spec_from_file_location("_tiny_mission", Path(__file__).with_name("test_generator.py"))
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
tiny_config = _module.tiny_config


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes, int, bool]] = []

    async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        self.published.append((topic, payload, qos, retain))


@pytest.fixture(scope="module")
def messages():
    return list(MissionGenerator(tiny_config()).iter_messages())


def test_only_mqtt_kinds_published_qos1_retained_from_envelope(messages):
    client = FakeClient()
    count = asyncio.run(publish_all(messages, MqttSink(client)))
    expected = [m for m in messages if m.kind in MQTT_KINDS]
    assert count == len(expected) < len(messages)
    assert [(t, p, r) for t, p, _, r in client.published] == [(m.topic, encode(m), m.retained) for m in expected]
    assert {q for _, _, q, _ in client.published} == {QOS} == {1}
    assert any(r for *_, r in client.published)
    assert not all(r for *_, r in client.published)


def test_send_returns_false_for_domain_objects(messages):
    client = FakeClient()
    domain = next(m for m in messages if m.kind not in MQTT_KINDS)
    assert asyncio.run(MqttSink(client).send(domain)) is False
    assert client.published == []


def test_encode_is_compact_utf8_json(messages):
    raw = encode(messages[0])
    assert json.loads(raw.decode("utf-8")) == messages[0].payload
    assert raw == json.dumps(messages[0].payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def test_encode_rejects_nan(messages):
    bad = dataclasses.replace(messages[0], payload={"x": float("nan")})
    with pytest.raises(ValueError):
        encode(bad)


def test_publish_all_accepts_async_iterable_and_matches_batch(messages):
    batch = FakeClient()
    asyncio.run(publish_all(messages, MqttSink(batch)))
    streamed = FakeClient()
    asyncio.run(publish_all(paced(messages, Pacer(None)), MqttSink(streamed)))
    assert streamed.published == batch.published


def test_same_seed_same_bytes_through_sink(messages):
    first = FakeClient()
    asyncio.run(publish_all(messages, MqttSink(first)))
    again = FakeClient()
    asyncio.run(publish_all(MissionGenerator(tiny_config()).iter_messages(), MqttSink(again)))
    assert again.published == first.published


def test_calibrations_are_published_retained_on_their_topic(messages):
    from spine_physiology.envelope import EnvelopeKind

    client = FakeClient()
    asyncio.run(publish_all(messages, MqttSink(client)))
    calibrations = [m for m in messages if m.kind is EnvelopeKind.CALIBRATION]
    assert calibrations
    published = {topic: (retain, qos) for topic, _, qos, retain in client.published}
    for m in calibrations:
        assert published[m.topic] == (True, QOS)
    assert all("/calibration/" in m.topic for m in calibrations)
