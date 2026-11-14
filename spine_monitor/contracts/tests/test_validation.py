import json
import subprocess
import sys
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


def test_schemas_dir_is_found_and_has_every_schema():
    assert (validation.SCHEMAS_DIR / "common.schema.json").is_file()
    assert (validation.SCHEMAS_DIR / "engine" / "assess-request.schema.json").is_file()


def test_importing_the_package_does_not_load_jsonschema():
    # jsonschema pulls rfc3987_syntax (~1.4 s); the MCP server imports spine_contracts but never validates at startup.
    code = "import sys, spine_contracts; print(sorted(m for m in ('jsonschema', 'rfc3987_syntax', 'referencing') if m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"


def test_session_phase_rules_are_enforced_at_the_boundary():
    # A spine check brackets an exercise: phase and related_session_id belong to spine checks only, and the
    # related session needs its phase. Pydantic cannot see these if/then rules; validation.errors can.
    exercise = example("domain/session.exercise")
    assert validation.errors("domain/session", exercise) == []
    assert validation.errors("domain/session", exercise | {"phase": "pre"}) != []

    post = example("domain/session.spine-check-post")
    assert validation.errors("domain/session", post) == []
    assert validation.errors("domain/session", {k: v for k, v in post.items() if k != "phase"}) != []
    assert validation.errors("domain/session", post | {"planned_sets": 4}) != []
