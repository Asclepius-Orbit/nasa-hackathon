"""Read-only HTTP API of the app (contracts/app-openapi.yaml), used by the MCP server.

A FastAPI app of its own, served on its own port (`uvicorn --factory monitor.readonly:create_read_app
--port 8002`), so the MCP server never reaches the write endpoints of the UI. It opens the app's
SQLite with `PRAGMA query_only`: no endpoint modifies data, and the connection could not even if one
tried.

- Per-tool endpoints: `/status` (get_current_status), `/history` (get_history; two calls back
  compare_periods) and `/explanation` (explain_assessment).
- `/assess-base`: the /assess request the app would send now (`as_of` is its clock), never sent: the base of the
  MCP `simulate_scenario`. A request that cannot be built is a 404 whose detail is the reason.
- Resource endpoints: one assessment, session, feature set, self-report or baseline, or a list of a
  window. Lists are oldest first, capped by `limit`, keeping the most recent and setting `truncated`.
- Missing data is `null` and marked `stale` in the freshness: absence is never presented as good news.
- Errors are `app/error` problem details. Bodies are the JSON Schemas of `schemas/v1/`.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jsonschema import FormatChecker
from spine_contracts import SCHEMA_VERSION, topics

from monitor.assess_request import build_assess_request
from monitor.db import Store
from monitor.settings import AppSettings

_log = logging.getLogger(__name__)

PROBLEM = "application/problem+json"
UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_FORMATS = FormatChecker()

AstronautId = Annotated[str, Path(pattern=f"^{topics.ASTRONAUT_ID.pattern}$")]
EntityId = Annotated[str, Path(pattern=UUID)]
From = Annotated[str, Query(alias="from", description="Start of the window, inclusive (RFC 3339 with offset).")]
To = Annotated[str, Query(description="End of the window, inclusive (RFC 3339 with offset).")]
Limit = Annotated[int, Query(ge=1, le=500)]


class ReadProblem(Exception):
    def __init__(self, status: int, type_: str, title: str, detail: str, errors: list[dict[str, str]] | None = None) -> None:
        super().__init__(detail)
        self.status, self.type, self.title, self.detail, self.errors = status, type_, title, detail, errors


def _problem(status: int, type_: str, title: str, detail: str | None = None, errors: list[dict[str, str]] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"type": type_, "title": title, "status": status}
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM)


def _not_found(detail: str) -> ReadProblem:
    return ReadProblem(404, "not_found", "Not found", detail)


def _instant(name: str, value: str) -> datetime:
    """RFC 3339 with a mandatory offset, the same rule as `utc_timestamp` in the schemas."""
    if not _FORMATS.conforms(value, "date-time"):
        error = {"pointer": name, "message": "not an RFC 3339 date-time with offset"}
        raise ReadProblem(422, "invalid_request", "Invalid request", f"{name} must be RFC 3339 with offset", [error])
    return datetime.fromisoformat(value)


def _window(start: str, end: str) -> tuple[datetime, datetime]:
    first, last = _instant("from", start), _instant("to", end)
    if last < first:
        raise ReadProblem(422, "invalid_range", "Invalid range", "to is before from", [{"pointer": "to", "message": "must not be before from"}])
    return first, last


def _hours_since(instant: str, now: datetime) -> float:
    return round((now - datetime.fromisoformat(instant)).total_seconds() / 3600, 3)


def _at(entity: dict[str, Any] | None, key: str) -> str | None:
    return None if entity is None else entity[key]


def _freshness(what: str, last_at: str | None, now: datetime, threshold_h: float, device_id: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"what": what}
    if device_id is not None:
        entry["device_id"] = device_id
    age = None if last_at is None else _hours_since(last_at, now)
    entry.update(last_at=last_at, age_hours=age, stale=age is None or age > threshold_h)
    return entry


def create_read_app(
    settings: AppSettings | None = None,
    *,
    store: Store | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    """`store` is injected by tests; otherwise the app's database is opened read-only (and closed on shutdown)."""
    settings = settings or AppSettings()
    owned = store is None
    db = Store(settings.db_path, read_only=True) if store is None else store

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owned:
                db.close()

    app = FastAPI(
        title="Spine app read-only API",
        version="1.0.0",
        description="Read-only API of the on-board app for the MCP server. Contract: contracts/app-openapi.yaml.",
        lifespan=lifespan,
    )
    app.state.store = db

    @app.exception_handler(ReadProblem)
    async def _read_problem(_: Request, exc: ReadProblem) -> JSONResponse:
        return _problem(exc.status, exc.type, exc.title, exc.detail, exc.errors)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"pointer": str(e["loc"][-1]), "message": e["msg"]} for e in exc.errors()]
        return _problem(422, "invalid_request", "Invalid request", errors=errors)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        _log.exception("read-only API error", exc_info=exc)
        return _problem(500, "internal", "Internal error")

    def known(astronaut_id: str) -> str:
        if not db.astronaut_known(astronaut_id):
            raise _not_found(f"unknown astronaut {astronaut_id}")
        return astronaut_id

    def found(entity: dict[str, Any] | None, what: str, entity_id: str) -> dict[str, Any]:
        if entity is None:
            raise _not_found(f"unknown {what} {entity_id}")
        return entity

    def listing(astronaut_id: str, start: str, end: str, limit: int, items: list[dict[str, Any]], truncated: bool) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "astronaut_id": astronaut_id,
            "from": start,
            "to": end,
            "limit": limit,
            "truncated": truncated,
            "items": items,
        }

    @app.get("/health", operation_id="health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/crew/{astronaut_id}/status", operation_id="getStatus")
    async def get_status(astronaut_id: AstronautId) -> dict[str, Any]:
        known(astronaut_id)
        now = clock()
        context = db.daily_context(astronaut_id, now.date().isoformat())
        assessment = db.latest_assessment(astronaut_id)
        session = db.latest_served_session(astronaut_id)
        report = db.latest_self_report_of_any_kind(astronaut_id)
        devices = db.device_statuses(astronaut_id)
        freshness = [
            _freshness("assessment", _at(assessment, "as_of"), now, settings.stale_assessment_hours),
            _freshness("session", _at(session, "started_at"), now, settings.stale_session_hours),
            _freshness("self_report", _at(report, "timestamp"), now, settings.stale_self_report_hours),
            *(_freshness("device", d["timestamp"], now, settings.stale_device_hours, d["device_id"]) for d in devices),
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "astronaut_id": astronaut_id,
            "generated_at": now.isoformat(),
            "today": None if context is None else {k: context[k] for k in ("date", "mission_day", "phase", "gravity_environment") if k in context},
            "latest_assessment": assessment,
            "latest_session": session,
            "latest_self_report": report,
            "devices": devices,
            "active_baseline": _active_baseline(db, astronaut_id, assessment),
            "freshness": freshness,
        }

    @app.get("/crew/{astronaut_id}/history", operation_id="getHistory")
    async def get_history(astronaut_id: AstronautId, start: From, to: To, limit: Limit = 100) -> dict[str, Any]:
        first, last = _window(start, to)
        known(astronaut_id)
        feature_sets, more_features = db.feature_sets_window(astronaut_id, first, last, limit)
        assessments, more_assessments = db.assessments_window(astronaut_id, first, last, limit)
        return {
            "schema_version": SCHEMA_VERSION,
            "astronaut_id": astronaut_id,
            "from": start,
            "to": to,
            "limit": limit,
            "truncated": more_features or more_assessments,
            "feature_sets": feature_sets,
            "assessments": assessments,
        }

    @app.get("/assessments/{assessment_id}/explanation", operation_id="getAssessmentExplanation")
    async def get_explanation(assessment_id: EntityId) -> dict[str, Any]:
        assessment = found(db.assessment(assessment_id), "assessment", assessment_id)
        return _explanation(db, assessment)

    @app.get("/crew/{astronaut_id}/assessments", operation_id="listAssessments")
    async def list_assessments(astronaut_id: AstronautId, start: From, to: To, limit: Limit = 100) -> dict[str, Any]:
        first, last = _window(start, to)
        known(astronaut_id)
        return listing(astronaut_id, start, to, limit, *db.assessments_window(astronaut_id, first, last, limit))

    @app.get("/assessments/{assessment_id}", operation_id="getAssessment")
    async def get_assessment(assessment_id: EntityId) -> dict[str, Any]:
        return found(db.assessment(assessment_id), "assessment", assessment_id)

    @app.get("/crew/{astronaut_id}/sessions", operation_id="listSessions")
    async def list_sessions(astronaut_id: AstronautId, start: From, to: To, limit: Limit = 100) -> dict[str, Any]:
        first, last = _window(start, to)
        known(astronaut_id)
        return listing(astronaut_id, start, to, limit, *db.sessions_window(astronaut_id, first, last, limit))

    @app.get("/sessions/{session_id}", operation_id="getSession")
    async def get_session(session_id: EntityId) -> dict[str, Any]:
        return found(db.served_session(session_id), "session", session_id)

    @app.get("/sessions/{session_id}/feature-set", operation_id="getSessionFeatureSet")
    async def get_session_feature_set(session_id: EntityId) -> dict[str, Any]:
        found(db.served_session(session_id), "session", session_id)
        return found(db.feature_set(session_id), "feature set of session", session_id)

    @app.get("/crew/{astronaut_id}/self-reports", operation_id="listSelfReports")
    async def list_self_reports(astronaut_id: AstronautId, start: From, to: To, limit: Limit = 100) -> dict[str, Any]:
        first, last = _window(start, to)
        known(astronaut_id)
        return listing(astronaut_id, start, to, limit, *db.self_reports_window(astronaut_id, first, last, limit))

    @app.get("/self-reports/{report_id}", operation_id="getSelfReport")
    async def get_self_report(report_id: EntityId) -> dict[str, Any]:
        return found(db.self_report(report_id), "self-report", report_id)

    @app.get("/crew/{astronaut_id}/assess-base", operation_id="getAssessBase")
    async def get_assess_base(astronaut_id: AstronautId) -> dict[str, Any]:
        known(astronaut_id)
        built = build_assess_request(db, astronaut_id, clock(), timedelta(days=settings.assess_window_days))
        if built.request is None:
            raise _not_found(built.skipped or "no_request")
        return built.request

    @app.get("/crew/{astronaut_id}/baselines/{baseline_id}", operation_id="getBaseline")
    async def get_baseline(astronaut_id: AstronautId, baseline_id: EntityId) -> dict[str, Any]:
        baseline = db.baseline(baseline_id)
        if baseline is None or baseline["astronaut_id"] != astronaut_id:
            raise _not_found(f"unknown baseline {baseline_id} of {astronaut_id}")
        return baseline

    return app


def _active_baseline(db: Store, astronaut_id: str, assessment: dict[str, Any] | None) -> dict[str, Any] | None:
    """The baseline the latest assessment was compared against; the latest one when it cites none."""
    cited = (assessment or {}).get("based_on", {}).get("baseline_id")
    baseline = db.baseline(cited) if cited else None
    if baseline is None or baseline["astronaut_id"] != astronaut_id:
        baseline = db.latest_baseline(astronaut_id)
    if baseline is None:
        return None
    return {"baseline_id": baseline["baseline_id"], "version": baseline["version"], "created_at": baseline["created_at"]}


def _explanation(db: Store, assessment: dict[str, Any]) -> dict[str, Any]:
    """The assessment plus what `based_on` points to; whatever no longer exists goes to `unresolved`."""
    astronaut_id = assessment["astronaut_id"]
    based_on = assessment.get("based_on", {})
    unresolved: list[dict[str, str]] = []

    baseline_ref, entries = None, []
    if (baseline_id := based_on.get("baseline_id")) is not None:
        baseline = db.baseline(baseline_id)
        if baseline is None or baseline["astronaut_id"] != astronaut_id:
            unresolved.append({"kind": "baseline", "id": baseline_id})
        else:
            baseline_ref = {"baseline_id": baseline_id, "version": baseline["version"]}
            drivers = {d["feature_id"] for d in assessment["drivers"]}
            entries = [e for e in baseline["entries"] if e["feature_id"] in drivers]

    feature_sets = []
    for session_id in based_on.get("session_ids", []):
        feature_set = db.feature_set(session_id)
        if feature_set is None or feature_set["astronaut_id"] != astronaut_id:
            unresolved.append({"kind": "session", "id": session_id})
        else:
            feature_sets.append(feature_set)

    reports = []
    for report_id in based_on.get("self_report_ids", []):
        report = db.self_report(report_id)
        if report is None or report["astronaut_id"] != astronaut_id:
            unresolved.append({"kind": "self_report", "id": report_id})
        else:
            reports.append(report)

    return {
        "schema_version": SCHEMA_VERSION,
        "assessment": assessment,
        "baseline": baseline_ref,
        "baseline_entries": entries,
        "feature_sets": feature_sets,
        "self_reports": reports,
        "unresolved": unresolved,
    }
