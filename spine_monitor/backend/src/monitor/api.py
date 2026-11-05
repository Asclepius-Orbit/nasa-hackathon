"""UI API (`/ui/...`): the stateful part that writes.

Separate from the read-only API of `app-openapi.yaml` (the one of the MCP server), not touched here.
- Profile and daily context: PUT of the full entity.
- Tiered self-reports: daily (quick), weekly Oswestry and red flags, always available. Every
  self-report triggers /assess; `GET .../self-reports/due` says what is due today.
- Step-by-step protocol: create a session (start_session), mark events, calibrate and stop. Each step
  returns its command; its state (acks) is read at `GET /ui/commands/{command_id}`.

The app fills in what it owns (ids, `schema_version`, instants, `source`) and validates the result
against the JSON Schema of the entity. Errors are problem details shaped like `app/error`.
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from spine_contracts import SCHEMA_VERSION, topics, validation

from monitor.analysis import Analysis
from monitor.control import CommandError, Control
from monitor.db import Store
from monitor.models import CalibrateBody, EventBody, StopBody

PROBLEM = "application/problem+json"
TITLES = {
    "invalid_request": "Invalid request",
    "not_found": "Not found",
    "conflict": "Conflict",
    "unavailable": "Service unavailable",
    "internal": "Internal error",
}


class Problem(Exception):
    def __init__(self, status: int, detail: str, *, type_: str = "invalid_request", errors: list[dict[str, str]] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.type = type_
        self.errors = errors


def problem_response(status: int, type_: str, detail: str | None = None, errors: list[dict[str, str]] | None = None) -> JSONResponse:
    # `app/error` only enumerates these types; a conflict or a bus outage is expressed by the status.
    contract_type = type_ if type_ in ("invalid_request", "invalid_range", "not_found", "unsupported_schema_version", "internal") else "invalid_request"
    body: dict[str, Any] = {"type": contract_type, "title": TITLES.get(type_, "Error"), "status": status}
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    async def _problem(_: Request, exc: Problem) -> JSONResponse:
        return problem_response(exc.status, exc.type, exc.detail, exc.errors)

    @app.exception_handler(CommandError)
    async def _command(_: Request, exc: CommandError) -> JSONResponse:
        kind = {404: "not_found", 409: "conflict", 503: "unavailable"}.get(exc.status, "invalid_request")
        return problem_response(exc.status, kind, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"pointer": "/" + "/".join(map(str, e["loc"][1:] if e["loc"][:1] == ("body",) else e["loc"])), "message": e["msg"]} for e in exc.errors()]
        return problem_response(422, "invalid_request", errors=errors)


def _schema_errors(schema: str, payload: dict[str, Any]) -> None:
    found = validation.errors(schema, payload)
    if found:
        errors = [{"pointer": e.split(": ", 1)[0], "message": e.split(": ", 1)[-1]} for e in found]
        raise Problem(422, f"does not conform to {schema}", errors=errors)


async def _body(request: Request) -> dict[str, Any]:
    try:
        payload = json.loads(await request.body())
    except ValueError:
        raise Problem(422, "The body is not valid JSON.") from None
    if not isinstance(payload, dict):
        raise Problem(422, "The body must be a JSON object.")
    return payload


def _astronaut(astronaut_id: str) -> str:
    if not topics.ASTRONAUT_ID.fullmatch(astronaut_id):
        raise Problem(422, f"invalid astronaut_id {astronaut_id!r}: must be a pseudonym like AST-01")
    return astronaut_id


def _same(body: dict[str, Any], key: str, value: Any) -> None:
    if key in body and body[key] != value:
        raise Problem(422, f"{key} in the body ({body[key]!r}) does not match the path ({value!r})")


def build_router(store: Store, control: Control, analysis: Analysis, *, odi_interval_days: int, clock=lambda: datetime.now(UTC)) -> APIRouter:
    router = APIRouter(prefix="/ui", tags=["ui"])

    def today() -> str:
        return clock().date().isoformat()

    # --- devices ----------------------------------------------------------------------------

    @router.get("/crew/{astronaut_id}/devices")
    async def devices(astronaut_id: str) -> list[dict[str, Any]]:
        return store.device_statuses(_astronaut(astronaut_id))

    # --- profile and daily context ---------------------------------------------------------------

    @router.put("/crew/{astronaut_id}/profile")
    async def put_profile(astronaut_id: str, request: Request) -> dict[str, Any]:
        body = await _body(request)
        _same(body, "astronaut_id", _astronaut(astronaut_id))
        profile = {"schema_version": SCHEMA_VERSION, "updated_at": clock().isoformat(), **body, "astronaut_id": astronaut_id}
        _schema_errors("domain/astronaut-profile", profile)
        store.put_profile(profile)
        return profile

    @router.get("/crew/{astronaut_id}/profile")
    async def get_profile(astronaut_id: str) -> dict[str, Any]:
        profile = store.profile(_astronaut(astronaut_id))
        if profile is None:
            raise Problem(404, f"no profile for {astronaut_id}", type_="not_found")
        return profile

    @router.put("/crew/{astronaut_id}/daily-context/{day}")
    async def put_daily_context(astronaut_id: str, day: date, request: Request) -> dict[str, Any]:
        body = await _body(request)
        _same(body, "astronaut_id", _astronaut(astronaut_id))
        _same(body, "date", day.isoformat())
        context = {"schema_version": SCHEMA_VERSION, **body, "astronaut_id": astronaut_id, "date": day.isoformat()}
        _schema_errors("domain/daily-context", context)
        store.put_daily_context(context)
        return context

    @router.get("/crew/{astronaut_id}/daily-context/{day}")
    async def get_daily_context(astronaut_id: str, day: date) -> dict[str, Any]:
        context = store.daily_context(_astronaut(astronaut_id), day.isoformat())
        if context is None:
            raise Problem(404, f"no daily context for {astronaut_id} on {day}", type_="not_found")
        return context

    # --- self-reports ----------------------------------------------------------------------------

    @router.post("/crew/{astronaut_id}/self-reports", status_code=201)
    async def post_self_report(astronaut_id: str, request: Request) -> JSONResponse:
        body = await _body(request)
        _same(body, "astronaut_id", _astronaut(astronaut_id))
        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "report_id": str(uuid.uuid4()),
            "timestamp": clock().isoformat(),
            "source": "device",
            **body,
            "astronaut_id": astronaut_id,
        }
        if "mission_day" not in report:
            context = store.daily_context(astronaut_id, today())
            if context is not None and "mission_day" in context:
                report["mission_day"] = context["mission_day"]
        _schema_errors("domain/self-report", report)
        if (existing := store.self_report(report["report_id"])) is not None:
            # UI retry with the same report_id: idempotent when it is the same report.
            def content(r: dict[str, Any]) -> dict[str, Any]:
                return {k: v for k, v in r.items() if k != "timestamp" or "timestamp" in body}

            if content(existing) != content(report):
                raise Problem(409, f"report {report['report_id']} already exists with other content", type_="conflict")
            return JSONResponse({"report": existing, "assessment": None, "assessment_skipped": "duplicate"}, status_code=200)
        store.put_self_report(report)
        outcome = await analysis.assess(astronaut_id)
        return JSONResponse(
            {"report": report, "assessment": outcome.assessment, "assessment_skipped": outcome.skipped},
            status_code=201,
        )

    @router.get("/crew/{astronaut_id}/self-reports/due")
    async def self_reports_due(astronaut_id: str) -> dict[str, Any]:
        _astronaut(astronaut_id)
        now = clock()
        daily = store.latest_self_report(astronaut_id, "daily")
        odi = store.latest_self_report(astronaut_id, "weekly_odi")
        daily_today = daily is not None and datetime.fromisoformat(daily["timestamp"]).astimezone(UTC).date() == now.date()
        odi_due = odi is None or datetime.fromisoformat(odi["timestamp"]) <= now - timedelta(days=odi_interval_days)
        return {
            "date": now.date().isoformat(),
            "daily_due": not daily_today,
            "weekly_odi_due": odi_due,
            "red_flag_available": True,
            "last_daily_at": None if daily is None else daily["timestamp"],
            "last_weekly_odi_at": None if odi is None else odi["timestamp"],
        }

    # --- sessions and protocol --------------------------------------------------------------------

    @router.post("/crew/{astronaut_id}/sessions", status_code=202)
    async def start_session(astronaut_id: str, request: Request) -> JSONResponse:
        body = await _body(request)
        _same(body, "astronaut_id", _astronaut(astronaut_id))
        max_duration_s = body.pop("max_duration_s", None)
        for field in ("session_id", "started_at", "ended_at", "end_reason", "events"):
            if field in body:
                raise Problem(422, f"{field} is set by the app, not by the UI")
        device_id = body.get("device_id")
        if not isinstance(device_id, str) or not topics.DEVICE_ID.fullmatch(device_id):
            raise Problem(422, "device_id is required", errors=[{"pointer": "/device_id", "message": "required"}])
        if "mission_context" not in body:
            context = store.daily_context(astronaut_id, today())
            if context is None:
                raise Problem(422, "no mission_context: send it or record today's daily context first")
            body["mission_context"] = {k: context[k] for k in ("mission_day", "phase", "gravity_environment") if k in context}
        status = store.device_status(astronaut_id, device_id)
        session = {
            "schema_version": SCHEMA_VERSION,
            "session_id": str(uuid.uuid4()),
            "astronaut_id": astronaut_id,
            "source": status["source"] if status is not None else "device",
            "started_at": clock().isoformat(),
            "ended_at": None,
            "events": [],
            **body,
        }
        _schema_errors("domain/session", session)
        if max_duration_s is not None and (not isinstance(max_duration_s, int) or max_duration_s < 1):
            raise Problem(422, "max_duration_s must be a positive integer")
        command = await control.start_session(session, max_duration_s=max_duration_s)
        return JSONResponse({"session": session, "state": "starting", "command": command}, status_code=202)

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        row = store.session(session_id)
        if row is None:
            raise Problem(404, f"unknown session {session_id}", type_="not_found")
        return {"session": row.payload, "state": row.state, "error": row.error, "feature_set": store.feature_set(session_id)}

    @router.post("/sessions/{session_id}/stop", status_code=202)
    async def stop_session(session_id: str, body: StopBody) -> dict[str, Any]:
        return {"command": await control.stop_session(session_id, body.reason)}

    @router.post("/sessions/{session_id}/events", status_code=202)
    async def mark_event(session_id: str, body: EventBody) -> dict[str, Any]:
        return {"command": await control.mark_event(session_id, body.event_type, body.rep_index, body.note)}

    @router.post("/sessions/{session_id}/calibrations", status_code=202)
    async def calibrate(session_id: str, body: CalibrateBody) -> dict[str, Any]:
        return {"command": await control.calibrate(session_id, body.calibration_type, body.stream_ids, body.duration_s)}

    @router.get("/commands/{command_id}")
    async def get_command(command_id: str) -> dict[str, Any]:
        command = store.command(command_id)
        if command is None:
            raise Problem(404, f"unknown command {command_id}", type_="not_found")
        return {"command": command.payload, "state": command.state, "acks": command.acks}

    return router
