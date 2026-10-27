from datetime import UTC, datetime

from spine_contracts import Driver, EvaluationRequest, Level
from spine_engine import (
    AssessmentPipeline,
    Deviation,
    NullBaselineStrategy,
    RiskResult,
    TriageDecision,
    stub_pipeline,
)


def request() -> EvaluationRequest:
    example = EvaluationRequest.model_config["json_schema_extra"]["examples"][0]
    return EvaluationRequest.model_validate(example)


def test_stub_pipeline_is_green():
    assessment = stub_pipeline().evaluate(request())
    assert assessment.astronaut_id == "A1"
    assert assessment.mission_day == 87
    assert assessment.as_of.isoformat() == "2026-10-25T10:00:00+00:00"
    assert assessment.level == Level.GREEN
    assert assessment.risk_score == 0.0
    assert assessment.drivers == []
    assert assessment.trend is None
    assert not assessment.escalate_to_ground


# Fakes sin herencia: el pipeline solo depende de la forma (Protocol).
class FakeBaseline:
    def __init__(self):
        self.calls = []

    def deviations(self, baseline, readings):
        self.calls.append((baseline, list(readings)))
        return [Deviation(metric="m", value=1.0, score=9.0)]


class FakeRisk:
    def __init__(self):
        self.received = None

    def score(self, deviations):
        self.received = list(deviations)
        return RiskResult(score=0.9, drivers=[Driver(feature="m", value=1.0, contribution=0.9)])


class FakeTriage:
    def __init__(self):
        self.received = None

    def decide(self, risk, deviations):
        self.received = (risk, list(deviations))
        return TriageDecision(level=Level.RED, escalate_to_ground=True, recommendations=["REC_X"])


def test_pipeline_wires_custom_implementations():
    baseline, risk, triage = FakeBaseline(), FakeRisk(), FakeTriage()
    req = request()

    assessment = AssessmentPipeline(baseline, risk, triage).evaluate(req)

    deviations = [Deviation(metric="m", value=1.0, score=9.0)]
    assert baseline.calls == [(req.baseline, req.readings)]
    assert risk.received == deviations
    assert triage.received[1] == deviations
    assert triage.received[0].score == 0.9
    assert assessment.level == Level.RED
    assert assessment.risk_score == 0.9
    assert assessment.drivers[0].feature == "m"
    assert assessment.recommendations == ["REC_X"]
    assert assessment.escalate_to_ground


def test_null_baseline_uses_latest_reading_and_skips_unknown_metrics():
    req = request()
    older = req.readings[0].model_copy(update={"timestamp": datetime(2026, 10, 25, 9, tzinfo=UTC), "value": 0.0})
    unknown = req.readings[0].model_copy(update={"sensor_id": "other"})

    result = NullBaselineStrategy().deviations(req.baseline, [req.readings[0], older, unknown])

    assert result == [Deviation(metric="stretch_lumbar.resistance", value=2150.0, score=50.0)]
