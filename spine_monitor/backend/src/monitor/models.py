"""Bodies of the small requests of the UI API.

Entities (session, self-report, profile, daily context) have no model here: the body is completed
with the fields the app sets and validated against its JSON Schema (`spine_contracts.validation`),
which is the source of truth. These models only cover the protocol steps.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

EventType = Literal["rep_start", "rep_end", "mvc_start", "mvc_end", "rest_start", "rest_end", "pain_reported", "artifact", "note"]
StopReason = Literal["completed", "aborted_by_user", "aborted_pain", "aborted_other"]
CalibrationType = Literal["stretch", "emg_mvc", "imu"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StopBody(_Body):
    reason: StopReason = "completed"
    # Repetition at which the astronaut stopped for pain; only with `aborted_pain`.
    rep_index: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _rep_only_for_pain(self) -> Self:
        if self.rep_index is not None and self.reason != "aborted_pain":
            raise ValueError("rep_index is only accepted with reason aborted_pain")
        return self


class EventBody(_Body):
    event_type: EventType
    rep_index: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=200)


class CalibrateBody(_Body):
    calibration_type: CalibrationType
    stream_ids: list[str] = Field(min_length=1)
    duration_s: float | None = Field(default=None, gt=0)
