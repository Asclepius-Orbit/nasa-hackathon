"""MQTT sink (aiomqtt): publishes the envelopes that have a topic; domain objects never go on the bus.

Owner: D1. QoS 1 for everything (fixed by the AsyncAPI contract), retained per `Envelope.retained`
(DeviceStatus, ClockSync and calibrations). The client is any object with the signature of `aiomqtt.Client.publish`, so tests
use a fake client without a broker.
"""

import json
from collections.abc import AsyncIterable, Awaitable, Iterable
from typing import Protocol

from spine_physiology.envelope import MQTT_KINDS, Envelope

# QoS of all simulator messages (AsyncAPI contract, not a simulation parameter).
QOS = 1


class MqttPublisher(Protocol):
    # `def` + Awaitable (not `async def`): aiomqtt returns a Coroutine, which a Protocol `async def` does not allow.
    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> Awaitable[None]: ...


def encode(envelope: Envelope) -> bytes:
    """Compact JSON payload in UTF-8 (same JSON as the JSONL line)."""
    return json.dumps(envelope.payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


class MqttSink:
    def __init__(self, client: MqttPublisher) -> None:
        self._client = client

    async def send(self, envelope: Envelope) -> bool:
        """Publishes if it has a topic; False if it is a domain object (not published)."""
        if envelope.kind not in MQTT_KINDS or envelope.topic is None:
            return False
        await self._client.publish(envelope.topic, encode(envelope), qos=QOS, retain=envelope.retained)
        return True


async def publish_all(messages: Iterable[Envelope] | AsyncIterable[Envelope], sink: MqttSink) -> int:
    """Publishes in order; returns how many were published. Pacing is set by `pacing.paced` upstream."""
    count = 0
    if isinstance(messages, AsyncIterable):
        async for envelope in messages:
            count += await sink.send(envelope)
    else:
        for envelope in messages:
            count += await sink.send(envelope)
    return count
