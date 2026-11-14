"""Puertos del gateway: lo que la app necesita de la fuente de líneas del Arduino.

Solo tipos, sin lógica. La implementación real es ``serial_source.SerialLineSource`` (pyserial en un hilo) y los tests de la
app usan una fuente de mentira que reparte líneas ya preparadas. Los puertos de MQTT (cliente, mensajes entrantes) viven en
``app`` porque solo los usa ella.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SerialLine:
    """Una línea cruda recibida del Arduino (sin el terminador) y el instante UTC en que llegó al gateway."""

    data: bytes
    received_at: datetime


@dataclass(frozen=True, slots=True)
class LinkUp:
    """El puerto serie se ha abierto (o reabierto tras un corte)."""

    at: datetime


@dataclass(frozen=True, slots=True)
class LinkDown:
    """El puerto serie se ha perdido o no se pudo abrir; la fuente reintenta por su cuenta."""

    at: datetime
    reason: str


type SerialEvent = SerialLine | LinkUp | LinkDown


class LineSource(Protocol):
    """Fuente de eventos del enlace serie.

    ``events()`` es un generador asíncrono que no termina por sí solo: emite ``LinkUp`` al abrir, ``SerialLine`` por cada línea
    y ``LinkDown`` al perder el puerto, y reintenta la apertura con backoff. Termina cuando se cancela la tarea que lo consume.
    """

    def events(self) -> AsyncIterator[SerialEvent]: ...
