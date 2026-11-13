"""Dataset de demo: los últimos 14 días de AST-01 generados relativos a un reloj inyectable.

Los ejemplos de los contratos (`mocks.py`) son una única foto con uno o dos elementos por lista y se comparten con los tests de
contrato, así que no cambian. Aquí se construye, a partir de ellos como plantilla (mismos campos, unidades, ids de feature, de
regla y de recomendación, y mismo baseline), una trayectoria con historia para que el agente pueda explicar el nivel, comparar
periodos y listar sesiones y autoinformes.

Todo es función pura de `now` (redondeado a la hora hacia abajo): los ids son uuid5 sobre astronauta, tipo y marca de tiempo, así
que el mismo `now` da siempre los mismos datos. Nada es diagnóstico: solo evidencia (features, z-scores) y reglas.

Trayectoria (t = 0 hace 13 días, t = 13 ahora): verde de t = 0 a 7, ámbar de t = 8 a 13 con riesgo creciente, hasta 0,62 en `now`.
"""

import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from spine_contracts import (
    Assessment,
    AssessRequest,
    Explanation,
    History,
    SelfReportList,
    SessionList,
    SimulateRequest,
    SimulateResponse,
    Status,
    dump,
)

from spine_mcp_server.mocks import ExampleAppReader, _load, _not_found

ASTRONAUT = "AST-01"
DAYS = 14
_NAMESPACE = uuid.UUID("a5c1e910-0000-4000-8000-0d3e0da7a000")
_MISSION_DAY_NOW = 87
_AMBER_FROM = 8  # primer día ámbar (t)
_PROTOCOL = "lat_pulldown_standing_v1"
_BASELINE_STRETCH_MEAN = 6.5
_STRETCH_Z_SCALE = 1.083
_PAIN_BASELINE = 2.0
_PAIN_Z_SCALE = 1.2
# Dolor NRS del autoinforme diario por día t (sube de ~2 a 5).
_PAIN = [2, 2, 2, 2, 2, 2, 3, 3, 3, 4, 4, 5, 5, 5]
_RISK_PER_STRETCH_MM = 0.052
_STRETCH_ID = "stretch.full_spine.peak_elongation_mm"
_PAIN_ID = "symptoms.pain_nrs_7d_mean"


def _template(relative: str) -> Any:
    """Copia fresca (JSON recién leído) de un ejemplo, para modificarla sin tocar nada compartido."""
    return _load(relative)


def _hour(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _uuid(kind: str, key: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{ASTRONAUT}/{kind}/{key}"))


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _level(score: float) -> str:
    return "green" if score < 0.4 else "amber" if score < 0.8 else "red"


def _worst(*levels: str) -> str:
    return max(levels, key=("green", "amber", "red").index)


def _stretch(t: int) -> float:
    return round(6.6 + 0.06 * t, 2) if t < _AMBER_FROM else round(7.0 + 0.35 * (t - 6), 2)


def _z_stretch(value: float) -> float:
    return round((value - _BASELINE_STRETCH_MEAN) / _STRETCH_Z_SCALE, 1)


def _z_pain(mean: float) -> float:
    return round((mean - _PAIN_BASELINE) / _PAIN_Z_SCALE, 1)


def _pain_mean_7d(t: int) -> float:
    window = _PAIN[max(0, t - 6) : t + 1]
    return round(sum(window) / len(window), 1)


def _risk(t: int) -> float:
    return round(0.18 + 0.02 * t, 2) if t < _AMBER_FROM else round(0.40 + 0.0444 * (t - _AMBER_FROM), 2)


class _Timeline:
    """Marcas de tiempo de cada elemento, todas relativas a `now`; `k` son los días hacia atrás (t = 13 - k)."""

    def __init__(self, now: datetime) -> None:
        self.now = _hour(now)

    @staticmethod
    def t(k: int) -> int:
        return DAYS - 1 - k

    def assessment_at(self, k: int) -> datetime:
        return self.now - timedelta(days=k)

    def mission_day(self, k: int) -> int:
        return _MISSION_DAY_NOW - k

    def report_at(self, k: int) -> datetime:
        return self.assessment_at(k) - timedelta(minutes=30)

    def exercise_ks(self) -> list[int]:
        return list(range(DAYS - 1, 0, -2))  # 13, 11, ..., 1: una cada 2 días

    def exercise_start(self, k: int) -> datetime:
        return self.assessment_at(k) - timedelta(minutes=90)

    def exercise_end(self, k: int) -> datetime:
        return self.exercise_start(k) + timedelta(minutes=6)

    def feature_set_at(self, k: int) -> datetime:
        return self.exercise_end(k) + timedelta(minutes=3)

    def check_starts(self) -> list[datetime]:
        return [self.assessment_at(k) - timedelta(hours=2) for k in (8, 4, 0)]

    def odi_at(self) -> datetime:
        return self.assessment_at(2) - timedelta(minutes=45)

    def session_k(self, k: int) -> int:
        """Día de la última sesión de ejercicio ya procesada en la valoración de `k` (la de `k` si es impar, si no la anterior)."""
        return k if k % 2 == 1 else k + 1


def _exercise_session(tl: _Timeline, k: int) -> dict[str, Any]:
    session = _template("app/session-list.json")["items"][0]
    start = tl.exercise_start(k)
    example_start = datetime.fromisoformat(session["started_at"])
    offsets = [(datetime.fromisoformat(event["timestamp"]) - example_start).total_seconds() for event in session["events"]]
    session.update(
        session_id=_uuid("session", _iso(start)),
        source="simulator",
        started_at=_iso(start),
        ended_at=_iso(tl.exercise_end(k)),
        mission_context={"phase": "inflight", "gravity_environment": "microgravity"},
    )
    for event, offset in zip(session["events"], offsets, strict=True):
        event["timestamp"] = (start + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return session


def _spine_check(start: datetime) -> dict[str, Any]:
    session = _template("domain/session.spine-check.json")
    session.update(
        session_id=_uuid("session", _iso(start)),
        source="simulator",
        started_at=_iso(start),
        ended_at=_iso(start + timedelta(minutes=5)),
        end_reason="completed",
        mission_context={"phase": "inflight", "gravity_environment": "microgravity"},
    )
    return session


def _feature_set(tl: _Timeline, k: int) -> dict[str, Any]:
    t = tl.t(k)
    template = _template("app/history.json")["feature_sets"][0]
    values = {
        "emg.latissimus_dorsi.left.rms_pct_mvc": round(41.0 + 0.5 * t, 1),
        "emg.latissimus_dorsi.left.median_freq_hz": round(87.3 - 0.65 * t, 1),
        "imu.t9.sagittal.rom_deg": round(28.4 + 0.2167 * t, 1),
        _STRETCH_ID: _stretch(t),
    }
    for feature in template["features"]:
        if feature["feature_id"] in values:
            feature["value"] = values[feature["feature_id"]]
        if feature["feature_id"] == "emg.latissimus_dorsi.left.median_freq_hz" and t < 8:
            feature["quality"]["flags"] = []
    start = tl.exercise_start(k)
    template.update(
        session_id=_uuid("session", _iso(start)),
        computed_at=_iso(tl.feature_set_at(k)),
        window={"start": _iso(start + timedelta(minutes=1)), "end": _iso(start + timedelta(minutes=6))},
        skipped=[{"feature_id": "emg.latissimus_dorsi.right.median_freq_hz", "reason": "stream_missing"}],
    )
    return template


def _self_report(tl: _Timeline, k: int) -> dict[str, Any]:
    t = tl.t(k)
    report = _template("app/self-report-list.json")["items"][0]
    pain = _PAIN[t]
    report.update(
        report_id=_uuid("self_report", _iso(tl.report_at(k))),
        timestamp=_iso(tl.report_at(k)),
        mission_day=tl.mission_day(k),
        pain_nrs=pain,
        pain_nrs_worst_24h=min(10, pain + 1),
        stiffness_nrs=min(10, pain + 1),
        sleep={"hours": round(7.2 - 0.12 * t, 1), "quality_nrs": max(3, round(8 - 0.3 * t))},
    )
    return report


def _weekly_odi(tl: _Timeline) -> dict[str, Any]:
    report = _template("domain/self-report.weekly-odi.json")
    report.update(report_id=_uuid("self_report", _iso(tl.odi_at())), timestamp=_iso(tl.odi_at()), mission_day=_MISSION_DAY_NOW - 2)
    return report


def _assessment(tl: _Timeline, k: int) -> dict[str, Any]:
    t = tl.t(k)
    amber = t >= _AMBER_FROM
    template = _template("app/status.json")["latest_assessment"]
    stretch = _stretch(tl.t(tl.session_k(k)))
    pain_mean = _pain_mean_7d(t)
    z_stretch, z_pain = _z_stretch(stretch), _z_pain(pain_mean)
    spine_length = round(_clamp(0.1 + 0.25 * z_stretch), 2)
    muscle = round(0.2 + 0.027 * t, 2)
    symptoms = round(_clamp(0.1 + 0.22 * z_pain), 2)
    domains = [("spine_length", spine_length), ("muscle", muscle), ("kinematics", 0.2), ("symptoms", symptoms)]
    rule_fires = amber and t >= 10
    template.update(
        assessment_id=_uuid("assessment", _iso(tl.assessment_at(k))),
        as_of=_iso(tl.assessment_at(k)),
        mission_day=tl.mission_day(k),
        level="amber" if amber else "green",
        risk_score=_risk(t),
        confidence=round(0.80 - 0.005 * t, 2),
        domains=[{"domain": name, "level": _level(score), "score": score, "evaluated": True} for name, score in domains],
        drivers=[
            {
                "feature_id": _STRETCH_ID,
                "value": stretch,
                "unit": "mm",
                "deviation_z": z_stretch,
                "contribution": round(_clamp(0.13 * z_stretch, 0.02, 0.35), 2),
            },
            {"feature_id": _PAIN_ID, "value": pain_mean, "unit": "nrs", "deviation_z": z_pain, "contribution": round(_clamp(0.1 * z_pain, 0.02, 0.25), 2)},
        ],
        trend={"projected_level": "red", "days_to_threshold": 12 + 2 * (DAYS - 1 - t)} if amber else {"projected_level": "green", "days_to_threshold": None},
        fired_rules=[
            {
                "rule_id": "RULE_SYMPTOM_PAIN_TREND",
                "domain": "symptoms",
                "level": "amber",
                "overrides_model": False,
                "description": "Sustained pain above the alert range over the last 7 days (thresholds owned by the clinical model).",
            }
        ]
        if rule_fires
        else [],
        recommendations=(["REC_LUMBAR_STAB_01"] + (["REC_INCREASE_ARED"] if rule_fires else [])) if amber else [],
        escalation={"required": False, "reasons": []},
        based_on={
            **template["based_on"],
            "session_ids": [_uuid("session", _iso(tl.exercise_start(tl.session_k(k))))],
            "self_report_ids": [_uuid("self_report", _iso(tl.report_at(k)))],
        },
    )
    return template


def _stretch_baseline_entry() -> dict[str, Any]:
    entries = _template("domain/baseline.json")["entries"]
    return next(entry for entry in entries if entry["feature_id"] == _STRETCH_ID)


class DemoAppReader(ExampleAppReader):
    """`ExampleAppReader` (mismo filtrado y límites) con una explicación por cada valoración del historial."""

    def __init__(self, explanations: dict[str, Explanation], **data: Any) -> None:
        super().__init__(explanation=next(reversed(explanations.values())), **data)
        self._explanations = explanations

    def get_explanation(self, assessment_id: str) -> Explanation:
        if assessment_id in self._explanations:
            return self._explanations[assessment_id]
        return super().get_explanation(assessment_id)


def _freshness(now: datetime, items: list[tuple[str, datetime, float, str | None]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for what, last_at, stale_after_hours, device_id in items:
        age = round((now - last_at).total_seconds() / 3600, 1)
        entry: dict[str, Any] = {"what": what, "last_at": _iso(last_at), "age_hours": age, "stale": age > stale_after_hours}
        if device_id is not None:
            entry["device_id"] = device_id
        result.append(entry)
    return result


def build_demo_app_reader(now: datetime) -> ExampleAppReader:
    """Lector de la app sobre los 14 días de AST-01 que terminan en `now` (redondeado a la hora hacia abajo)."""
    tl = _Timeline(now)
    ks = list(range(DAYS - 1, -1, -1))  # de más antiguo a más reciente
    assessments = {k: _assessment(tl, k) for k in ks}
    feature_sets = {k: _feature_set(tl, k) for k in tl.exercise_ks()}
    reports = {k: _self_report(tl, k) for k in ks}
    odi = _weekly_odi(tl)
    sessions = [_exercise_session(tl, k) for k in tl.exercise_ks()] + [_spine_check(s) for s in tl.check_starts()]
    sessions.sort(key=lambda s: s["started_at"])
    window = {"from": _iso(tl.now - timedelta(days=DAYS)), "to": _iso(tl.now), "limit": 100, "truncated": False}
    all_reports = sorted([*reports.values(), odi], key=lambda r: r["timestamp"])

    explanations: dict[str, Explanation] = {}
    for k in ks:
        assessment = assessments[k]
        session_k = tl.session_k(k)
        explanations[assessment["assessment_id"]] = Explanation.model_validate(
            {
                "schema_version": "1.0.0",
                "assessment": assessment,
                "baseline": {"baseline_id": assessment["based_on"]["baseline_id"], "version": 1},
                "baseline_entries": [_stretch_baseline_entry()],
                "feature_sets": [feature_sets[session_k]],
                "self_reports": [reports[k]],
                "unresolved": [],
            }
        )

    last_check = _spine_check(tl.check_starts()[-1])
    template = _template("app/status.json")
    device = template["devices"][0]
    device_at = tl.check_starts()[-1] + timedelta(minutes=30)
    device.update(timestamp=_iso(device_at), status="ok", battery_pct=64)
    for stream in device["streams"]:
        stream["state"] = "active"
    latest_session_at = tl.check_starts()[-1]
    status = Status.model_validate(
        {
            "schema_version": "1.0.0",
            "astronaut_id": ASTRONAUT,
            "generated_at": _iso(tl.now),
            "today": {"date": tl.now.date().isoformat(), "mission_day": _MISSION_DAY_NOW, "phase": "inflight", "gravity_environment": "microgravity"},
            "latest_assessment": assessments[0],
            "latest_session": last_check,
            "latest_self_report": reports[0],
            "devices": [device],
            "active_baseline": {
                "baseline_id": assessments[0]["based_on"]["baseline_id"],
                "version": 1,
                "created_at": _iso(tl.now - timedelta(days=DAYS + 1)),
            },
            "freshness": _freshness(
                tl.now,
                [
                    ("assessment", tl.assessment_at(0), 6, None),
                    ("session", latest_session_at, 72, None),
                    ("self_report", tl.report_at(0), 30, None),
                    ("device", device_at, 72, "wb-01"),
                ],
            ),
        }
    )
    return DemoAppReader(
        explanations,
        status=status,
        history=History.model_validate(
            {
                "schema_version": "1.0.0",
                "astronaut_id": ASTRONAUT,
                **window,
                "feature_sets": [feature_sets[k] for k in tl.exercise_ks()],
                "assessments": [assessments[k] for k in ks],
            }
        ),
        sessions=SessionList.model_validate({"schema_version": "1.0.0", "astronaut_id": ASTRONAUT, **window, "items": sessions}),
        self_reports=SelfReportList.model_validate({"schema_version": "1.0.0", "astronaut_id": ASTRONAUT, **window, "items": all_reports}),
    )


class _DemoSimulator:
    """`Simulator` de demo: la referencia es la última valoración y el escenario mueve el riesgo según los overrides de `stretch.full_spine.peak_elongation_mm`.

    No es el motor: es una aproximación lineal (`_RISK_PER_STRETCH_MM` por mm de elongación extra) para que la demo responda a «¿y si...?».
    Los overrides de otras features no cambian el escenario.
    """

    def __init__(self, reference: Assessment) -> None:
        self._reference = reference

    def simulate(self, request: SimulateRequest) -> SimulateResponse:
        reference = dump(self._reference)
        stretch = next(d for d in reference["drivers"] if d["feature_id"] == _STRETCH_ID)
        extra = 0.0
        for override in request.overrides:
            if override.feature_id != _STRETCH_ID:
                continue
            if override.mode == "add":
                extra += override.value
            elif override.mode == "set":
                extra += override.value - stretch["value"]
            else:
                extra += stretch["value"] * (override.value - 1)
        scenario = deepcopy(reference)
        if extra:
            scenario = self._moved(scenario, stretch["value"] + extra, extra)
        return SimulateResponse.model_validate({"schema_version": "1.0.0", "reference": reference, "scenario": scenario, "hypothetical": True})

    def _moved(self, scenario: dict[str, Any], stretch: float, extra: float) -> dict[str, Any]:
        z = _z_stretch(stretch)
        risk = round(_clamp(scenario["risk_score"] + _RISK_PER_STRETCH_MM * extra, 0.0, 0.99), 2)
        for driver in scenario["drivers"]:
            if driver["feature_id"] == _STRETCH_ID:
                driver.update(value=round(stretch, 2), deviation_z=z, contribution=round(_clamp(0.13 * z, 0.02, 0.35), 2))
        for domain in scenario["domains"]:
            if domain["domain"] == "spine_length":
                domain.update(score=round(_clamp(0.1 + 0.25 * z), 2), level=_level(_clamp(0.1 + 0.25 * z)))
        level = _worst(_level(risk), *(d["level"] for d in scenario["domains"]))
        scenario.update(assessment_id=_uuid("scenario", f"{scenario['assessment_id']}/{stretch}"), risk_score=risk, level=level)
        if level == "red":
            scenario["trend"] = {"projected_level": "red", "days_to_threshold": 0}
            scenario["recommendations"] = [*scenario["recommendations"], "REC_CONTACT_FLIGHT_SURGEON"]
            scenario["escalation"] = {"required": True, "urgency": "priority", "reasons": ["Scenario: spine elongation beyond the alert range."]}
        return scenario


def build_demo_simulator(now: datetime) -> _DemoSimulator:
    """Simulador de demo cuya referencia es la valoración de AST-01 en `now`."""
    latest = build_demo_app_reader(now).get_status(ASTRONAUT).latest_assessment
    assert latest is not None  # el estado de demo siempre trae valoración
    return _DemoSimulator(latest)


class _DemoAssessBase:
    """`AssessBaseSource` de demo: la petición de valoración de AST-01 en `now`."""

    def __init__(self, base: AssessRequest) -> None:
        self._base = base

    def get_base(self, astronaut_id: str) -> AssessRequest:
        if astronaut_id != self._base.astronaut_id:
            raise _not_found("Astronaut", astronaut_id)
        return self._base


def build_demo_assess_base(now: datetime) -> _DemoAssessBase:
    """Base de simulación de AST-01 en `now`: sus últimos feature sets y autoinformes diarios, con el perfil y el baseline de los ejemplos."""
    tl = _Timeline(now)
    template = _template("engine/assess-request.json")
    template["as_of"] = _iso(tl.now)
    template["mission_day"] = _MISSION_DAY_NOW
    template["profile"]["updated_at"] = _iso(tl.now - timedelta(days=20))
    template["profile"]["anthropometry"]["body_mass_measured_at"] = _iso(tl.now - timedelta(days=20))
    template["baseline"]["created_at"] = _iso(tl.now - timedelta(days=DAYS + 1))
    template["feature_sets"] = [_feature_set(tl, k) for k in tl.exercise_ks()[-3:]]
    template["self_reports"] = [_self_report(tl, k) for k in range(6, -1, -1)]
    context = template["daily_context"][0]
    midnight = tl.now.replace(hour=0)
    context.update(date=tl.now.date().isoformat(), mission_day=_MISSION_DAY_NOW)
    context["medication"][0]["taken_at"] = _iso(midnight)
    return _DemoAssessBase(AssessRequest.model_validate(template))


__all__ = ["DemoAppReader", "build_demo_app_reader", "build_demo_assess_base", "build_demo_simulator"]
