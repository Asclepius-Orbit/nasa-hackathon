"""UI API (`/ui/...`): the stateful part that writes.

Separate from the read-only API of `app-openapi.yaml` (the one of the MCP server), not touched here.
- Profile and daily context: PUT of the full entity.
- Tiered self-reports: daily (quick), weekly Oswestry and red flags, always available. Every
  self-report triggers /assess; `GET .../self-reports/due` says what is due today. A stored profile or
  daily context also asks for an /assess, debounced per astronaut (`Analysis.request_assess`).
- Step-by-step protocol: create a session (start_session), mark events, calibrate and stop. Each step
  returns its command; its state (acks) is read at `GET /ui/commands/{command_id}`.

The app fills in what it owns (ids, `schema_version`, instants, `source`) and validates the result
against the JSON Schema of the entity. Errors are problem details shaped like `app/error`.
- Views (`GET /ui/crew/{id}/views/*`, `GET /ui/sessions/{id}/view`, `ui-openapi.yaml`): the stored data fed to the pure
  builders of `monitor.views`; each result is validated against its schema before it is served.
- Live repetition counter (`GET /ui/sessions/{id}/reps`, `ui-openapi.yaml`): server-sent events from `monitor.live.stream`.
- Ground (`GET /ui/crew/{id}/houston` and `/outbox`, `ui-openapi.yaml`): the outbox that `monitor.ground.sender` empties.
- Crew (`PUT /ui/crew/{id}` registers an id the UI assigned, `GET /ui/crew` lists what is stored for each): ids only; names and
  PINs stay in the UI.
- Medical contacts (`POST/GET /ui/crew/{id}/contacts`): calls to the crew medical officer; each one requeues the ground summary
  of its day. A stop for pain (`aborted_pain`, with the repetition) and an urgent report also requeue it and ask for /assess.
- Houston weekly report (`GET /ui/crew/{id}/houston-report.pdf`, `ui-openapi.yaml`): PDF of the last seven UTC days, built by `monitor.report`.
- Export (`GET /ui/crew/{id}/export`): everything stored for the astronaut but the raw chunks, for the ground or the end of the mission.
- Every route needs the shared token and, when it is about one astronaut, that astronaut unlocked (`monitor.ui_auth`).
"""

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from spine_contracts import SCHEMA_VERSION, topics, validation

from monitor.analysis import Analysis
from monitor.control import CommandError, Control
from monitor.db import SessionRow, Store, day_span
from monitor.engine import EngineError
from monitor.ground.link import GroundWindows, link_state
from monitor.live.stream import RepEventResponse, RepStream
from monitor.live.tracker import has_live_reps
from monitor.models import CalibrateBody, EventBody, StopBody
from monitor.report.houston_pdf import render_pdf
from monitor.report.week import build_week_report, week_start
from monitor.settings import AppSettings
from monitor.ui_auth import AuthProblem
from monitor.views.baseline import build_baseline, build_first_day
from monitor.views.cord import (
    CORD_PROTOCOLS,
    EstimateRequest,
    Measurements,
    Outcome,
    build_cord_history,
    build_cord_protocols,
    collect_measurements,
    due_protocols,
    estimate_request,
    estimate_view,
)
from monitor.views.due import reports_due
from monitor.views.history import build_history
from monitor.views.houston import build_houston, build_outbox
from monitor.views.lumbar import ELONGATION_FEATURE, instant, latest_lumbar_delta
from monitor.views.mission import launch_date_of, mission_day_of, mission_for
from monitor.views.protocol_catalog import protocol_entry
from monitor.views.session import build_session_view
from monitor.views.strip import build_strip, minutes_since
from monitor.views.tasks import tasks_for
from monitor.views.today import build_today

logger = logging.getLogger(__name__)

PROBLEM = "application/problem+json"
TITLES = {
    "invalid_request": "Invalid request",
    "not_found": "Not found",
    "no_reps": "No repetitions",
    "conflict": "Conflict",
    "unavailable": "Service unavailable",
    "unauthorized": "Unauthorized",
    "forbidden": "Forbidden",
    "internal": "Internal error",
}

CONTRACT_PROBLEMS = ("invalid_request", "invalid_range", "not_found", "no_reps", "unsupported_schema_version", "internal")


class Problem(Exception):
    def __init__(self, status: int, detail: str, *, type_: str = "invalid_request", errors: list[dict[str, str]] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.type = type_
        self.errors = errors


def problem_response(status: int, type_: str, detail: str | None = None, errors: list[dict[str, str]] | None = None) -> JSONResponse:
    # `app/error` only enumerates these types; a conflict or a bus outage is expressed by the status.
    contract_type = type_ if type_ in CONTRACT_PROBLEMS else "invalid_request"
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

    @app.exception_handler(AuthProblem)
    async def _auth(_: Request, exc: AuthProblem) -> JSONResponse:
        response = problem_response(exc.status, "unauthorized" if exc.status == 401 else "forbidden", exc.detail)
        if exc.status == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        return response

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


_DAY = timedelta(days=1)
# Longest history window (`to` - `from` + 1 days); a longer one is an `invalid_range` (ui-openapi.yaml).
MAX_HISTORY_DAYS = 366
# The engine answers 422 `invalid_request` both for a resistance outside the measured curve and for inputs the model cannot represent;
# only the detail tells them apart (the contract has no dedicated type), so it is matched here.
_OUT_OF_MODEL_RANGE_DETAIL = "outside the measured curve"


def _is_out_of_model_range(exc: EngineError) -> bool:
    return exc.status == 422 and _OUT_OF_MODEL_RANGE_DETAIL in str((exc.problem or {}).get("detail", ""))

# Entries of the outbox view: the most recent ones. Older ones stay in the database; the screen only follows the current delivery.
OUTBOX_VIEW_LIMIT = 50
# Session states that hold a finished cord measurement attempt (the rest are in progress).
CORD_HISTORY_STATES = ("processed", "processing_failed", "rejected")


@dataclass(frozen=True)
class ViewSettings:
    """What the UI views need from the app settings."""

    baseline_required: int
    baseline_plan_offsets_days: tuple[int, ...]
    baseline_protocol_id: str
    history_window_days: int
    stale_session_hours: float
    stale_device_hours: float
    ground_windows: GroundWindows | None
    cord_follow_up_interval_days: int = 7
    cord_min_quality_score: float = 0.5

    @classmethod
    def from_app(cls, settings: AppSettings) -> ViewSettings:
        return cls(
            settings.baseline_required,
            tuple(settings.baseline_plan_offsets_days),
            settings.baseline_protocol_id,
            settings.history_window_days,
            settings.stale_session_hours,
            settings.stale_device_hours,
            settings.ground_windows,
            settings.cord_follow_up_interval_days,
            settings.cord_min_quality_score,
        )


def _conforming(schema: str, view: dict[str, Any]) -> dict[str, Any]:
    """The view after checking it against its schema: a view that does not conform is a bug and is never served.

    The validation error goes to the log only; the client gets a generic message.
    """
    found = validation.errors(schema, view)
    if found:
        # Only the JSON pointer: the message of a violation quotes the offending value, which may be health data.
        logger.error("the %s view does not conform to its schema at %s", schema, found[0].split(": ", 1)[0])
        raise Problem(500, "the view could not be built", type_="internal")
    return view


def _queue_day(queue_day: Callable[[str, date], None], astronaut_id: str, day: date) -> None:
    """The report is already stored: what the queueing does wrong must not cost it. Only the type is logged, never its text."""
    try:
        queue_day(astronaut_id, day)
    except Exception as exc:
        logger.error("%s: ground summary of %s not queued: %s", astronaut_id, day, type(exc).__name__)


def build_router(
    store: Store,
    control: Control,
    analysis: Analysis,
    *,
    odi_interval_days: int,
    views: ViewSettings,
    reps: RepStream,
    clock=lambda: datetime.now(UTC),
    queue_day: Callable[[str, date], None] = lambda astronaut_id, day: None,
    guard: Callable[[Request], Any] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/ui", tags=["ui"], dependencies=[] if guard is None else [Depends(guard)])

    def today() -> str:
        return clock().date().isoformat()

    # --- crew ------------------------------------------------------------------------------------

    @router.put("/crew/{astronaut_id}")
    async def register_crew(astronaut_id: str) -> JSONResponse:
        """Idempotent: 201 when the id is new, 200 when it was already registered. No body: the backend keeps no name or PIN."""
        created = store.register_crew(_astronaut(astronaut_id), clock())
        return JSONResponse({"astronaut_id": astronaut_id, "registered": True}, status_code=201 if created else 200)

    @router.get("/crew")
    async def crew_view() -> dict[str, Any]:
        return _conforming("app/ui-crew", {"schema_version": SCHEMA_VERSION, "crew": store.crew_overview()})

    @router.get("/crew/{astronaut_id}/export")
    async def export_astronaut(astronaut_id: str) -> dict[str, Any]:
        """Everything stored for the astronaut except the raw chunks; an astronaut with nothing gets empty lists and nulls."""
        return {"schema_version": SCHEMA_VERSION, "exported_at": clock().isoformat(), **store.export_astronaut(_astronaut(astronaut_id))}

    # --- medical contacts --------------------------------------------------------------------------

    @router.post("/crew/{astronaut_id}/contacts", status_code=201)
    async def post_contact(astronaut_id: str, request: Request) -> JSONResponse:
        body = await _body(request)
        _same(body, "astronaut_id", _astronaut(astronaut_id))
        contact = {"schema_version": SCHEMA_VERSION, "contact_id": str(uuid.uuid4()), "at": clock().isoformat(), **body, "astronaut_id": astronaut_id}
        _schema_errors("domain/medical-contact", contact)
        if (existing := store.contact(contact["contact_id"])) is not None:
            # UI retry with the same contact_id: idempotent when it is the same contact.
            if {k: v for k, v in existing.items() if k != "at" or "at" in body} != {k: v for k, v in contact.items() if k != "at" or "at" in body}:
                raise Problem(409, f"contact {contact['contact_id']} already exists with other content", type_="conflict")
            return JSONResponse(existing, status_code=200)
        store.put_contact(contact)
        _queue_day(queue_day, astronaut_id, instant(contact["at"]).date())
        return JSONResponse(contact, status_code=201)

    @router.get("/crew/{astronaut_id}/contacts")
    async def contacts_view(astronaut_id: str) -> dict[str, Any]:
        view = {"schema_version": SCHEMA_VERSION, "astronaut_id": _astronaut(astronaut_id), "contacts": store.contacts(astronaut_id)}
        return _conforming("app/ui-contact-list", view)

    # --- devices ----------------------------------------------------------------------------

    @router.get("/crew/{astronaut_id}/devices")
    async def devices(astronaut_id: str) -> list[dict[str, Any]]:
        return store.device_statuses(_astronaut(astronaut_id))

    # --- profile and daily context ---------------------------------------------------------------

    def context_etag(context: dict[str, Any]) -> str:
        encoded = json.dumps(context, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return '"' + hashlib.sha256(encoded).hexdigest() + '"'

    @router.put("/crew/{astronaut_id}/profile")
    async def put_profile(
        astronaut_id: str,
        request: Request,
        if_none_match: Annotated[str | None, Header()] = None,
        if_match: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        if if_none_match not in (None, "*") or (if_none_match is not None and if_match is not None):
            raise Problem(422, "Invalid conditional headers")
        body = await _body(request)
        _same(body, "astronaut_id", _astronaut(astronaut_id))
        profile = {"schema_version": SCHEMA_VERSION, "updated_at": clock().isoformat(), **body, "astronaut_id": astronaut_id}
        _schema_errors("domain/astronaut-profile", profile)
        if if_match is not None:
            previous = store.profile(astronaut_id)
            if previous is None or context_etag(previous) != if_match or not store.replace_profile_if_unchanged(profile, previous):
                raise Problem(412, "profile changed", type_="conflict")
        elif if_none_match is not None:
            if not store.create_profile(profile):
                raise Problem(412, "profile already exists", type_="conflict")
        else:
            store.put_profile(profile)
        analysis.request_assess(astronaut_id)
        return profile

    @router.get("/crew/{astronaut_id}/profile")
    async def get_profile(astronaut_id: str, response: Response) -> dict[str, Any]:
        profile = store.profile(_astronaut(astronaut_id))
        if profile is None:
            raise Problem(404, f"no profile for {astronaut_id}", type_="not_found")
        response.headers["ETag"] = context_etag(profile)
        return profile

    @router.put("/crew/{astronaut_id}/daily-context/{day}")
    async def put_daily_context(
        astronaut_id: str,
        day: date,
        request: Request,
        if_none_match: Annotated[str | None, Header()] = None,
        if_match: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        if if_match is not None and if_none_match is not None:
            raise Problem(422, "Cannot combine If-Match and If-None-Match")
        if if_none_match not in (None, "*"):
            raise Problem(422, "If-None-Match only accepts *", errors=[{"pointer": "If-None-Match", "message": "only * is accepted"}])
        body = await _body(request)
        _same(body, "astronaut_id", _astronaut(astronaut_id))
        _same(body, "date", day.isoformat())
        context = {"schema_version": SCHEMA_VERSION, **body, "astronaut_id": astronaut_id, "date": day.isoformat()}
        _schema_errors("domain/daily-context", context)
        if if_match is not None:
            previous = store.daily_context(astronaut_id, day.isoformat())
            if previous is None or context_etag(previous) != if_match or not store.replace_daily_context_if_unchanged(context, previous):
                raise Problem(412, "daily context changed", type_="conflict")
        elif if_none_match is None:
            store.put_daily_context(context)
        elif not store.create_daily_context(context):
            # Only create (the UI's automatic context): a context stored meanwhile, by hand or by another client, wins.
            raise Problem(412, f"a daily context is already stored for {astronaut_id} on {day}", type_="conflict")
        analysis.request_assess(astronaut_id)
        return context

    @router.get("/crew/{astronaut_id}/daily-context/{day}")
    async def get_daily_context(astronaut_id: str, day: date, response: Response) -> dict[str, Any]:
        context = store.daily_context(_astronaut(astronaut_id), day.isoformat())
        if context is None:
            raise Problem(404, f"no daily context for {astronaut_id} on {day}", type_="not_found")
        response.headers["ETag"] = context_etag(context)
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
        if (report.get("red_flags") or report.get("other_symptoms")) and outcome.assessment is None:
            # Nothing was announced (assess skipped): an urgent report must still reach the ground. With an assessment, its listener queued it.
            _queue_day(queue_day, astronaut_id, instant(report["timestamp"]).date())
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
        daily_due, odi_due = reports_due(daily, odi, now, odi_interval_days)
        return {
            "date": now.date().isoformat(),
            "daily_due": daily_due,
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

    def _announce_pain_stop(row: SessionRow) -> None:
        """Like an urgent report: the ground hears of it now, and the assessment is redone (debounced) with it.

        The day is the one the session started in, the only one whose summary lists the session."""
        astronaut_id = row.payload["astronaut_id"]
        _queue_day(queue_day, astronaut_id, instant(row.payload["started_at"]).date())
        analysis.request_assess(astronaut_id)

    @router.post("/sessions/{session_id}/stop", status_code=202)
    async def stop_session(session_id: str, body: StopBody) -> dict[str, Any]:
        row = store.session(session_id)
        try:
            command = await control.stop_session(session_id, body.reason, body.rep_index)
        except CommandError as exc:
            # The pain is stored even when the bus is down (the event goes in before the send): the ground and the assessment must hear of it.
            if body.reason == "aborted_pain" and exc.status == 503 and row is not None:
                _announce_pain_stop(row)
            raise
        if body.reason == "aborted_pain" and row is not None:
            _announce_pain_stop(row)
        return {"command": command}

    @router.post("/sessions/{session_id}/reprocess", status_code=202)
    async def reprocess_session(session_id: str) -> dict[str, Any]:
        """Asks again for the analysis of a `processing_failed` session; it runs in the background (`control.spawn`)."""
        row = store.session(session_id)
        if row is None:
            raise Problem(404, f"unknown session {session_id}", type_="not_found")
        if row.state != "processing_failed":
            raise Problem(409, f"session {session_id} is {row.state}: only a processing_failed session can be reprocessed", type_="conflict")
        if not store.transition_session(session_id, ("processing_failed",), "ended"):
            raise Problem(409, f"session {session_id} changed state meanwhile", type_="conflict")
        store.reset_processing_attempts(session_id)
        control.spawn(analysis.process_session(session_id))
        return {"session": session_id, "state": "ended"}

    @router.post("/sessions/{session_id}/events", status_code=202)
    async def mark_event(session_id: str, body: EventBody) -> dict[str, Any]:
        return {"command": await control.mark_event(session_id, body.event_type, body.rep_index, body.note, body.set_index)}

    @router.post("/sessions/{session_id}/calibrations", status_code=202)
    async def calibrate(session_id: str, body: CalibrateBody) -> dict[str, Any]:
        return {"command": await control.calibrate(session_id, body.calibration_type, body.stream_ids, body.duration_s)}

    @router.get("/commands/{command_id}")
    async def get_command(command_id: str) -> dict[str, Any]:
        command = store.command(command_id)
        if command is None:
            raise Problem(404, f"unknown command {command_id}", type_="not_found")
        return {"command": command.payload, "state": command.state, "acks": command.acks}

    # --- views (ui-openapi.yaml) --------------------------------------------------------------------

    def baseline_view(astronaut_id: str, profile: dict[str, Any] | None) -> dict[str, Any]:
        return build_baseline(
            store.latest_baseline(astronaut_id),
            store.session_rows(astronaut_id, phase="preflight"),
            launch_date_of(profile),
            views.baseline_required,
            views.baseline_plan_offsets_days,
            views.baseline_protocol_id,
        )

    def open_tasks(astronaut_id: str, profile: dict[str, Any] | None, baseline: dict[str, Any], now: datetime) -> list[str]:
        daily_due, odi_due = reports_due(
            store.latest_self_report(astronaut_id, "daily"), store.latest_self_report(astronaut_id, "weekly_odi"), now, odi_interval_days
        )
        baseline_pending = mission_for(profile, now.date())["phase"] == "preflight" and baseline["recorded"] < baseline["required"]
        start, end = day_span(now.date(), now.date())
        session_today = any(row.state != "rejected" for row in store.session_rows(astronaut_id, start=start, end=end))
        cord_due = due_protocols(cord_measurements(astronaut_id), mission_for(profile, now.date())["phase"], now, views.cord_follow_up_interval_days)
        return list(tasks_for(daily_due, odi_due, baseline_pending, session_today, cord_due))

    def cord_measurements(astronaut_id: str) -> Measurements:
        """Valid cord measurements from the sessions already analysed: (session, feature set) pairs of the cord protocols."""
        rows = [(row.payload, store.feature_set(row.payload["session_id"])) for row in store.session_rows(astronaut_id) if row.state == "processed"]
        pairs = [(session, fs) for session, fs in rows if session["protocol_id"] in CORD_PROTOCOLS and fs is not None]
        return collect_measurements(pairs, views.cord_min_quality_score)

    async def cord_estimate(protocol_id: str, measurements: Measurements) -> Outcome:
        asked = estimate_request(protocol_id, measurements[protocol_id])
        if isinstance(asked, str):
            return asked
        return await _ask_estimate(asked)

    async def _ask_estimate(asked: EstimateRequest) -> Outcome:
        try:
            return estimate_view(asked, await analysis.stretch_estimate(asked.body))
        except EngineError as exc:
            return "ESTIMATE_OUT_OF_MODEL_RANGE" if _is_out_of_model_range(exc) else "ESTIMATE_ENGINE_UNAVAILABLE"

    @router.get("/crew/{astronaut_id}/views/strip")
    async def strip_view(
        astronaut_id: str, device_id: Annotated[str | None, Query(pattern=f"^{topics.DEVICE_ID.pattern}$")] = None
    ) -> dict[str, Any]:
        _astronaut(astronaut_id)
        view = build_strip(
            astronaut_id,
            store.profile(astronaut_id),
            store.device_statuses(astronaut_id),
            store.latest_self_report(astronaut_id, "daily"),
            store.latest_self_report(astronaut_id, "weekly_odi"),
            clock(),
            odi_interval_days,
            views.stale_device_hours,
            link_state(views.ground_windows, clock()),
            minutes_since(store.last_ground_contact(astronaut_id), clock()),
            device_id,
        )
        return _conforming("app/ui-strip", view)

    @router.get("/crew/{astronaut_id}/views/today")
    async def today_view(astronaut_id: str) -> dict[str, Any]:
        _astronaut(astronaut_id)
        now = clock()
        profile = store.profile(astronaut_id)
        # "Recent" measures: the history window ending now; an older measure no longer says how the spine is today.
        recent = store.feature_sets_between(astronaut_id, now - timedelta(days=views.history_window_days), now)
        lumbar = latest_lumbar_delta(recent, store.latest_baseline(astronaut_id))
        last = store.latest_session(astronaut_id)
        in_progress = None if last is None else (last, protocol_entry(last.payload["protocol_id"]))
        tasks = open_tasks(astronaut_id, profile, baseline_view(astronaut_id, profile), now)
        view = build_today(store.latest_assessment(astronaut_id), lumbar, in_progress, tasks, now, views.stale_session_hours)
        return _conforming("app/ui-today", view)

    @router.get("/crew/{astronaut_id}/views/history")
    async def history_view(
        astronaut_id: str, from_date: Annotated[date | None, Query(alias="from")] = None, to_date: Annotated[date | None, Query(alias="to")] = None
    ) -> dict[str, Any]:
        _astronaut(astronaut_id)
        now = clock()
        today_date = now.date()
        launch = launch_date_of(store.profile(astronaut_id))
        try:
            last = today_date if to_date is None else to_date
            first = last - timedelta(days=views.history_window_days - 1) if from_date is None else from_date
            if first > last:
                raise Problem(422, f"from ({first}) is after to ({last})", type_="invalid_range")
            if (last - first).days + 1 > MAX_HISTORY_DAYS:
                raise Problem(422, f"the window from {first} to {last} is longer than {MAX_HISTORY_DAYS} days", type_="invalid_range")
            start, end = day_span(first, last)
            # Feature sets are anchored at the start of their session, a day is told by the start of their window: read one day more
            # on each side. The pre-flight range needs the latest measure at any date: one bounded query, not the whole history.
            feature_sets = store.feature_sets_between(astronaut_id, start - _DAY, end + _DAY)
            latest_feature_set = store.latest_feature_set_with(astronaut_id, ELONGATION_FEATURE)
        except OverflowError:
            raise Problem(422, "date out of range", type_="invalid_range") from None
        view = build_history(
            first,
            last,
            today_date,
            lambda day: None if launch is None else mission_day_of(launch, day),
            store.assessments_between(astronaut_id, start, end),
            feature_sets,
            store.self_reports_between(astronaut_id, start, end),
            store.session_rows(astronaut_id, start=start, end=end),
            store.latest_baseline(astronaut_id),
            latest_feature_set,
        )
        return _conforming("app/ui-history", view)

    @router.get("/crew/{astronaut_id}/views/baseline")
    async def baseline_screen(astronaut_id: str) -> dict[str, Any]:
        _astronaut(astronaut_id)
        return _conforming("app/ui-baseline", baseline_view(astronaut_id, store.profile(astronaut_id)))

    @router.get("/crew/{astronaut_id}/views/first-day")
    async def first_day_view(astronaut_id: str) -> dict[str, Any]:
        _astronaut(astronaut_id)
        profile = store.profile(astronaut_id)
        baseline = baseline_view(astronaut_id, profile)
        return _conforming("app/ui-first-day", build_first_day(open_tasks(astronaut_id, profile, baseline, clock()), baseline))

    @router.get("/crew/{astronaut_id}/views/cord-protocols")
    async def cord_protocols_view(astronaut_id: str) -> dict[str, Any]:
        _astronaut(astronaut_id)
        now = clock()
        measurements = cord_measurements(astronaut_id)
        estimates = {protocol: await cord_estimate(protocol, measurements) for protocol in CORD_PROTOCOLS}
        phase = mission_for(store.profile(astronaut_id), now.date())["phase"]
        view = build_cord_protocols(measurements, phase, now, views.cord_follow_up_interval_days, estimates)
        return _conforming("app/ui-cord-protocols", view)

    @router.get("/crew/{astronaut_id}/views/cord-history")
    async def cord_history_view(astronaut_id: str, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> dict[str, Any]:
        _astronaut(astronaut_id)
        rows = store.session_rows(astronaut_id)
        pairs: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        for protocol in CORD_PROTOCOLS:
            # Newest first and only as many sessions as needed: the history is never loaded whole. Sessions still in progress are not
            # measurements yet; one without feature set (rejected, or whose analysis failed) is listed as invalid.
            cord = [row for row in reversed(rows) if row.payload["protocol_id"] == protocol and row.state in CORD_HISTORY_STATES]
            pairs.extend((row.payload, store.feature_set(row.payload["session_id"])) for row in cord[:limit])
        return _conforming("app/ui-cord-history", build_cord_history(pairs, views.cord_min_quality_score, limit))

    @router.get("/crew/{astronaut_id}/houston")
    async def houston_view(astronaut_id: str) -> dict[str, Any]:
        _astronaut(astronaut_id)
        view = build_houston(
            astronaut_id, store.latest_ground_summary(astronaut_id), link_state(views.ground_windows, clock()), store.last_ground_contact(astronaut_id)
        )
        return _conforming("app/ui-houston", view)

    @router.get("/crew/{astronaut_id}/outbox")
    async def outbox_view(astronaut_id: str) -> dict[str, Any]:
        _astronaut(astronaut_id)
        items = store.ground_outbox_items(astronaut_id, OUTBOX_VIEW_LIMIT)
        view = build_outbox(astronaut_id, items, link_state(views.ground_windows, clock()), store.last_ground_contact(astronaut_id))
        return _conforming("app/ui-outbox", view)

    @router.get("/crew/{astronaut_id}/houston-report.pdf")
    async def houston_report(astronaut_id: str, end: date | None = None) -> Response:
        _astronaut(astronaut_id)
        now = clock()
        last = now.date() if end is None else end
        if last > now.date():
            raise Problem(422, f"end ({last}) is in the future", type_="invalid_range")
        first = week_start(last)
        start, stop = day_span(first, last)
        launch = launch_date_of(store.profile(astronaut_id))
        week = build_week_report(
            astronaut_id,
            first,
            last,
            now,
            lambda day: None if launch is None else mission_day_of(launch, day),
            store.assessments_between(astronaut_id, start, stop),
            store.self_reports_between(astronaut_id, start, stop),
            store.session_rows(astronaut_id, start=start, end=stop),
            store.feature_sets_between(astronaut_id, start - _DAY, stop + _DAY),
        )
        pdf = await asyncio.to_thread(render_pdf, week)  # matplotlib and reportlab are CPU work: off the event loop
        name = f"houston-report-{astronaut_id}-{first}_{last}.pdf"
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    def known_session(session_id: str) -> SessionRow:
        try:
            canonical = str(uuid.UUID(session_id))
        except ValueError:
            canonical = ""
        if canonical != session_id:
            raise Problem(422, f"invalid session_id {session_id!r}: must be a lowercase UUID")
        row = store.session(session_id)
        if row is None:
            raise Problem(404, f"unknown session {session_id}", type_="not_found")
        return row

    @router.get("/sessions/{session_id}/view")
    async def session_view(session_id: str) -> dict[str, Any]:
        row = known_session(session_id)
        payload = row.payload
        view = build_session_view(
            row,
            store.session_commands(session_id, "calibrate"),
            store.device_status(payload["astronaut_id"], payload["device_id"]),
            protocol_entry(payload["protocol_id"]),
            clock(),
            views.stale_device_hours,
        )
        return _conforming("app/ui-session", view)

    @router.get("/sessions/{session_id}/reps", status_code=200, response_class=RepEventResponse)
    async def rep_events(session_id: str) -> RepEventResponse:
        if not has_live_reps(known_session(session_id).payload):
            raise Problem(409, f"session {session_id} has no repetitions to count live", type_="no_reps")
        return RepEventResponse(reps.events(session_id))

    return router
