import json
from pathlib import Path
from re import fullmatch

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012
from spine_contracts.catalog import expand_ids, load_catalog, placeholders

CONTRACTS_DIR = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = CONTRACTS_DIR / "schemas" / "v1"
CATALOG_DIR = CONTRACTS_DIR / "catalog"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def schema_files() -> list[Path]:
    return sorted(SCHEMAS_DIR.rglob("*.schema.json"))


@pytest.fixture(scope="session")
def schemas() -> dict[Path, dict]:
    return {path: load_json(path) for path in schema_files()}


@pytest.fixture(scope="session")
def registry(schemas) -> Registry:
    registry = Registry()
    for schema in schemas.values():
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema, DRAFT202012))
    return registry


@pytest.fixture(scope="session")
def validator_for(schemas, registry):
    """validator_for('telemetry/stream-chunk') -> validator with every $ref resolvable."""

    def build(name: str) -> Draft202012Validator:
        path = SCHEMAS_DIR / f"{name}.schema.json"
        return Draft202012Validator(schemas[path], registry=registry, format_checker=FormatChecker())

    return build


SCHEMA_IDS = [path.relative_to(SCHEMAS_DIR).as_posix() for path in schema_files()]


def iter_refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_refs(item)


@pytest.mark.parametrize("rel", SCHEMA_IDS)
def test_schema_is_valid_2020_12(rel):
    Draft202012Validator.check_schema(load_json(SCHEMAS_DIR / rel))


@pytest.mark.parametrize("rel", SCHEMA_IDS)
def test_schema_id_matches_path(rel):
    schema = load_json(SCHEMAS_DIR / rel)
    assert schema["$id"] == f"https://schemas.asclepius-orbit.example/v1/{rel}"


@pytest.mark.parametrize("rel", SCHEMA_IDS)
def test_every_ref_resolves(rel, schemas, registry):
    schema = schemas[SCHEMAS_DIR / rel]
    resolver = registry.resolver(schema["$id"])
    for ref in set(iter_refs(schema)):
        try:
            resolver.lookup(ref)
        except Unresolvable as error:
            pytest.fail(f"{rel}: unresolvable $ref {ref!r}: {error}")


# ---- examples -------------------------------------------------------------


def example_files(kind: str):
    return sorted((EXAMPLES_DIR / kind).rglob("*.json"))


def schema_name(path, kind: str) -> str:
    folder = path.parent.relative_to(EXAMPLES_DIR / kind).as_posix()
    stem = path.name.split(".")[0]
    return f"{folder}/{stem}" if folder != "." else stem


@pytest.mark.parametrize("path", example_files("valid"), ids=lambda p: p.name)
def test_valid_examples_validate(path, validator_for):
    validator = validator_for(schema_name(path, "valid"))
    errors = sorted(validator.iter_errors(load_json(path)), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


# Every defect must be caught: mutate a valid example in place and expect rejection.
# (example, why it must fail, mutation)
MUTATIONS = [
    ("telemetry/stream-chunk.emg", "wrong EMG unit", lambda d: d["units"].update(emg="V")),
    ("telemetry/stream-chunk.emg", "naive timestamp", lambda d: d.update(received_at="2026-11-13T10:03:04.310")),
    ("telemetry/stream-chunk.emg", "unknown property", lambda d: d.update(foo=1)),
    ("telemetry/stream-chunk.emg", "unknown signal_kind", lambda d: d.update(signal_kind="filtered")),
    ("telemetry/stream-chunk.emg", "zero sample rate", lambda d: d.update(sample_rate_hz=0)),
    ("telemetry/stream-chunk.emg", "missing quality", lambda d: d.pop("quality")),
    ("telemetry/stream-chunk.emg", "quality score above 1", lambda d: d["quality"]["emg"].update(score=1.1)),
    ("telemetry/stream-chunk.emg", "quality without assessor", lambda d: d["quality"]["emg"].pop("assessed_by")),
    ("telemetry/stream-chunk.emg", "unknown quality flag", lambda d: d["quality"]["emg"].update(flags=["smelly"])),
    ("telemetry/stream-chunk.emg", "kind says imu but data is emg", lambda d: d.update(stream_kind="imu")),
    ("telemetry/stream-chunk.emg", "no session", lambda d: d.pop("session_id")),
    ("telemetry/stream-chunk.emg", "boot_id beyond 32 bits", lambda d: d.update(boot_id=2**32)),
    ("telemetry/stream-chunk.imu", "missing gyro axis", lambda d: d["data"].pop("gyro_z")),
    ("telemetry/stream-chunk.imu", "wrong gyro unit", lambda d: d["units"].update(gyro_x="rad/s")),
    ("telemetry/stream-chunk.imu", "invalid accel range", lambda d: d["config"].update(accel_range_g=3)),
    ("telemetry/stream-chunk.stretch", "derived length without calibration", lambda d: d.pop("calibration_id")),
    (
        "telemetry/stream-chunk.stretch-raw",
        "length added without calibration",
        lambda d: (d["data"].update(length_mm=[400.0, 400.0, 400.0]), d["units"].update(length_mm="mm")),
    ),
    ("telemetry/stream-chunk.stretch", "only derived values, no ADC counts", lambda d: d["data"].pop("adc_counts")),
    ("telemetry/clock-sync.round-trip", "real name instead of pseudonym", lambda d: d.update(astronaut_id="Neil Armstrong")),
    ("telemetry/clock-sync.round-trip", "other major version", lambda d: d.update(schema_version="2.0.0")),
    ("telemetry/device-status.degraded", "battery above 100", lambda d: d.update(battery_pct=101)),
    ("telemetry/command.start-session", "unknown session type", lambda d: d.update(session_type="other")),
    ("telemetry/command.mark-event", "unknown event", lambda d: d.update(event_type="dance")),
    ("telemetry/command.calibrate", "no streams to calibrate", lambda d: d.update(stream_ids=[])),
    ("telemetry/command.stop-session", "unknown command type", lambda d: d.update(type="reboot")),
    ("telemetry/command-ack.rejected", "rejection without error", lambda d: d.pop("error")),
    ("telemetry/command-ack.rejected", "unknown error code", lambda d: d["error"].update(code="oops")),
    ("domain/session.exercise", "exercise without declared load", lambda d: d.pop("load_declared")),
    ("domain/session.exercise", "EMG placement without side", lambda d: d["streams"][0]["placement"].pop("side")),
    ("domain/session.exercise", "IMU placement without level", lambda d: d["streams"][1].update(placement={"spine_region": "lumbar"})),
    ("domain/session.exercise", "stretch placement without region", lambda d: d["streams"][2].update(placement={"spine_level": "l3"})),
    ("domain/session.exercise", "no streams", lambda d: d.update(streams=[])),
    ("domain/session.exercise", "event without timestamp", lambda d: d["events"][0].pop("timestamp")),
    ("domain/calibration.stretch", "curve with a single point", lambda d: d["curve"].update(points=d["curve"]["points"][:1])),
    ("domain/calibration.stretch", "zero rest length", lambda d: d.update(rest_length_mm=0)),
    ("domain/calibration.emg-mvc", "negative MVC", lambda d: d.update(mvc_rms_mv=-1)),
    ("domain/calibration.imu", "invalid gyro range", lambda d: d.update(gyro_range_dps=300)),
    ("domain/calibration.imu", "mounting axis not anatomical", lambda d: d["mounting"].update(sensor_x="north")),
    ("domain/astronaut-profile", "unknown sex", lambda d: d.update(sex_at_birth="x")),
    ("domain/astronaut-profile", "no date of birth", lambda d: d.pop("date_of_birth")),
    ("domain/astronaut-profile", "age stored instead of derived", lambda d: d.update(age_years=38)),
    ("domain/astronaut-profile", "absurd height", lambda d: d["anthropometry"].update(height_standing_cm=17)),
    ("domain/self-report.daily", "pain above 10", lambda d: d.update(pain_nrs=11)),
    ("domain/self-report.daily", "unknown red flag", lambda d: d.update(red_flags=["sad"])),
    ("domain/self-report.red-flag", "red flag report with no flags", lambda d: d.update(red_flags=[])),
    ("domain/self-report.weekly-odi", "ODI item above 5", lambda d: d["items"][0].update(score=6)),
    ("domain/daily-context", "no gravity environment", lambda d: d.pop("gravity_environment")),
    ("domain/feature-set.preflight", "malformed feature id", lambda d: d["features"][0].update(feature_id="RMS")),
    ("domain/feature-set.preflight", "feature without quality", lambda d: d["features"][0].pop("quality")),
    ("domain/baseline", "entry without MAD", lambda d: d["entries"][0].pop("mad")),
    ("domain/baseline", "entry without gravity environment", lambda d: d["entries"][0].pop("gravity_environment")),
    ("domain/assessment.amber", "risk score above 1", lambda d: d.update(risk_score=1.5)),
    ("domain/assessment.amber", "escalation required without urgency", lambda d: d.update(escalation={"required": True, "reasons": []})),
    ("domain/assessment.amber", "no confidence", lambda d: d.pop("confidence")),
    ("domain/assessment.amber", "bad recommendation code", lambda d: d.update(recommendations=["rest"])),
    ("domain/assessment.amber", "bad rule id", lambda d: d["fired_rules"][0].update(rule_id="pain")),
    ("engine/features-request", "no chunks", lambda d: d.update(chunks=[])),
    ("engine/baseline-request", "no reference sessions", lambda d: d.update(feature_sets=[])),
    ("engine/assess-request", "no baseline", lambda d: d.pop("baseline")),
    ("engine/simulate-request", "no overrides property", lambda d: d.pop("overrides")),
    ("engine/simulate-request", "override that is not an object", lambda d: d.update(overrides=[1])),
    ("engine/simulate-request", "unknown override mode", lambda d: d["overrides"][0].update(mode="multiply")),
    ("engine/simulate-response", "scenario not flagged hypothetical", lambda d: d.update(hypothetical=False)),
    ("engine/error", "error status below 400", lambda d: d.update(status=200)),
    ("app/status", "no freshness", lambda d: d.pop("freshness")),
    ("app/status", "stale is not a boolean", lambda d: d["freshness"][0].update(stale="no")),
    ("app/status", "negative age", lambda d: d["freshness"][0].update(age_hours=-1)),
    ("app/status", "device freshness without device id", lambda d: d["freshness"][3].pop("device_id")),
    ("app/status", "unknown freshness kind", lambda d: d["freshness"][0].update(what="mood")),
    ("app/status", "latest assessment omitted instead of null", lambda d: d.pop("latest_assessment")),
    ("app/status", "embedded assessment without confidence", lambda d: d["latest_assessment"].pop("confidence")),
    ("app/status", "baseline without version", lambda d: d["active_baseline"].pop("version")),
    ("app/history", "no truncated flag", lambda d: d.pop("truncated")),
    ("app/history", "limit above 500", lambda d: d.update(limit=501)),
    ("app/history", "limit zero", lambda d: d.update(limit=0)),
    ("app/history", "naive window start", lambda d: d.update({"from": "2027-02-02T00:00:00"})),
    ("app/history", "no assessments collection", lambda d: d.pop("assessments")),
    ("app/explanation", "no assessment", lambda d: d.pop("assessment")),
    ("app/explanation", "unresolved item of unknown kind", lambda d: d.update(unresolved=[{"kind": "mood", "id": d["baseline"]["baseline_id"]}])),
    ("app/explanation", "baseline entry without MAD", lambda d: d["baseline_entries"][0].pop("mad")),
    ("app/assessment-list", "item that is not an assessment", lambda d: d.update(items=[{"level": "green"}])),
    ("app/assessment-list", "unknown property", lambda d: d.update(page=2)),
    ("app/session-list", "no items", lambda d: d.pop("items")),
    ("app/self-report-list", "limit not an integer", lambda d: d.update(limit=10.5)),
    ("app/error.not-found", "unknown problem type", lambda d: d.update(type="teapot")),
    ("app/error.not-found", "error status below 400", lambda d: d.update(status=200)),
    ("domain/astronaut-profile", "launch date that is not a date", lambda d: d.update(launch_date="L-30")),
    ("domain/astronaut-profile", "landing date with a time", lambda d: d.update(landing_date="2027-05-20T10:00:00Z")),
    # UI views: codes and data only, absence is null or 'nodata', never omitted or defaulted.
    ("app/ui-strip", "mission omitted instead of nulls", lambda d: d.pop("mission")),
    ("app/ui-strip", "unknown mission phase", lambda d: d["mission"].update(phase="cruise")),
    ("app/ui-strip", "mission day omitted instead of null", lambda d: d["mission"].pop("day")),
    ("app/ui-strip", "battery above 100", lambda d: d["harness"].update(battery_pct=101)),
    ("app/ui-strip", "active stream listed as weak", lambda d: d["harness"]["weak_streams"][0].update(state="active")),
    ("app/ui-strip", "prose note instead of codes", lambda d: d["harness"].update(note="EMG right weak")),
    ("app/ui-strip", "ground link omitted instead of null", lambda d: d.pop("ground_link")),
    ("app/ui-strip", "negative minutes since contact", lambda d: d.update(last_contact_min_ago=-1)),
    ("app/ui-strip", "reports due as a count", lambda d: d.update(reports_due=1)),
    ("app/ui-strip", "harness without stale flag", lambda d: d["harness"].pop("stale")),
    ("app/ui-strip", "harness stale as a string", lambda d: d["harness"].update(stale="no")),
    ("app/ui-strip", "harness without last seen instant", lambda d: d["harness"].pop("last_seen_at")),
    ("app/ui-strip", "harness last seen without offset", lambda d: d["harness"].update(last_seen_at="2026-11-13T10:00:00")),
    ("app/ui-strip.unpaired", "harness omitted instead of null", lambda d: d.pop("harness")),
    ("app/ui-today", "unknown level", lambda d: d.update(level="yellow")),
    ("app/ui-today", "prose recommendation", lambda d: d.update(recommendations=["Do the stabilisation set"])),
    ("app/ui-today", "reason without feature id", lambda d: d["reasons"][0].pop("feature_id")),
    ("app/ui-today", "confidence above 1", lambda d: d["trust"].update(confidence=1.2)),
    ("app/ui-today", "unknown missing reason", lambda d: d["trust"]["missing"][0].update(reason="forgot")),
    ("app/ui-today", "unknown task code", lambda d: d.update(also_today=["TASK_DANCE"])),
    ("app/ui-today", "assessed level without assessment id", lambda d: d.update(assessment_id=None)),
    ("app/ui-today", "assessed level without trust facts", lambda d: d.update(trust=None)),
    ("app/ui-today", "lumbar delta omitted instead of null", lambda d: d.pop("lumbar_delta_mm")),
    ("app/ui-today", "lumbar measured_at omitted instead of null", lambda d: d.pop("lumbar_measured_at")),
    ("app/ui-today", "lumbar delta without its measure instant", lambda d: d.update(lumbar_measured_at=None)),
    ("app/ui-today", "measure instant without a lumbar delta", lambda d: d.update(lumbar_delta_mm=None)),
    ("app/ui-today", "measure instant without offset", lambda d: d.update(lumbar_measured_at="2027-02-09T08:00:00")),
    ("app/ui-today.nodata", "measure instant with a null lumbar delta", lambda d: d.update(lumbar_measured_at="2026-11-13T09:00:00Z")),
    ("app/ui-today.nodata", "nodata with an assessment id", lambda d: d.update(assessment_id="a55e55e0-0000-4000-8000-0000000000a1")),
    ("app/ui-today.nodata", "nodata with trust facts", lambda d: d.update(trust={"confidence": 1, "data_available": 0, "data_total": 0, "missing": []})),
    ("app/ui-today.nodata", "nodata with recommendations", lambda d: d.update(recommendations=["REC_LUMBAR_STAB_01"])),
    ("app/ui-today.nodata", "session in progress without started_at", lambda d: d["session_in_progress"].pop("started_at")),
    ("app/ui-history", "window end that is a timestamp", lambda d: d.update(to="2027-02-09T00:00:00Z")),
    ("app/ui-history", "day level green by default", lambda d: d["days"][0].pop("level")),
    ("app/ui-history", "pain above 10", lambda d: d["days"][0].update(pain_nrs=11)),
    ("app/ui-history", "length omitted instead of null", lambda d: d["days"][0].pop("length_delta_mm")),
    ("app/ui-history", "unknown session state", lambda d: d["days"][1]["session"].update(state="paused")),
    ("app/ui-history", "unknown report kind", lambda d: d["days"][1]["report"].update(kind="monthly")),
    ("app/ui-history", "unknown red flag", lambda d: d["days"][1]["report"].update(red_flags=["sad"])),
    ("app/ui-history", "range without upper bound", lambda d: d["preflight_range_mm"].pop("hi")),
    ("app/ui-baseline", "zero sessions required", lambda d: d.update(required=0)),
    ("app/ui-baseline", "reference version zero", lambda d: d.update(reference_version=0)),
    ("app/ui-baseline", "unknown session status", lambda d: d["sessions"][0].update(status="skipped")),
    ("app/ui-baseline", "complete session without id", lambda d: d["sessions"][0].update(session_id=None)),
    ("app/ui-baseline", "offset as a label", lambda d: d["sessions"][0].update(offset_days="L-30")),
    ("app/ui-first-day", "unknown task code", lambda d: d.update(todo=["TASK_PROFILE_NAME"])),
    ("app/ui-first-day", "crew name in the view", lambda d: d.update(crew_first_name="Ana")),
    ("app/ui-first-day", "baseline progress omitted", lambda d: d.pop("baseline")),
    ("app/ui-session", "astronaut id omitted", lambda d: d.pop("astronaut_id")),
    ("app/ui-session", "astronaut id that is not a pseudonym", lambda d: d.update(astronaut_id="Ana")),
    ("app/ui-session", "four steps", lambda d: d.update(steps=d["steps"][:4])),
    ("app/ui-session", "steps out of order", lambda d: d.update(steps=[d["steps"][1], d["steps"][0], *d["steps"][2:]])),
    ("app/ui-session", "unknown step status", lambda d: d["steps"][0].update(status="skipped")),
    ("app/ui-session", "unknown sensor check status", lambda d: d["sensor_checks"][0].update(status="ok")),
    ("app/ui-session", "unknown stream state", lambda d: d["sensor_checks"][0].update(state="sleeping")),
    ("app/ui-session", "band without load", lambda d: d["band"].pop("nominal_kg")),
    ("app/ui-session", "fit item that is prose", lambda d: d.update(fit_items=["Clean the skin"])),
    ("app/ui-session", "unacked calibration with an ack time", lambda d: d["calibration"].update(acked=False)),
    ("app/ui-session", "unknown session state", lambda d: d.update(state="paused")),
]


@pytest.mark.parametrize("case", MUTATIONS, ids=lambda case: f"{case[0]}: {case[1]}")
def test_mutated_example_is_rejected(case, validator_for):
    name, _why, mutate = case
    document = load_json(EXAMPLES_DIR / "valid" / f"{name}.json")
    validator = validator_for(name.split(".")[0])
    assert validator.is_valid(document), "the unmodified example must be valid"
    mutate(document)
    assert not validator.is_valid(document)


def test_feature_set_accepts_calibration_expired_as_skip_reason(validator_for):
    document = load_json(EXAMPLES_DIR / "valid" / "domain" / "feature-set.preflight.json")
    document["skipped"].append({"feature_id": "stretch.lumbar.peak_elongation_mm", "reason": "calibration_expired"})
    assert validator_for("domain/feature-set").is_valid(document)
    document["skipped"][-1]["reason"] = "calibration_stale"
    assert not validator_for("domain/feature-set").is_valid(document)


def test_feature_set_accepts_insufficient_sample_rate_as_skip_reason(validator_for):
    # SPARC needs a fast enough IMU (#44): a slower stream skips it with its own reason
    document = load_json(EXAMPLES_DIR / "valid" / "domain" / "feature-set.preflight.json")
    document["skipped"].append({"feature_id": "imu.l3.sagittal.smoothness_sparc", "reason": "insufficient_sample_rate"})
    assert validator_for("domain/feature-set").is_valid(document)
    document["skipped"][-1]["reason"] = "sample_rate_too_low"
    assert not validator_for("domain/feature-set").is_valid(document)


def test_astronaut_profile_accepts_optional_mission_dates(validator_for):
    # launch/landing dates are optional and nullable: profiles written before they existed stay valid.
    validator = validator_for("domain/astronaut-profile")
    document = load_json(EXAMPLES_DIR / "valid" / "domain" / "astronaut-profile.json")
    assert "launch_date" not in document and "landing_date" not in document
    assert validator.is_valid(document)
    assert validator.is_valid(document | {"launch_date": "2026-12-01", "landing_date": "2027-05-20"})
    assert validator.is_valid(document | {"launch_date": None, "landing_date": None})


def test_spine_check_needs_no_declared_load(validator_for):
    # exercise sessions require a declared load; passive spine checks do not
    document = load_json(EXAMPLES_DIR / "valid" / "domain" / "session.spine-check.json")
    assert validator_for("domain/session").is_valid(document)


# Engine examples embed the standalone ones: they must not drift apart.


def valid_example(rel: str):
    return load_json(EXAMPLES_DIR / "valid" / f"{rel}.json")


def test_engine_examples_embed_the_standalone_examples():
    features = valid_example("engine/features-request")
    assert features["session"] == valid_example("domain/session.exercise")
    assert features["chunks"] == [valid_example(f"telemetry/stream-chunk.{kind}") for kind in ("emg", "imu", "stretch")]
    assert features["calibrations"] == [valid_example(f"domain/calibration.{kind}") for kind in ("stretch", "emg-mvc", "imu")]
    assert features["clock_syncs"] == [valid_example("telemetry/clock-sync.round-trip")]
    assert features["profile"] == valid_example("domain/astronaut-profile")

    assert valid_example("engine/baseline-request")["feature_sets"] == [valid_example("domain/feature-set.preflight")]

    assess = valid_example("engine/assess-request")
    assert assess["profile"] == valid_example("domain/astronaut-profile")
    assert assess["baseline"] == valid_example("domain/baseline")
    assert assess["feature_sets"] == [valid_example("domain/feature-set.inflight")]
    assert assess["self_reports"] == [valid_example("domain/self-report.daily")]
    assert assess["daily_context"] == [valid_example("domain/daily-context")]

    assert valid_example("engine/simulate-request")["base"] == assess
    no_overrides = valid_example("engine/simulate-request.no-overrides")
    assert no_overrides["base"] == assess
    assert no_overrides["overrides"] == []
    assert valid_example("engine/simulate-response")["reference"] == valid_example("domain/assessment.amber")


def test_app_examples_embed_the_standalone_examples():
    assessment = valid_example("domain/assessment.amber")
    session = valid_example("domain/session.exercise")
    report = valid_example("domain/self-report.daily")
    featureset = valid_example("domain/feature-set.inflight")
    baseline = valid_example("domain/baseline")

    status = valid_example("app/status")
    assert status["latest_assessment"] == assessment
    assert status["latest_session"] == session
    assert status["latest_self_report"] == report
    assert status["devices"] == [valid_example("telemetry/device-status.degraded")]
    assert status["active_baseline"]["baseline_id"] == baseline["baseline_id"]
    assert status["today"]["date"] == valid_example("domain/daily-context")["date"]

    history = valid_example("app/history")
    assert history["feature_sets"] == [featureset]
    assert history["assessments"] == [assessment]

    explanation = valid_example("app/explanation")
    assert explanation["assessment"] == assessment
    assert explanation["feature_sets"] == [featureset]
    assert explanation["self_reports"] == [report]
    assert explanation["baseline_entries"] == [e for e in baseline["entries"] if e["feature_id"] in {d["feature_id"] for d in assessment["drivers"]}]

    assert valid_example("app/assessment-list")["items"] == [assessment]
    assert valid_example("app/session-list")["items"] == [session]
    assert valid_example("app/self-report-list")["items"] == [report]


def test_explanation_resolves_everything_the_assessment_is_based_on():
    explanation = valid_example("app/explanation")
    based_on = explanation["assessment"]["based_on"]

    assert explanation["baseline"] == {"baseline_id": based_on["baseline_id"], "version": based_on["baseline_version"]}
    assert [f["session_id"] for f in explanation["feature_sets"]] == based_on["session_ids"]
    assert [r["report_id"] for r in explanation["self_reports"]] == based_on["self_report_ids"]
    assert explanation["unresolved"] == []


def test_status_freshness_is_consistent():
    status = valid_example("app/status")
    for item in status["freshness"]:
        assert (item["last_at"] is None) == (item["age_hours"] is None), item
        if item["last_at"] is None:
            assert item["stale"], "missing data must be reported as stale, never as fresh"


def test_session_streams_and_calibrations_are_consistent():
    session = valid_example("domain/session.exercise")
    calibrations = {c["calibration_id"]: c for c in (valid_example(f"domain/calibration.{kind}") for kind in ("stretch", "emg-mvc", "imu"))}
    for stream in session["streams"]:
        assert calibrations[stream["calibration_id"]]["stream_id"] == stream["stream_id"]
    chunk_streams = {valid_example(f"telemetry/stream-chunk.{kind}")["stream_id"] for kind in ("emg", "imu", "stretch")}
    assert chunk_streams == {stream["stream_id"] for stream in session["streams"]}


def test_ui_views_are_derived_from_the_standalone_examples():
    # The views carry codes and data taken from the domain objects, never a reworded copy.
    assessment = valid_example("domain/assessment.amber")
    today = valid_example("app/ui-today")
    assert today["level"] == assessment["level"]
    assert today["assessment_id"] == assessment["assessment_id"]
    assert today["assessed_at"] == assessment["as_of"]
    assert today["recommendations"] == assessment["recommendations"]
    assert today["reasons"] == [
        {"feature_id": d["feature_id"], "deviation_z": d.get("deviation_z"), "contribution": d["contribution"]} for d in assessment["drivers"]
    ]
    assert today["trust"]["confidence"] == assessment["confidence"]
    assert today["trust"]["missing"] == assessment["completeness"]["missing"]
    assert 0 <= today["trust"]["data_available"] <= today["trust"]["data_total"]

    device = valid_example("telemetry/device-status.degraded")
    strip = valid_example("app/ui-strip")
    assert strip["harness"]["device_id"] == device["device_id"]
    assert strip["harness"]["battery_pct"] == device["battery_pct"]
    assert strip["harness"]["degraded"] == (device["status"] == "degraded")
    assert strip["harness"]["weak_streams"] == [
        {"stream": s["stream_id"], "state": s["state"]} for s in device["streams"] if s["state"] in ("stalled", "disabled")
    ]

    assert valid_example("app/ui-first-day")["baseline"] == valid_example("app/ui-baseline")


def test_ui_nodata_views_carry_no_assessment():
    today = valid_example("app/ui-today.nodata")
    assert today["level"] == "nodata"
    assert today["assessment_id"] is None and today["trust"] is None
    assert today["recommendations"] == [] and today["reasons"] == []
    assert today["lumbar_delta_mm"] is None


def test_ui_history_days_are_ordered_and_inside_the_window():
    for name in ("app/ui-history", "app/ui-history.empty"):
        history = valid_example(name)
        dates = [day["date"] for day in history["days"]]
        assert dates == sorted(dates), name
        assert all(history["from"] <= d <= history["to"] for d in dates), name
        range_mm = history["preflight_range_mm"]
        assert range_mm is None or range_mm["lo"] <= range_mm["hi"], name


def test_ui_baseline_counts_are_consistent():
    for name in ("app/ui-baseline", "app/ui-baseline.empty"):
        baseline = valid_example(name)
        assert baseline["recorded"] == sum(s["status"] == "complete" for s in baseline["sessions"]), name
        assert (baseline["reference_version"] is None) == (baseline["built_from_sessions"] == 0), name


# Schemas that only hold shared $defs; they are never a message by themselves.
DEFINITIONS_ONLY = {"common.schema.json", "app/ui-common.schema.json"}


def test_every_schema_has_a_valid_example():
    covered = {schema_name(path, "valid") for path in example_files("valid")}
    missing = [rel for rel in SCHEMA_IDS if rel.removesuffix(".schema.json") not in covered and rel not in DEFINITIONS_ONLY]
    assert not missing, f"no valid example for: {missing}"


def test_definitions_only_schemas_have_no_root_type():
    for rel in DEFINITIONS_ONLY:
        schema = load_json(SCHEMAS_DIR / rel)
        assert "$defs" in schema and not {"type", "oneOf", "anyOf", "allOf", "properties"} & schema.keys(), rel


# ---- semantic checks the schema language cannot express ---------------------


def chunks_in(document):
    if isinstance(document, dict):
        if "stream_kind" in document and "data" in document and "n_samples" in document:
            yield document
        for value in document.values():
            yield from chunks_in(value)
    elif isinstance(document, list):
        for item in document:
            yield from chunks_in(item)


@pytest.mark.parametrize("path", example_files("valid"), ids=lambda p: p.name)
def test_chunk_arrays_match_n_samples(path):
    for chunk in chunks_in(load_json(path)):
        for channel, values in chunk["data"].items():
            assert len(values) == chunk["n_samples"], f"{path.name}: {channel} has {len(values)} samples, n_samples={chunk['n_samples']}"


# ---- feature catalog --------------------------------------------------------


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def common():
    return load_json(SCHEMAS_DIR / "common.schema.json")


def test_catalog_matches_its_schema(catalog):
    Draft202012Validator(load_json(CATALOG_DIR / "feature-catalog.schema.json")).validate(catalog)


def test_catalog_ids_are_unique(catalog):
    ids = [entry["feature_id"] for entry in catalog["entries"]]
    assert len(ids) == len(set(ids))


def test_catalog_placeholders_resolve_and_expand_to_valid_ids(catalog, common):
    pattern = common["$defs"]["feature_id"]["pattern"]
    for entry in catalog["entries"]:
        for name in placeholders(entry["feature_id"]):
            assert name in common["$defs"], f"{entry['feature_id']}: unknown placeholder {name}"
        for expanded in expand_ids(entry):
            assert fullmatch(pattern, expanded), expanded


def test_catalog_has_no_invented_ranges(catalog):
    # Ranges belong to the clinical owner; a non-null range must carry its source.
    for entry in catalog["entries"]:
        if entry["ranges"] is not None:
            assert entry["status"] == "validated", entry["feature_id"]


def test_example_feature_ids_exist_in_catalog(catalog, common):
    known = {expanded for entry in catalog["entries"] for expanded in expand_ids(entry)}

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "feature_id" and isinstance(value, str):
                    yield value
                else:
                    yield from walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)

    for path in example_files("valid"):
        for feature_id in walk(json.loads(path.read_text(encoding="utf-8"))):
            assert feature_id in known, f"{path.name}: {feature_id} not in catalog"
