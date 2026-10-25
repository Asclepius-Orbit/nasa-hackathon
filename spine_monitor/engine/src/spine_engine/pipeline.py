from spine_contracts import Assessment, EvaluationRequest

from spine_engine.interfaces import BaselineStrategy, RiskModel, TriagePolicy


class AssessmentPipeline:
    """Sin estado: baseline + lecturas entran, una evaluación sale."""

    def __init__(
        self,
        baseline_strategy: BaselineStrategy,
        risk_model: RiskModel,
        triage_policy: TriagePolicy,
    ) -> None:
        self._baseline_strategy = baseline_strategy
        self._risk_model = risk_model
        self._triage_policy = triage_policy

    def evaluate(self, request: EvaluationRequest) -> Assessment:
        deviations = self._baseline_strategy.deviations(request.baseline, request.readings)
        risk = self._risk_model.score(deviations)
        triage = self._triage_policy.decide(risk, deviations)
        return Assessment(
            astronaut_id=request.astronaut_id,
            mission_day=request.mission_day,
            as_of=max(r.timestamp for r in request.readings),
            level=triage.level,
            risk_score=risk.score,
            drivers=risk.drivers,
            recommendations=triage.recommendations,
            escalate_to_ground=triage.escalate_to_ground,
        )
