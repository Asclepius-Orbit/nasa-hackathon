import json
from pathlib import Path

import pytest
from jsonschema import FormatChecker
from jsonschema.exceptions import ValidationError

from spine_contracts import validation

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "valid"


def example(rel: str):
    return json.loads((EXAMPLES / f"{rel}.json").read_text(encoding="utf-8"))


def test_date_time_format_is_really_checked():
    # Without the format extras installed jsonschema silently skips `format`, and naive timestamps pass.
    assert "date-time" in FormatChecker().checkers
    payload = example("telemetry/clock-sync.round-trip") | {"utc": "2026-11-13T10:03:00.100"}
    assert validation.errors("telemetry/clock-sync", payload) != []


def test_valid_payload_has_no_errors():
    payload = example("telemetry/stream-chunk.emg")

    assert validation.errors("telemetry/stream-chunk", payload) == []
    validation.validate("telemetry/stream-chunk", payload)


def test_errors_are_readable_and_point_at_the_field():
    payload = example("telemetry/stream-chunk.emg")
    payload["units"]["emg"] = "V"

    problems = validation.errors("telemetry/stream-chunk", payload)

    assert problems
    assert any("/units/emg" in p or "emg" in p for p in problems)


def test_validate_raises_on_conditional_rule():
    payload = example("domain/session.exercise")
    del payload["load_declared"]

    with pytest.raises(ValidationError, match="load_declared"):
        validation.validate("domain/session", payload)


def test_derived_cord_length_needs_its_calibration():
    payload = example("telemetry/stream-chunk.stretch")
    del payload["calibration_id"]

    assert validation.errors("telemetry/stream-chunk", payload) != []


def test_unknown_schema_name():
    with pytest.raises(KeyError):
        validation.validator("domain/nope")
