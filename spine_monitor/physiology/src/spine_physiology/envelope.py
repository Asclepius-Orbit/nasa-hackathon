"""Common envelope for everything the core produces: a contract message ready for a sink.

The core (generator, gateway) only produces `Envelope`s; the sinks decide what to do with them:
JSONL writes all of them, MQTT publishes only those carrying a `topic` (the other domain objects never go on the bus).
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


# Kinds that travel over MQTT; the rest are domain objects (JSONL only). Calibrations go retained
# (the current one of each stream); see `RETAINED_KINDS`.
MQTT_KINDS = frozenset({EnvelopeKind.STREAM_CHUNK, EnvelopeKind.DEVICE_STATUS, EnvelopeKind.CLOCK_SYNC, EnvelopeKind.COMMAND_ACK, EnvelopeKind.CALIBRATION})

# Kinds that are always published retained.
RETAINED_KINDS = frozenset({EnvelopeKind.CALIBRATION})


@dataclass(frozen=True, slots=True)
class Envelope:
    """An emitted message.

    - `payload`: JSON-ready dict, already passed through `spine_contracts.messages.dump` (or a free dict for
      ground truth, which has no schema). Not modified after the envelope is created.
    - `topic`: MQTT topic for the `MQTT_KINDS` kinds; None for domain objects.
    - `at`: UTC instant (aware) at which the message exists/would be published; orders the output and the pacing.
    - `retained`: publish as retained (DeviceStatus, ClockSync and always the calibrations).
    - `day`: mission day index (0 = first simulated day); None for the profile.
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
        if self.kind in RETAINED_KINDS and not self.retained:
            raise ValueError(f"{self.kind} envelopes must be retained")

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
        """Envelope from a contract model (serialized with `dump`)."""
        return cls(kind=kind, topic=topic, payload=dump(model), at=at, retained=retained, astronaut_id=astronaut_id, day=day)
