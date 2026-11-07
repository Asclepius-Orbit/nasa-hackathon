import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from spine_contracts import (
    SCHEMA_VERSION,
    AppErrorResponse,
    Assessment,
    AssessmentList,
    AssessRequest,
    AstronautProfile,
    Baseline,
    BaselineRequest,
    BaselineView,
    ClockSync,
    CommandAck,
    ComparePeriods,
    DailyContext,
    DeviceStatus,
    ErrorResponse,
    Explanation,
    FeatureSet,
    FeaturesRequest,
    FirstDayView,
    History,
    HistoryView,
    SelfReportList,
    Session,
    SessionList,
    SessionView,
    SimulateRequest,
    SimulateResponse,
    Status,
    StripView,
    TodayView,
    calibration_adapter,
    command_adapter,
    dump,
    self_report_adapter,
    stream_chunk_adapter,
    validation,
)

CONTRACTS_DIR = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = CONTRACTS_DIR / "examples" / "valid"
MODELS_DIR = CONTRACTS_DIR / "src" / "spine_contracts" / "models"

# schema name -> function that parses a payload into the Pydantic model
PARSERS = {
    "telemetry/stream-chunk": stream_chunk_adapter.validate_python,
    "telemetry/command": command_adapter.validate_python,
    "telemetry/command-ack": CommandAck.model_validate,
    "telemetry/clock-sync": ClockSync.model_validate,
    "telemetry/device-status": DeviceStatus.model_validate,
    "domain/session": Session.model_validate,
    "domain/calibration": calibration_adapter.validate_python,
    "domain/astronaut-profile": AstronautProfile.model_validate,
    "domain/self-report": self_report_adapter.validate_python,
    "domain/daily-context": DailyContext.model_validate,
    "domain/feature-set": FeatureSet.model_validate,
    "domain/baseline": Baseline.model_validate,
    "domain/assessment": Assessment.model_validate,
    "engine/features-request": FeaturesRequest.model_validate,
    "engine/baseline-request": BaselineRequest.model_validate,
    "engine/assess-request": AssessRequest.model_validate,
    "engine/simulate-request": SimulateRequest.model_validate,
    "engine/simulate-response": SimulateResponse.model_validate,
    "engine/error": ErrorResponse.model_validate,
    "app/status": Status.model_validate,
    "app/history": History.model_validate,
    "app/explanation": Explanation.model_validate,
    "app/assessment-list": AssessmentList.model_validate,
    "app/session-list": SessionList.model_validate,
    "app/self-report-list": SelfReportList.model_validate,
    "app/error": AppErrorResponse.model_validate,
    "app/ui-strip": StripView.model_validate,
    "app/ui-today": TodayView.model_validate,
    "app/ui-history": HistoryView.model_validate,
    "app/ui-baseline": BaselineView.model_validate,
    "app/ui-first-day": FirstDayView.model_validate,
    "app/ui-session": SessionView.model_validate,
    "mcp/compare-periods": ComparePeriods.model_validate,
}

# RFC 9457 problem details carry no schema_version
PROBLEM_DETAILS = {"engine/error", "app/error"}


def example_files():
    return sorted(EXAMPLES_DIR.rglob("*.json"))


def schema_name(path: Path) -> str:
    return f"{path.parent.relative_to(EXAMPLES_DIR).as_posix()}/{path.name.split('.')[0]}"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_schema_with_a_message_has_a_parser():
    assert {schema_name(path) for path in example_files()} == set(PARSERS)


@pytest.mark.parametrize("path", example_files(), ids=lambda p: p.name)
def test_example_parses_and_dumps_back_to_a_valid_payload(path):
    model = PARSERS[schema_name(path)](load(path))
    payload = dump(model)

    assert validation.errors(schema_name(path), payload) == [], "what the models emit must satisfy the schema"
    assert PARSERS[schema_name(path)](payload) == model, "parse(dump(model)) must give the same model"


def test_generated_models_are_up_to_date(tmp_path):
    spec = importlib.util.spec_from_file_location("generate_models", CONTRACTS_DIR / "scripts" / "generate_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.generate(tmp_path / "models")

    fresh = {p.relative_to(tmp_path / "models"): p.read_text(encoding="utf-8") for p in (tmp_path / "models").rglob("*.py")}
    committed = {p.relative_to(MODELS_DIR): p.read_text(encoding="utf-8") for p in MODELS_DIR.rglob("*.py")}
    assert committed.keys() == fresh.keys(), "different set of generated files"
    stale = [str(name) for name in fresh if fresh[name] != committed[name]]
    assert not stale, f"run `uv run python contracts/scripts/generate_models.py`; stale: {stale}"


def test_examples_use_the_current_schema_version():
    def versions(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "schema_version":
                    yield value
                else:
                    yield from versions(value)
        elif isinstance(node, list):
            for item in node:
                yield from versions(item)

    for path in example_files():
        expected = set() if schema_name(path) in PROBLEM_DETAILS else {SCHEMA_VERSION}
        assert set(versions(load(path))) == expected, path.name


# ---- Pydantic and JSON Schema share the work ---------------------------------


def test_pydantic_rejects_naive_timestamps_and_unknown_fields():
    payload = load(EXAMPLES_DIR / "telemetry" / "clock-sync.round-trip.json")

    with pytest.raises(ValidationError):
        ClockSync.model_validate(payload | {"utc": "2026-11-13T10:03:00.100"})
    with pytest.raises(ValidationError):
        ClockSync.model_validate(payload | {"surprise": 1})


def test_list_items_are_plain_strings_not_wrappers():
    assessment = Assessment.model_validate(load(EXAMPLES_DIR / "domain" / "assessment.amber.json"))

    # constrained list items must stay `str`: str(item) of a RootModel wrapper would read "root='...'"
    assert assessment.based_on.session_ids == ["3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a03"]
    assert f"{assessment.based_on.session_ids[0]}" == "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a03"


def test_conditional_rules_are_enforced_by_the_json_schema_not_by_pydantic():
    # An exercise session without a declared load is structurally fine for Pydantic;
    # only the schema's if/then rule catches it. That is why boundaries call validation.validate().
    payload = load(EXAMPLES_DIR / "domain" / "session.exercise.json")
    del payload["load_declared"]

    Session.model_validate(payload)
    assert validation.errors("domain/session", payload) != []


def test_feature_set_skip_reason_includes_calibration_expired():
    example = json.loads((EXAMPLES_DIR / "domain" / "feature-set.preflight.json").read_text(encoding="utf-8"))
    example["skipped"].append({"feature_id": "stretch.lumbar.peak_elongation_mm", "reason": "calibration_expired"})
    parsed = FeatureSet.model_validate(example)
    assert parsed.skipped[-1].reason.value == "calibration_expired"


def test_view_models_keep_explicit_nulls():
    # Absence travels as an explicit null: dumping a view must not drop a null field.
    payload = load(EXAMPLES_DIR / "app" / "ui-today.nodata.json")
    dumped = dump(TodayView.model_validate(payload))
    assert dumped["assessment_id"] is None and dumped["trust"] is None and dumped["lumbar_delta_mm"] is None


def test_astronaut_profile_model_reads_mission_dates():
    payload = load(EXAMPLES_DIR / "domain" / "astronaut-profile.json")
    profile = AstronautProfile.model_validate(payload | {"launch_date": "2026-12-01", "landing_date": None})
    assert str(profile.launch_date) == "2026-12-01"
    assert profile.landing_date is None
