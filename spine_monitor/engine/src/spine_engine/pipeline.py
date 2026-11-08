from collections.abc import Sequence
from uuid import uuid4

from spine_contracts import (
    SCHEMA_VERSION,
    Assessment,
    AssessmentDomain,
    AssessRequest,
    BasedOn,
    Completeness,
    DomainAssessment,
    FeatureSet,
    Level,
    MissingItem,
    MissingReason,
)

from spine_engine.interfaces import BaselineStrategy, Deviation, RiskModel, TriagePolicy

# Dominio de evaluación según el prefijo del feature_id (hasta que el catálogo lo diga).
_DOMAIN_BY_PREFIX = {
    "emg": AssessmentDomain.MUSCLE,
    "stretch": AssessmentDomain.SPINE_LENGTH,
    "imu": AssessmentDomain.KINEMATICS,
    "symptoms": AssessmentDomain.SYMPTOMS,
}

_MISSING_REASON_BY_SKIP = {
    "no_data": MissingReason.NO_DATA,
    "low_quality": MissingReason.LOW_QUALITY,
    "no_calibration": MissingReason.NO_DATA,
    "calibration_expired": MissingReason.NO_DATA,
    "signal_not_raw": MissingReason.SIGNAL_NOT_RAW,
    "too_few_repetitions": MissingReason.NO_DATA,
    "stream_missing": MissingReason.SENSOR_OFFLINE,
}


def _domain_of(feature_id: str) -> AssessmentDomain | None:
    return _DOMAIN_BY_PREFIX.get(feature_id.split(".", 1)[0])


def _completeness(feature_sets: Sequence[FeatureSet], deviations: Sequence[Deviation]) -> Completeness:
    """Qué parte de lo esperado se pudo evaluar. Lo que falta nunca cuenta como verde."""
    compared = {d.feature_id for d in deviations}
    present = {f.feature_id for fs in feature_sets for f in fs.features}
    skipped = {s.feature_id: s.reason for fs in feature_sets for s in fs.skipped or []} if feature_sets else {}
    missing = [MissingItem(what=fid, reason=_MISSING_REASON_BY_SKIP[reason.value]) for fid, reason in skipped.items()]
    missing += [MissingItem(what=fid, reason=MissingReason.NO_BASELINE) for fid in sorted(present - compared)]
    expected = len(present | set(skipped))
    return Completeness(score=len(compared) / expected if expected else 0.0, missing=missing)


class AssessmentPipeline:
    """Sin estado: baseline + features entran, una evaluación sale."""

    def __init__(
        self,
        baseline_strategy: BaselineStrategy,
        risk_model: RiskModel,
        triage_policy: TriagePolicy,
    ) -> None:
        self._baseline_strategy = baseline_strategy
        self._risk_model = risk_model
        self._triage_policy = triage_policy

    def evaluate(self, request: AssessRequest) -> Assessment:
        deviations = self._baseline_strategy.deviations(request.baseline, request.feature_sets)
        completeness = _completeness(request.feature_sets, deviations)
        risk = self._risk_model.score(deviations, completeness.score)
        triage = self._triage_policy.decide(risk, deviations)

        evaluated = {domain for d in deviations if (domain := _domain_of(d.feature_id)) is not None}
        domains = [
            DomainAssessment(  # pyright: ignore[reportCallIssue]  # generated model: `score` has a positional Field default
                domain=domain,
                level=triage.domain_levels.get(domain, triage.level if domain in evaluated else Level.GREEN),
                evaluated=domain in evaluated,
            )
            for domain in AssessmentDomain
        ]

        assessment = Assessment(
            schema_version=SCHEMA_VERSION,
            assessment_id=str(uuid4()),
            astronaut_id=request.astronaut_id,
            as_of=request.as_of,
            level=triage.level,
            risk_score=risk.score,
            confidence=risk.confidence,
            completeness=completeness,
            domains=domains,
            drivers=risk.drivers,
            fired_rules=triage.fired_rules,
            recommendations=triage.recommendations,
            escalation=triage.escalation,
            based_on=BasedOn(
                baseline_id=request.baseline.baseline_id,
                baseline_version=request.baseline.version,
                # uniqueItems in the contract: a repeated input is listed once, in its first position.
                session_ids=list(dict.fromkeys(fs.session_id for fs in request.feature_sets if fs.session_id is not None)),
                self_report_ids=list(dict.fromkeys(report.report_id for report in request.self_reports or [])),
            ),
        )
        # `dump` emite lo que se fijó: un `mission_day=None` explícito saldría como null y el schema pide un entero.
        if request.mission_day is not None:
            assessment.mission_day = request.mission_day
        return assessment
