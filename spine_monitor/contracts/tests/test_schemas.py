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


def test_simulate_projection_metadata_contract(validator_for):
    response = load_json(EXAMPLES_DIR / "valid/engine/simulate-response.json")
    response["projection"] = {"project_days": 7, "unprojected_features": ["stretch.full_spine.peak_elongation_mm"]}
    validator = validator_for("engine/simulate-response")
    assert not list(validator.iter_errors(response))
    for invalid in ({"project_days": 0, "unprojected_features": []}, {"project_days": 7}, {"project_days": 7, "unprojected_features": [3]}):
        response["projection"] = invalid
        assert list(validator.iter_errors(response))


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
    ("domain/session.exercise", "IMU placement without level", lambda d: d["streams"][2].update(placement={"spine_region": "thoracic"})),
    ("domain/session.exercise", "stretch placement without region", lambda d: d["streams"][3].update(placement={"spine_level": "t9"})),
    ("domain/session.exercise", "no streams", lambda d: d.update(streams=[])),
    ("domain/session.exercise", "event without timestamp", lambda d: d["events"][0].pop("timestamp")),
    # Standardised exercise protocol (#12): sets, spine_check brackets, EMG electrodes and ground, cord spanning the whole spine.
    ("domain/session.exercise", "zero planned sets", lambda d: d.update(planned_sets=0)),
    ("domain/session.exercise", "zero repetitions per set", lambda d: d.update(reps_per_set=0)),
    ("domain/session.exercise", "phase on an exercise session", lambda d: d.update(phase="pre")),
    (
        "domain/session.exercise",
        "related session on an exercise session",
        lambda d: d.update(related_session_id="3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a02"),
    ),
    ("domain/session.exercise", "unknown set event", lambda d: d["events"][0].update(event_type="set_pause")),
    ("domain/session.exercise", "set index zero", lambda d: d["events"][0].update(set_index=0)),
    ("domain/session.exercise", "unknown property in an event", lambda d: d["events"][0].update(set=1)),
    ("domain/session.exercise", "electrode spacing zero", lambda d: d["streams"][0]["placement"].update(electrode_spacing_cm=0)),
    ("domain/session.exercise", "electrode spacing on an IMU", lambda d: d["streams"][2]["placement"].update(electrode_spacing_cm=10)),
    ("domain/session.exercise", "ground on a stretch cord", lambda d: d["streams"][3]["placement"].update(ground={"location": "iliac_crest"})),
    ("domain/session.exercise", "unknown ground location", lambda d: d["streams"][0]["placement"]["ground"].update(location="forehead")),
    ("domain/session.exercise", "ground without location", lambda d: d["streams"][0]["placement"]["ground"].pop("location")),
    ("domain/session.exercise", "ground with an unknown property", lambda d: d["streams"][0]["placement"]["ground"].update(gel="x")),
    ("domain/session.exercise", "cord level outside the vertebral enum", lambda d: d["streams"][3]["placement"]["span"].update(from_level="c5")),
    ("domain/session.exercise", "unknown spine region", lambda d: d["streams"][3]["placement"].update(spine_region="whole")),
    ("domain/session.spine-check", "unknown spine check phase", lambda d: d.update(phase="during")),
    ("domain/session.spine-check", "related session without its phase", lambda d: d.pop("phase")),
    ("domain/session.spine-check", "related session that is not a uuid", lambda d: d.update(related_session_id="exercise")),
    ("domain/session.spine-check", "planned sets on a spine check", lambda d: d.update(planned_sets=4)),
    ("domain/session.spine-check", "repetitions per set on a spine check", lambda d: d.update(reps_per_set=15)),
    ("domain/session.spine-check-post", "post check that lost its phase", lambda d: d.pop("phase")),
    ("telemetry/command.mark-event", "set index zero", lambda d: d.update(set_index=0)),
    ("telemetry/command.mark-event", "set index that is not an integer", lambda d: d.update(set_index=1.5)),
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
    ("domain/assessment.amber", "evidence without datum", lambda d: d["fired_rules"][0]["evidence"].pop("datum")),
    ("domain/assessment.amber", "evidence without value", lambda d: d["fired_rules"][0]["evidence"].pop("value")),
    ("domain/assessment.amber", "evidence without operator", lambda d: d["fired_rules"][0]["evidence"].pop("operator")),
    ("domain/assessment.amber", "evidence without threshold", lambda d: d["fired_rules"][0]["evidence"].pop("threshold")),
    ("domain/assessment.amber", "evidence with invalid operator", lambda d: d["fired_rules"][0]["evidence"].update(operator="=>")),
    ("domain/assessment.amber", "evidence with non-numeric value", lambda d: d["fired_rules"][0]["evidence"].update(value="high")),
    ("domain/assessment.amber", "evidence with non-numeric threshold", lambda d: d["fired_rules"][0]["evidence"].update(threshold="3")),
    ("domain/assessment.amber", "evidence with non-string datum", lambda d: d["fired_rules"][0]["evidence"].update(datum=7)),
    ("domain/assessment.amber", "evidence with unknown property", lambda d: d["fired_rules"][0]["evidence"].update(unit="x")),
    ("engine/features-request", "no chunks", lambda d: d.update(chunks=[])),
    ("engine/baseline-request", "no reference sessions", lambda d: d.update(feature_sets=[])),
    ("engine/baseline-request", "self-report with pain above 10", lambda d: d["self_reports"][0].update(pain_nrs=11)),
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
    ("app/ui-rep-state", "rep negative", lambda d: d.update(rep=-1)),
    ("app/ui-rep-state", "total zero", lambda d: d.update(total=0)),
    ("app/ui-rep-state", "unknown phase", lambda d: d.update(phase="holding")),
    ("app/ui-rep-state", "progress above 100", lambda d: d.update(progress_pct=100.5)),
    ("app/ui-rep-state", "set negative", lambda d: d.update(set=-1)),
    ("app/ui-rep-state", "set omitted", lambda d: d.pop("set")),
    ("app/ui-rep-state", "sets total zero", lambda d: d.update(sets_total=0)),
    ("app/ui-rep-state", "sets total omitted instead of null", lambda d: d.pop("sets_total")),
    ("app/ui-rep-state", "reps per set zero", lambda d: d.update(reps_per_set=0)),
    ("app/ui-rep-state", "reps per set omitted instead of null", lambda d: d.pop("reps_per_set")),
    ("app/ui-rep-state", "rep in set negative", lambda d: d.update(rep_in_set=-1)),
    ("app/ui-rep-state", "rep in set omitted", lambda d: d.pop("rep_in_set")),
    ("app/ui-rep-state", "last omitted instead of null", lambda d: d.pop("last")),
    ("app/ui-rep-state", "last duration zero", lambda d: d["last"].update(duration_s=0)),
    ("app/ui-rep-state", "unknown signal", lambda d: d.update(signal="weak")),
    ("app/ui-rep-state", "as_of without offset", lambda d: d.update(as_of="2026-11-15T10:00:00")),
    ("app/ui-rep-state", "extra field", lambda d: d.update(note="x")),
    ("app/ui-rep-state.first-rep", "progress omitted instead of null", lambda d: d.pop("progress_pct")),
    ("app/ui-rep-state.resting", "progress while resting", lambda d: d.update(progress_pct=50)),
    ("app/ui-rep-state.no-signal", "phase while there is no signal", lambda d: d.update(phase="pulling")),
    ("app/ui-rep-state.no-signal", "progress while there is no signal", lambda d: d.update(progress_pct=40)),
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
    ("app/ui-today.nodata", "session in progress without planned sets", lambda d: d["session_in_progress"].pop("planned_sets")),
    ("app/ui-today.nodata", "session in progress without reps per set", lambda d: d["session_in_progress"].pop("reps_per_set")),
    ("app/ui-today.nodata", "session in progress with zero sets", lambda d: d["session_in_progress"].update(planned_sets=0)),
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
    ("app/ui-strip", "ground link with an unknown state", lambda d: d.update(ground_link={"state": "degraded"})),
    ("app/ui-strip", "ground link down without back_in_min", lambda d: d["ground_link"].pop("back_in_min")),
    ("app/ui-strip.unpaired", "ground link up with a return time", lambda d: d["ground_link"].update(back_in_min=3)),
    ("app/ground-summary", "revision zero", lambda d: d.update(revision=0)),
    ("app/ground-summary", "date that is a timestamp", lambda d: d.update(date="2027-02-09T00:00:00Z")),
    ("app/ground-summary", "confidence above 1", lambda d: d.update(confidence=1.2)),
    ("app/ground-summary", "unknown red flag", lambda d: d.update(red_flags=["sad"])),
    ("app/ground-summary", "prose recommendation", lambda d: d.update(recommendations=["Do the set"])),
    ("app/ground-summary", "driver without its deviation", lambda d: d["drivers"][0].pop("deviation_z")),
    ("app/ground-summary", "escalation reason in prose", lambda d: d["escalation"].update(reasons=["Numb legs"])),
    ("app/ground-summary", "required escalation without urgency", lambda d: d["escalation"].update(required=True)),
    ("app/ground-summary", "contact carrying the note of the crew", lambda d: d["contacts"][0].update(text="Called the CMO")),
    ("app/ground-summary", "unknown other symptom", lambda d: d.update(other_symptoms=["sad"])),
    ("app/ground-summary", "pain stop at rep zero", lambda d: d["sessions"][0].update(pain_stop={"rep_index": 0, "at": "2027-02-09T08:30:00Z"})),
    ("domain/medical-contact", "unknown contact role", lambda d: d.update(contact="neighbour")),
    ("domain/self-report.urgent-other", "unknown other symptom", lambda d: d.update(other_symptoms=["sad"])),
    ("domain/self-report.urgent-other", "urgent report with nothing reported", lambda d: d.update(other_symptoms=[])),
    ("app/ui-crew", "negative session count", lambda d: d["crew"][0].update(sessions=-1)),
    ("app/ui-crew", "name in the crew list", lambda d: d["crew"][0].update(name="Ana")),
    ("app/ground-summary", "personal data in the summary", lambda d: d.update(date_of_birth="1980-01-01")),
    ("app/ground-summary", "astronaut id that is not a pseudonym", lambda d: d.update(astronaut_id="Ana")),
    ("app/ground-summary.nodata", "nodata with an assessment id", lambda d: d.update(assessment_id="a55e55e0-0000-4000-8000-0000000000a1")),
    ("app/ground-summary.nodata", "nodata with a confidence", lambda d: d.update(confidence=1)),
    ("app/ground-summary.nodata", "nodata with data counts", lambda d: d.update(data_available=0, data_total=0)),
    ("app/ground-summary.nodata", "nodata with drivers", lambda d: d.update(drivers=[{"feature_id": "a.b", "deviation_z": None}])),
    ("app/ground-summary.nodata", "nodata with recommendations", lambda d: d.update(recommendations=["REC_LUMBAR_STAB_01"])),
    ("app/ground-summary.nodata", "nodata with an escalation", lambda d: d.update(escalation={"required": False, "urgency": None, "reasons": []})),
    ("app/ui-houston", "summary omitted instead of null", lambda d: d.pop("summary")),
    ("app/ui-houston", "ground link omitted instead of null", lambda d: d.pop("ground_link")),
    ("app/ui-houston", "link down with negative minutes", lambda d: d["ground_link"].update(back_in_min=-1)),
    ("app/ui-houston", "last contact without offset", lambda d: d.update(last_contact_at="2027-02-09T06:00:02")),
    ("app/ui-houston", "real transport", lambda d: d.update(transport="satcom")),
    ("app/ui-houston", "content declared validated", lambda d: d.update(content_validated=True)),
    ("app/ui-houston", "summary that is not a summary", lambda d: d["summary"].update(level="yellow")),
    ("app/ui-outbox", "queued item already received", lambda d: d["items"][0].update(received_at="2027-02-09T09:00:05Z")),
    ("app/ui-outbox", "sent item without received_at", lambda d: d["items"][1].update(received_at=None)),
    ("app/ui-outbox", "unknown item state", lambda d: d["items"][0].update(state="failed")),
    ("app/ui-outbox", "negative attempts", lambda d: d["items"][0].update(attempts=-1)),
    ("app/ui-outbox", "revision zero", lambda d: d["items"][0].update(revision=0)),
    ("app/ui-outbox", "clinical payload in an item", lambda d: d["items"][0].update(summary={})),
    ("app/ui-outbox", "ground link omitted instead of null", lambda d: d.pop("ground_link")),
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
    ("app/ui-session", "planned sets omitted instead of null", lambda d: d.pop("planned_sets")),
    ("app/ui-session", "planned sets zero", lambda d: d.update(planned_sets=0)),
    ("app/ui-session", "reps per set omitted instead of null", lambda d: d.pop("reps_per_set")),
    ("app/ui-session", "reps per set zero", lambda d: d.update(reps_per_set=0)),
    ("app/ui-session", "rest between sets omitted instead of null", lambda d: d.pop("rest_between_sets_s")),
    ("app/ui-session", "negative rest between sets", lambda d: d.update(rest_between_sets_s=-1)),
    ("app/ui-session", "band without load", lambda d: d["band"].pop("nominal_kg")),
    ("app/ui-session", "fit item that is prose", lambda d: d.update(fit_items=["Clean the skin"])),
    ("app/ui-session", "unacked calibration with an ack time", lambda d: d["calibration"].update(acked=False)),
    ("app/ui-session", "unknown session state", lambda d: d.update(state="paused")),
    ("agent/chat-request", "astronaut id that is not a pseudonym", lambda d: d.update(astronaut_id="Ana")),
    ("agent/chat-request", "conversation id that is not a uuid", lambda d: d.update(conversation_id="chat-1")),
    ("agent/chat-request", "empty question", lambda d: d.update(question="")),
    ("agent/chat-request", "blank question", lambda d: d.update(question="   ")),
    ("agent/chat-request", "question over 2000 characters", lambda d: d.update(question="a" * 2001)),
    ("agent/chat-request", "history sent by the client", lambda d: d.update(history=[])),
    ("agent/chat-request", "schema version of another major", lambda d: d.update(schema_version="2.0.0")),
    ("agent/chat-request", "missing conversation id", lambda d: d.pop("conversation_id")),
    ("agent/chat-event.text", "empty delta", lambda d: d.update(delta="")),
    ("agent/chat-event.text", "unknown event type", lambda d: d.update(type="token")),
    ("agent/chat-event.text", "text event with the fallback field", lambda d: d.update(text="x")),
    ("agent/chat-event.tool-call", "tool args that are not an object", lambda d: d.update(tool_args="astronaut_id=AST-01")),
    ("agent/chat-event.tool-call", "missing tool name", lambda d: d.pop("tool_name")),
    ("agent/chat-event.fallback", "empty fallback text", lambda d: d.update(text="")),
    ("agent/chat-event.notice", "unknown notice code", lambda d: d.update(code="warning")),
    ("agent/chat-event.end", "end with a payload", lambda d: d.update(text="bye")),
    ("agent/chat-event.end", "missing schema version", lambda d: d.pop("schema_version")),
    ("agent/chat-event.error", "unknown error code", lambda d: d.update(code="timeout")),
    ("agent/chat-event.error", "error without message", lambda d: d.pop("message")),
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
    document["skipped"].append({"feature_id": "stretch.full_spine.peak_elongation_mm", "reason": "calibration_expired"})
    assert validator_for("domain/feature-set").is_valid(document)
    document["skipped"][-1]["reason"] = "calibration_stale"
    assert not validator_for("domain/feature-set").is_valid(document)


def test_feature_set_accepts_insufficient_sample_rate_as_skip_reason(validator_for):
    # an IMU feature needs a fast enough stream (#44): a slower stream skips it with its own reason
    document = load_json(EXAMPLES_DIR / "valid" / "domain" / "feature-set.preflight.json")
    document["skipped"].append({"feature_id": "imu.t9.sagittal.rom_deg", "reason": "insufficient_sample_rate"})
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


# Two EMG streams (one per latissimus dorsi), the IMU at T9 and the cord over the whole spine.
CHUNK_KINDS = ("emg", "emg-right", "imu", "stretch")
CALIBRATION_KINDS = ("stretch", "emg-mvc", "emg-mvc-right", "imu")


def test_engine_examples_embed_the_standalone_examples():
    features = valid_example("engine/features-request")
    assert features["session"] == valid_example("domain/session.exercise")
    assert features["chunks"] == [valid_example(f"telemetry/stream-chunk.{kind}") for kind in CHUNK_KINDS]
    assert features["calibrations"] == [valid_example(f"domain/calibration.{kind}") for kind in CALIBRATION_KINDS]
    assert features["clock_syncs"] == [valid_example("telemetry/clock-sync.round-trip")]
    assert features["profile"] == valid_example("domain/astronaut-profile")

    assert valid_example("engine/baseline-request")["feature_sets"] == [valid_example("domain/feature-set.preflight")]
    assert valid_example("engine/baseline-request")["self_reports"] == [valid_example("domain/self-report.daily")]

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
    calibrations = {c["calibration_id"]: c for c in (valid_example(f"domain/calibration.{kind}") for kind in CALIBRATION_KINDS)}
    for stream in session["streams"]:
        assert calibrations[stream["calibration_id"]]["stream_id"] == stream["stream_id"]
    chunk_streams = {valid_example(f"telemetry/stream-chunk.{kind}")["stream_id"] for kind in CHUNK_KINDS}
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


# Until Aguado validates (#38), ranges may exist only if they say so in their source.
PROVISIONAL_MARKER = "provisional, pendiente de Aguado"
NO_RANGES_DIRECTIONS = {"none", "unspecified"}
SYMMETRY_ID = "emg.{muscle}.symmetry_index_pct"


def _catalog_entries() -> list[dict]:
    return load_catalog()["entries"]


def _entries_with_ranges() -> list[dict]:
    return [entry for entry in _catalog_entries() if entry["ranges"] is not None]


def _entry_ids(entries: list[dict]) -> list[str]:
    return [entry["feature_id"] for entry in entries]


def test_catalog_has_no_invented_ranges(catalog):
    # Ranges belong to the clinical owner: a non-null range is validated or says it is provisional.
    for entry in catalog["entries"]:
        if entry["ranges"] is not None:
            provisional = entry["ranges"]["source"].startswith(PROVISIONAL_MARKER)
            assert entry["status"] == "validated" or provisional, entry["feature_id"]


@pytest.mark.parametrize("entry", _entries_with_ranges(), ids=_entry_ids(_entries_with_ranges()))
def test_catalog_ranges_are_ordered_and_consistent_with_risk_direction(entry):
    ranges = entry["ranges"]
    assert entry["risk_direction"] not in NO_RANGES_DIRECTIONS, entry["feature_id"]
    for name in ("normal", "alert", "critical"):
        low, high = ranges[name]["min"], ranges[name]["max"]
        assert low is None or high is None or low <= high, f"{entry['feature_id']}: {name}"
    # normal, alert and critical are laid out from the good end to the bad end
    order = {"higher_is_worse": ("normal", "alert", "critical"), "lower_is_worse": ("critical", "alert", "normal")}
    if entry["risk_direction"] in order:
        first, middle, last = (ranges[name] for name in order[entry["risk_direction"]])
        assert first["max"] is not None and middle["min"] is not None and first["max"] <= middle["min"], entry["feature_id"]
        assert middle["max"] is not None and last["min"] is not None and middle["max"] <= last["min"], entry["feature_id"]


@pytest.mark.parametrize("entry", _entries_with_ranges(), ids=_entry_ids(_entries_with_ranges()))
def test_provisional_catalog_entries_stay_proposed(entry):
    if entry["ranges"]["source"].startswith(PROVISIONAL_MARKER):
        assert entry["status"] == "proposed", entry["feature_id"]
        assert "Aguado" in entry["ranges"]["source"] and len(entry["ranges"]["source"]) > len(PROVISIONAL_MARKER) + 2


@pytest.mark.parametrize("entry", _catalog_entries(), ids=_entry_ids(_catalog_entries()))
def test_catalog_entries_without_a_risk_direction_have_no_ranges(entry):
    if entry["risk_direction"] in NO_RANGES_DIRECTIONS:
        assert entry["ranges"] is None, entry["feature_id"]


def test_catalog_version_marks_the_provisional_values():
    assert load_catalog()["catalog_version"] == "0.2.0"


def test_catalog_carries_provisional_directions_and_ranges_for_the_risk_layer():
    # #51 needs something to read: more than the symptoms scales, and at least one provisional range
    directions = {entry["feature_id"]: entry["risk_direction"] for entry in _catalog_entries()}
    specified = {fid for fid, direction in directions.items() if direction not in NO_RANGES_DIRECTIONS}
    assert len([fid for fid in specified if not fid.startswith("symptoms.")]) >= 5
    assert any(entry["ranges"]["source"].startswith(PROVISIONAL_MARKER) for entry in _entries_with_ranges())


def test_symmetry_index_waits_for_aguado():
    entry = next(entry for entry in _catalog_entries() if entry["feature_id"] == SYMMETRY_ID)
    assert (entry["risk_direction"], entry["ranges"], entry["status"]) == ("unspecified", None, "proposed")


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


GROUND_SCHEMAS = ["app/ground-summary", "app/ui-houston", "app/ui-outbox"]
NOT_FOR_GROUND = {"date_of_birth", "sex_at_birth", "gender_identity", "anthropometry", "spine_history", "space_history", "fitness", "notes"}


def property_names(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                yield from value
            yield from property_names(value)
    elif isinstance(node, list):
        for item in node:
            yield from property_names(item)


@pytest.mark.parametrize("rel", GROUND_SCHEMAS)
def test_ground_schemas_carry_no_personal_profile_data(rel, schemas):
    profile = set(load_json(SCHEMAS_DIR / "domain" / "astronaut-profile.schema.json")["properties"])
    banned = NOT_FOR_GROUND | (profile - {"schema_version", "astronaut_id", "updated_at", "launch_date", "landing_date"})
    assert not banned & set(property_names(schemas[SCHEMAS_DIR / f"{rel}.schema.json"]))


def test_assessment_example_has_a_fired_rule_with_evidence():
    evidence = valid_example("domain/assessment.amber")["fired_rules"][0]["evidence"]
    assert set(evidence) == {"datum", "value", "operator", "threshold"}


# ---- standardised exercise protocol (#12) -----------------------------------------------------


def test_exercise_session_follows_the_standardised_protocol():
    session = valid_example("domain/session.exercise")
    assert session["protocol_id"] == "lat_pulldown_standing_v1"
    assert (session["planned_sets"], session["reps_per_set"]) == (4, 15)
    assert session["planned_reps"] == session["planned_sets"] * session["reps_per_set"]
    assert session["load_declared"]["band_id"] == "green" and session["load_declared"]["nominal_kg"] == 25
    streams = {stream["stream_id"]: stream for stream in session["streams"]}
    assert set(streams) == {"emg_lat_left", "emg_lat_right", "imu_t9", "stretch_spine"}
    for stream_id, side in (("emg_lat_left", "left"), ("emg_lat_right", "right")):
        placement = streams[stream_id]["placement"]
        assert (placement["muscle"], placement["side"]) == ("latissimus_dorsi", side)
        assert placement["electrode_spacing_cm"] == 10
        assert placement["span"] == {"from_level": "t11", "to_level": "l2"}
        assert placement["ground"]["location"] == "iliac_crest"
    assert streams["imu_t9"]["placement"] == {"spine_level": "t9"}
    assert streams["stretch_spine"]["placement"] == {"spine_region": "full_spine", "span": {"from_level": "c3", "to_level": "s1"}}


def test_exercise_events_delimit_sets_and_repetitions():
    events = valid_example("domain/session.exercise")["events"]
    types = [event["event_type"] for event in events]
    assert {"set_start", "set_end", "rep_start", "rep_end"} <= set(types)
    assert types.index("set_start") < types.index("rep_start") and types.index("rep_end") < types.index("set_end")
    assert all(event["set_index"] == 1 for event in events if event["event_type"].startswith("set_"))


def test_spine_checks_bracket_the_exercise_session():
    exercise = valid_example("domain/session.exercise")
    pre, post = valid_example("domain/session.spine-check"), valid_example("domain/session.spine-check-post")
    assert (pre["phase"], post["phase"]) == ("pre", "post")
    assert pre["related_session_id"] == post["related_session_id"] == exercise["session_id"]
    assert pre["protocol_id"] == post["protocol_id"] == "spine_length_rest_v1"
    assert pre["started_at"] < exercise["started_at"] < post["started_at"]


def test_a_spine_check_without_an_exercise_needs_neither_phase_nor_related_session(validator_for):
    document = valid_example("domain/session.spine-check")
    document.pop("phase")
    document.pop("related_session_id")
    assert validator_for("domain/session").is_valid(document)


def test_set_events_are_valid_for_the_mark_event_command_and_the_session(validator_for):
    command = valid_example("telemetry/command.mark-event")
    event_types = load_json(SCHEMAS_DIR / "domain" / "session.schema.json")["$defs"]["event_type"]["enum"]
    for event_type in ("set_start", "set_end"):
        assert event_type in event_types
        assert validator_for("telemetry/command").is_valid(command | {"event_type": event_type, "set_index": 2})


def test_ui_openapi_event_request_matches_the_session_event_types():
    import yaml

    spec = yaml.safe_load((CONTRACTS_DIR / "ui-openapi.yaml").read_text(encoding="utf-8"))
    body = spec["paths"]["/ui/sessions/{session_id}/events"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    event_types = load_json(SCHEMAS_DIR / "domain" / "session.schema.json")["$defs"]["event_type"]["enum"]
    assert body["properties"]["event_type"]["enum"] == event_types
    assert body["properties"]["set_index"] == {"type": "integer", "minimum": 1}


def test_common_defines_the_protocol_sensor_positions(common):
    defs = common["$defs"]
    assert {"electrode_spacing_cm", "ground", "span"} <= set(defs["placement"]["properties"])
    assert defs["placement"]["properties"]["ground"]["properties"]["location"] == {"$ref": "#/$defs/ground_location"}
    assert "iliac_crest" in defs["ground_location"]["enum"]
    assert {"c3", "c4", "t9", "t11", "l2", "l5", "s1"} <= set(defs["spine_level"]["enum"])
    assert "c5" not in defs["spine_level"]["enum"], "only c3 and c4 were added"
    assert "full_spine" in defs["spine_region"]["enum"]
