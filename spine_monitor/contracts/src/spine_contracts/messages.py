"""Discriminated unions for the messages that have several shapes.

The generator drops the root `oneOf` schemas (stream chunk, command, calibration, self-report), so
they are declared here, discriminated by the same constant field the JSON Schema uses.
Parse a payload with the matching adapter: `stream_chunk_adapter.validate_json(raw)`.
"""

from typing import Annotated, Any

from pydantic import BaseModel, Field, TypeAdapter

from spine_contracts.models.domain.calibration_schema import EmgMvcCalibration, ImuCalibration, StretchCalibration
from spine_contracts.models.domain.self_report_schema import DailySelfReport, RedFlagReport, WeeklyOdiReport
from spine_contracts.models.telemetry.command_schema import (
    CalibrateCommand,
    MarkEventCommand,
    StartSessionCommand,
    StopSessionCommand,
)
from spine_contracts.models.telemetry.stream_chunk_schema import EmgChunk, ImuChunk, StretchChunk

StreamChunk = Annotated[EmgChunk | ImuChunk | StretchChunk, Field(discriminator="stream_kind")]
Command = Annotated[
    StartSessionCommand | StopSessionCommand | MarkEventCommand | CalibrateCommand,
    Field(discriminator="type"),
]
Calibration = Annotated[
    StretchCalibration | EmgMvcCalibration | ImuCalibration,
    Field(discriminator="calibration_type"),
]
SelfReport = Annotated[DailySelfReport | WeeklyOdiReport | RedFlagReport, Field(discriminator="kind")]

SCHEMA_VERSION = "1.0.0"


def dump(model: BaseModel) -> dict[str, Any]:
    """JSON-ready dict that conforms to the schema.

    Only fields that were set are emitted: defaults are left out and an explicit None stays `null`,
    which is what the schemas expect (`exclude_none` would drop required nullable fields such as
    `session_id` of a feature set). Build models without passing None for optional fields. Aliases are
    used because the window of the app API is `from`, a Python keyword the models call `from_`.
    """
    return model.model_dump(mode="json", exclude_unset=True, by_alias=True)


stream_chunk_adapter: TypeAdapter[StreamChunk] = TypeAdapter(StreamChunk)
command_adapter: TypeAdapter[Command] = TypeAdapter(Command)
calibration_adapter: TypeAdapter[Calibration] = TypeAdapter(Calibration)
self_report_adapter: TypeAdapter[SelfReport] = TypeAdapter(SelfReport)
