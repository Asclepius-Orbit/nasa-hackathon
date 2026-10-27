import pytest
from pydantic import ValidationError

from spine_contracts import (
    Assessment,
    DeviceStatus,
    EvaluationRequest,
    SensorReading,
    topics,
)

MODELS = [SensorReading, EvaluationRequest, Assessment]


@pytest.mark.parametrize("model", MODELS)
def test_examples_validate(model):
    for example in model.model_config["json_schema_extra"]["examples"]:
        model.model_validate(example)


def reading(**overrides):
    data = {
        "timestamp": "2026-10-25T10:00:00Z",
        "sensor_id": "imu_l3",
        "channel": "accel_x",
        "value": 0.0,
        "unit": "g",
    }
    return SensorReading.model_validate(data | overrides)


def test_metric_key():
    assert reading().metric == "imu_l3.accel_x"


@pytest.mark.parametrize("quality", [-0.1, 1.1])
def test_quality_out_of_range(quality):
    with pytest.raises(ValidationError):
        reading(quality=quality)


def test_naive_timestamp_rejected():
    with pytest.raises(ValidationError):
        reading(timestamp="2026-10-25T10:00:00")


def test_empty_readings_rejected():
    with pytest.raises(ValidationError):
        EvaluationRequest(astronaut_id="A1", baseline={}, readings=[])


@pytest.mark.parametrize("score", [-0.1, 1.1])
def test_risk_score_out_of_range(score):
    example = dict(Assessment.model_config["json_schema_extra"]["examples"][0], risk_score=score)
    with pytest.raises(ValidationError):
        Assessment.model_validate(example)


def test_device_status_battery_range():
    with pytest.raises(ValidationError):
        DeviceStatus(device_id="d1", timestamp="2026-10-25T10:00:00Z", status="ok", battery_pct=101)


def test_topics():
    assert topics.spine_raw("A1") == "crew/A1/spine/raw"
    assert topics.emg_raw("A1") == "crew/A1/emg/raw"
    assert topics.device_status("A1") == "crew/A1/device/status"
