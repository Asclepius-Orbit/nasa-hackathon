"""Sobre común de todo lo que produce el núcleo: un mensaje de contrato listo para un sink.

El núcleo (generator, gateway) solo produce `Envelope`s; los sinks deciden qué hacer con ellos:
JSONL escribe todos, MQTT publica solo los que llevan `topic` (los objetos de dominio nunca van al bus).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from spine_contracts import dump


class EnvelopeKind(StrEnum):
    STREAM_CHUNK = "stream_chunk"
    DEVICE_STATUS = "device_status"
    CLOCK_SYNC = "clock_sync"
    COMMAND_ACK = "command_ack"
    SESSION = "session"
    CALIBRATION = "calibration"
    SELF_REPORT = "self_report"
    DAILY_CONTEXT = "daily_context"
    PROFILE = "profile"
    GROUND_TRUTH = "ground_truth"


# Tipos que viajan por MQTT; el resto son objetos de dominio (solo JSONL).
MQTT_KINDS = frozenset({EnvelopeKind.STREAM_CHUNK, EnvelopeKind.DEVICE_STATUS, EnvelopeKind.CLOCK_SYNC, EnvelopeKind.COMMAND_ACK})


@dataclass(frozen=True, slots=True)
class Envelope:
    """Un mensaje emitido.

    - `payload`: dict JSON-ready, ya pasado por `spine_contracts.messages.dump` (o dict libre para
      ground truth, que no tiene schema). No se modifica después de crear el sobre.
    - `topic`: topic MQTT para los tipos de `MQTT_KINDS`; None para los objetos de dominio.
    - `at`: instante UTC (aware) en que el mensaje existe/se publicaría; ordena la salida y el ritmo.
    - `retained`: publicar como retained (DeviceStatus).
    - `day`: índice de día de misión (0 = primer día simulado); None para el perfil.
    """

    kind: EnvelopeKind
    topic: str | None
    payload: dict[str, Any]
    at: datetime
    retained: bool
    astronaut_id: str
    day: int | None

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() != timedelta(0):
            raise ValueError(f"Envelope.at must be an aware UTC datetime, got {self.at!r}")
        if (self.topic is not None) != (self.kind in MQTT_KINDS):
            raise ValueError(f"{self.kind} envelopes {'need' if self.kind in MQTT_KINDS else 'must not have'} a topic")

    @classmethod
    def of(
        cls,
        kind: EnvelopeKind,
        model: BaseModel,
        *,
        at: datetime,
        astronaut_id: str,
        day: int | None,
        topic: str | None = None,
        retained: bool = False,
    ) -> Envelope:
        """Sobre a partir de un modelo de contrato (se serializa con `dump`)."""
        return cls(kind=kind, topic=topic, payload=dump(model), at=at, retained=retained, astronaut_id=astronaut_id, day=day)
