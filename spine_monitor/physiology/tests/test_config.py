import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from spine_contracts import GravityEnvironment, MissionPhase, StreamKind

from spine_physiology.config import MissionConfig, load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"


@pytest.fixture(scope="module")
def raw() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def variant(raw: dict, edit) -> dict:
    data = copy.deepcopy(raw)
    edit(data)
    return data


def test_example_loads():
    config = load_config(EXAMPLE)
    assert config.total_days == 14
    assert [m.astronaut_id for m in config.crew] == ["AST-01", "AST-02"]
    assert [s.phase for s in config.segments] == [MissionPhase.PREFLIGHT, MissionPhase.INFLIGHT, MissionPhase.POSTFLIGHT]
    assert config.segments[1].gravity_environment is GravityEnvironment.MICROGRAVITY
    assert config.device.stream("imu_l3").stream_kind is StreamKind.IMU
    assert config.protocol("lat_pulldown_standing_v1").session_type == "exercise"
    assert config.signals.samples_per_chunk(config.signals.emg.sample_rate_hz) == 100


def test_example_has_placeholder_warning():
    assert EXAMPLE.read_text(encoding="utf-8").startswith("# PLACEHOLDER")


@pytest.mark.parametrize(
    "path",
    [
        ("seed",),
        ("signals", "chunk_ms"),
        ("signals", "emg", "noise_rms_mv"),
        ("curves", "pain_stiffness", "daily_sd"),
        ("faults", "device", "clock", "drift_ppm"),
        ("device", "battery", "start_pct"),
        ("self_report", "nrs_noise_sd"),
        ("calibration", "emg_mvc", "window_ms"),
    ],
)
def test_missing_numeric_field_fails(raw, path):
    def drop(data):
        node = data
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    with pytest.raises(ValidationError, match="Field required"):
        MissionConfig.model_validate(variant(raw, drop))


def test_nullable_numeric_field_must_still_be_written(raw):
    with pytest.raises(ValidationError, match="Field required"):
        MissionConfig.model_validate(variant(raw, lambda d: d["device"].pop("rssi_dbm")))


@pytest.mark.parametrize("where", [(), ("signals", "imu"), ("crew", 0, "baseline"), ("faults", "quality", "clipping")])
def test_extra_field_is_forbidden(raw, where):
    def add(data):
        node = data
        for key in where:
            node = node[key]
        node["surprise"] = 1

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        MissionConfig.model_validate(variant(raw, add))


def test_numbers_are_strict(raw):
    with pytest.raises(ValidationError):
        MissionConfig.model_validate(variant(raw, lambda d: d["signals"]["emg"].update(noise_rms_mv="1e-3")))
    with pytest.raises(ValidationError):
        MissionConfig.model_validate(variant(raw, lambda d: d["signals"].update(chunk_ms=True)))


def test_times_must_be_strings(raw):
    with pytest.raises(ValidationError, match="quoted"):
        MissionConfig.model_validate(variant(raw, lambda d: d["self_report"].update(time_utc=420)))


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda d: d["segments"][0].update(days=0), "greater than 0"),
        (lambda d: d["curves"]["spine_elongation"]["points"].reverse(), "strictly sorted"),
        (lambda d: d["signals"]["imu"].update(sample_rate_hz=0.0), "greater than 0"),
        (lambda d: d["signals"]["stretch"].update(sample_rate_hz=15.0), "whole number"),
        (lambda d: d["schedule"]["background"]["sample_rates_hz"].update(imu=5.0), "whole number"),
        (lambda d: d["signals"]["emg"].update(band_high_hz=600.0), "band"),
        (lambda d: d["signals"]["stretch"]["curve"].reverse(), "strictly increasing"),
        (lambda d: d["faults"]["quality"]["clipping"].update(probability=1.5), "less than or equal to 1"),
        (lambda d: d["faults"]["quality"]["clipping"]["duration_ms"].update(min=900.0), "min"),
        (lambda d: d["crew"][1].update(astronaut_id="AST-01"), "duplicated astronaut_id"),
        (lambda d: d["crew"][0]["baseline"]["emg_mvc_rms_mv"].pop("emg_lat_right"), "emg_mvc_rms_mv"),
        (lambda d: d["crew"][0]["baseline"].update(cord_rest_length_mm=900.0), "outside the stretch curve"),
        (lambda d: d["device"]["battery"].update(drain_pct_per_hour_idle=99.0), "drain_pct_per_hour_streaming"),
        (lambda d: d["faults"]["quality"]["motion_artifact"].pop("band_low_hz"), "band_low_hz"),
        (lambda d: d["faults"]["quality"]["motion_artifact"].update(band_low_hz=30.0), "band_low_hz must be"),
        (lambda d: d["faults"]["quality"]["sensor_warmup"].pop("decay_fraction"), "decay_fraction"),
        (lambda d: d["schedule"]["sessions"][0].update(protocol_id="nope_v1"), "unknown protocol_id"),
        (lambda d: d["schedule"]["sessions"][0].update(time_utc="05:00"), "power window"),
        (lambda d: d["schedule"]["background"]["streams"].append("emg_lat_left"), "EMG stream"),
        (lambda d: d["protocols"]["spine_length_rest_v1"]["streams"].append("emg_nope"), "unknown streams"),
        (lambda d: d["daily_context"]["exercise"].pop("inflight"), "missing phases"),
        (lambda d: d["device"]["streams"][0]["placement"].pop("side"), "muscle and side"),
        (lambda d: d["signals"]["imu"]["mounting"].update(sensor_y="caudal"), "three different body axes"),
        (lambda d: d["protocols"]["spine_length_rest_v1"].update(session_type="exercise"), "Field required"),
    ],
)
def test_invalid_values_fail(raw, edit, message):
    with pytest.raises(ValidationError, match=message):
        MissionConfig.model_validate(variant(raw, edit))


def test_config_is_frozen():
    config = load_config(EXAMPLE)
    with pytest.raises(ValidationError):
        config.seed = 1
