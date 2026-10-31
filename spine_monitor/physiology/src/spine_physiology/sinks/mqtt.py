"""Sink MQTT (aiomqtt): publica los envelopes con topic; los objetos de dominio nunca van al bus.

Dueño: D1. QoS 1 para todo (lo fija el contrato AsyncAPI), retained según `Envelope.retained`
(DeviceStatus, ClockSync y calibraciones). El cliente es cualquier objeto con la firma de `aiomqtt.Client.publish`, así los tests
usan un cliente falso sin broker.
"""

import json
from collections.abc import AsyncIterable, Iterable
from typing import Protocol

from spine_physiology.envelope import MQTT_KINDS, Envelope

# QoS de todos los mensajes del simulador (contrato AsyncAPI, no un parámetro de simulación).
QOS = 1


class MqttPublisher(Protocol):
    async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None: ...


def encode(envelope: Envelope) -> bytes:
    """Payload JSON compacto en UTF-8 (mismo JSON que la línea JSONL)."""
    return json.dumps(envelope.payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


class MqttSink:
    def __init__(self, client: MqttPublisher) -> None:
        self._client = client

    async def send(self, envelope: Envelope) -> bool:
        """Publica si tiene topic; False si es un objeto de dominio (no se publica)."""
        if envelope.kind not in MQTT_KINDS or envelope.topic is None:
            return False
        await self._client.publish(envelope.topic, encode(envelope), qos=QOS, retain=envelope.retained)
        return True


async def publish_all(messages: Iterable[Envelope] | AsyncIterable[Envelope], sink: MqttSink) -> int:
    """Publica en orden; devuelve cuántos se publicaron. El ritmo lo pone `pacing.paced` aguas arriba."""
    count = 0
    if isinstance(messages, AsyncIterable):
        async for envelope in messages:
            count += await sink.send(envelope)
    else:
        for envelope in messages:
            count += await sink.send(envelope)
    return count
