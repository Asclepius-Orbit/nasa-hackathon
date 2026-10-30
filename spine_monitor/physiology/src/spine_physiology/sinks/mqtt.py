"""Sink MQTT (aiomqtt): publica los envelopes con topic; los objetos de dominio nunca van al bus.

Dueño: D1. QoS 1 para todo (lo fija el contrato AsyncAPI), retained según `Envelope.retained`
(DeviceStatus). El cliente es cualquier objeto con la firma de `aiomqtt.Client.publish`, así los tests
usan un cliente falso sin broker.
"""

from collections.abc import AsyncIterable, Iterable
from typing import Protocol

from spine_physiology.envelope import Envelope

# QoS de todos los mensajes del simulador (contrato AsyncAPI, no un parámetro de simulación).
QOS = 1


class MqttPublisher(Protocol):
    async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None: ...


def encode(envelope: Envelope) -> bytes:
    """Payload JSON compacto en UTF-8 (mismo JSON que la línea JSONL)."""
    raise NotImplementedError


class MqttSink:
    def __init__(self, client: MqttPublisher) -> None:
        raise NotImplementedError

    async def send(self, envelope: Envelope) -> bool:
        """Publica si tiene topic; False si es un objeto de dominio (no se publica)."""
        raise NotImplementedError


async def publish_all(messages: Iterable[Envelope] | AsyncIterable[Envelope], sink: MqttSink) -> int:
    """Publica en orden; devuelve cuántos se publicaron. El ritmo lo pone `pacing.paced` aguas arriba."""
    raise NotImplementedError
