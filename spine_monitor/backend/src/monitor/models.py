"""Bodies of the small requests of the UI API.

Entities (session, self-report, profile, daily context) have no model here: the body is completed
with the fields the app sets and validated against its JSON Schema (`spine_contracts.validation`),
which is the source of truth. These models only cover the protocol steps.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal["rep_start", "rep_end", "mvc_start", "mvc_end", "rest_start", "rest_end", "pain_reported", "artifact", "note"]
StopReason = Literal["completed", "aborted_by_user", "aborted_pain", "aborted_other"]
CalibrationType = Literal["stretch", "emg_mvc", "imu"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StopBody(_Body):
    reason: StopReason = "completed"


class EventBody(_Body):
    event_type: EventType
    rep_index: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=200)


class CalibrateBody(_Body):
    calibration_type: CalibrationType
    stream_ids: list[str] = Field(min_length=1)
    duration_s: float | None = Field(default=None, gt=0)
