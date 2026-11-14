"""What the app asks the engine for, and when.

- When a session ends: /features with the session, its chunks, the calibrations it cites, the clock
  anchors of its boot_ids and the profile. The FeatureSet is stored.
- If the session is pre-flight: /baseline with every pre-flight feature set of the astronaut (a new
  version each time, `previous_baseline_version` = the latest one).
- Then, and on every self-report: /assess with profile, active baseline, feature sets, self-reports
  and daily context of the last `assess_window_days`.

An assessment that cannot be requested (no profile, no baseline, no features) is never made up: the
reason is returned and nothing is stored, so absence never looks like a green.

Every assessment is stored at one place, whatever triggered it; `on_assessment` hears of each one there.

`request_assess` asks for a debounced reassessment: it runs `debounce_s` after the last request for that astronaut (a burst
makes one), through `assess`, and what it skips or what fails is only logged. A new baseline requests one. An immediate
assessment cancels the one still waiting, since it already reads the current data; `close` cancels them all at shutdown.

A /features failure is final (state `processing_failed`, no retry) unless it is transient: the engine unreachable or
answering 5xx. Then it is also recorded in `processing_attempts` with its next retry time (`backoff_s`), which
`ProcessingRetrier` picks up; a success clears it. On startup `reset_interrupted` puts back the sessions a dead
process left `processing` (before the bus runs) and `process_pending` processes every `ended` one.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from spine_contracts import SCHEMA_VERSION, validation

from monitor.analysis_retry import backoff_s
from monitor.assess_request import build_assess_request
from monitor.db import Store
from monitor.engine import EngineClient, EngineError

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssessOutcome:
    assessment: dict[str, Any] | None = None
    skipped: str | None = None


def _transient(exc: Exception) -> bool:
    """Worth retrying: the engine did not answer (the client wraps the HTTP error) or answered 5xx. A request or
    response that breaks the contract also has no status, but it is not transient."""
    if not isinstance(exc, EngineError):
        return False
    if exc.status is None:
        return isinstance(exc.__cause__, httpx.HTTPError)
    return exc.status >= 500


class Analysis:
    def __init__(
        self,
        store: Store,
        engine: EngineClient,
        *,
        window_days: int,
        clock=lambda: datetime.now(UTC),
        on_assessment: Callable[[dict[str, Any]], None] | None = None,
        retry_min_s: float = 30.0,
        retry_max_s: float = 900.0,
        debounce_s: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._engine = engine
        self._window = timedelta(days=window_days)
        self._clock = clock
        self._on_assessment = on_assessment
        self._retry_min_s = retry_min_s
        self._retry_max_s = retry_max_s
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._debounce_s = debounce_s
        self._sleep = sleep
        # Debounced reassessments: the one still waiting out its window per astronaut, and every one alive (waiting or assessing).
        self._pending: dict[str, asyncio.Task[None]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def reset_interrupted(self) -> list[str]:
        """Startup only, sync and before the bus starts: `processing` sessions were cut by a dead process, so back to
        `ended`. Later it would also reset an analysis that is running. Returns the ids it reset."""
        ids = [row.payload["session_id"] for row in self._store.sessions_in_states(("processing",))]
        return [i for i in ids if self._store.transition_session(i, ("processing",), "ended")]

    async def process_pending(self) -> list[str]:
        """Startup recovery: processes every `ended` session, one after another (each waits for the engine).
        Returns the ids it went through; one already taken by another task is skipped by `process_session`."""
        ids = [row.payload["session_id"] for row in self._store.sessions_in_states(("ended",))]
        for session_id in ids:
            await self.process_session(session_id)
        return ids

    # --- session ended ------------------------------------------------------------------------

    async def process_session(self, session_id: str) -> None:
        """features -> (baseline if pre-flight) -> assess. Only once per session (state `ended`)."""
        if not self._store.transition_session(session_id, ("ended",), "processing"):
            return
        row = self._store.session(session_id)
        assert row is not None
        session = row.payload
        astronaut_id = session["astronaut_id"]
        try:
            feature_set = await self._features(session)
        except (EngineError, ValueError) as exc:
            _log.warning("session %s: features not computed: %s", session_id, exc)
            self._store.transition_session(session_id, ("processing",), "processing_failed", str(exc))
            if _transient(exc):
                self._schedule_retry(session_id, str(exc))
            else:
                self._store.reset_processing_attempts(session_id)
            return
        self._store.transition_session(session_id, ("processing",), "processed")
        self._store.reset_processing_attempts(session_id)

        async with self._locks[astronaut_id]:
            if session["mission_context"]["phase"] == "preflight" and feature_set["features"]:
                await self._rebuild_baseline(astronaut_id)
            outcome = await self._assess_locked(astronaut_id)
        if outcome.skipped:
            _log.info("session %s: assessment skipped: %s", session_id, outcome.skipped)

    def _schedule_retry(self, session_id: str, error: str) -> None:
        attempt = self._store.processing_attempt(session_id)
        delay = backoff_s((attempt.attempts if attempt else 0) + 1, self._retry_min_s, self._retry_max_s)
        self._store.record_processing_failure(session_id, error, self._clock() + timedelta(seconds=delay))

    async def _features(self, session: dict[str, Any]) -> dict[str, Any]:
        astronaut_id, session_id = session["astronaut_id"], session["session_id"]
        validation.validate("domain/session", session)
        chunks = self._store.session_chunks(astronaut_id, session_id)
        if not chunks:
            raise ValueError("no chunks were received for this session")
        calibration_ids = {s["calibration_id"] for s in session["streams"] if "calibration_id" in s}
        calibration_ids |= {c["calibration_id"] for c in chunks if "calibration_id" in c}
        calibrations = self._store.calibrations(calibration_ids)
        missing = calibration_ids - {c["calibration_id"] for c in calibrations}
        if missing:
            # The engine marks what depends on them as `no_calibration`; the session is not blocked.
            _log.warning("session %s: calibrations not received: %s", session_id, sorted(missing))
        anchors = self._store.clock_syncs(astronaut_id, session["device_id"], {c["boot_id"] for c in chunks})
        request: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "session": session,
            "chunks": chunks,
            "calibrations": calibrations,
            "clock_syncs": anchors,
        }
        if (profile := self._store.profile(astronaut_id)) is not None:
            request["profile"] = profile
        feature_set = await self._engine.features(request)
        if feature_set["astronaut_id"] != astronaut_id or feature_set["session_id"] != session_id:
            raise EngineError("engine returned a feature set of another session")
        self._store.put_feature_set(feature_set, phase=session["mission_context"]["phase"], anchor_at=session["started_at"])
        return feature_set

    async def _rebuild_baseline(self, astronaut_id: str) -> dict[str, Any] | None:
        feature_sets = self._store.feature_sets_of_phase(astronaut_id, "preflight")
        if not feature_sets:
            return None
        request: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "astronaut_id": astronaut_id, "feature_sets": feature_sets}
        anchors = [datetime.fromisoformat((fs.get("window") or {}).get("end", fs["computed_at"])) for fs in feature_sets]
        request["self_reports"] = self._store.self_reports_between(astronaut_id, min(anchors) - timedelta(days=7), max(anchors))
        if (previous := self._store.latest_baseline(astronaut_id)) is not None:
            request["previous_baseline_version"] = previous["version"]
        try:
            baseline = await self._engine.baseline(request)
        except EngineError as exc:
            _log.warning("%s: baseline not rebuilt: %s", astronaut_id, exc)
            return None
        if previous is not None and baseline["version"] <= previous["version"]:
            _log.warning("%s: engine returned baseline version %s, not newer than %s", astronaut_id, baseline["version"], previous["version"])
            return None
        self._store.put_baseline(baseline)
        self.request_assess(astronaut_id)
        return baseline

    async def stretch_estimate(self, request: dict[str, Any]) -> dict[str, Any]:
        """The engine's calculated cord length estimate; outside the assessment (it is neither stored nor sent to /assess)."""
        return await self._engine.stretch_estimate(request)

    # --- assessment ------------------------------------------------------------------------------

    async def assess(self, astronaut_id: str, as_of: datetime | None = None) -> AssessOutcome:
        async with self._locks[astronaut_id]:
            return await self._assess_locked(astronaut_id, as_of)

    async def _assess_locked(self, astronaut_id: str, as_of: datetime | None = None) -> AssessOutcome:
        # It reads the data as it is now, so a debounced reassessment still waiting would only repeat it.
        self._cancel_pending(astronaut_id)
        as_of = as_of or self._clock()
        built = build_assess_request(self._store, astronaut_id, as_of, self._window)
        if built.request is None:
            return AssessOutcome(skipped=built.skipped)
        request = built.request
        try:
            assessment = await self._engine.assess(request)
        except EngineError as exc:
            _log.warning("%s: assessment failed: %s", astronaut_id, exc)
            return AssessOutcome(skipped=f"engine_error: {exc}")
        if assessment["astronaut_id"] != astronaut_id:
            return AssessOutcome(skipped="engine_error: assessment of another astronaut")
        self._store.put_assessment(assessment)
        self._announce(assessment)
        return AssessOutcome(assessment=assessment)

    # --- debounced reassessment -----------------------------------------------------------------

    def request_assess(self, astronaut_id: str) -> None:
        """Assesses the astronaut `debounce_s` after the last request (from within the running loop): a burst makes one
        assessment. What it skips or what fails is logged, never raised."""
        self._cancel_pending(astronaut_id)
        task = asyncio.get_running_loop().create_task(self._reassess_later(astronaut_id), name=f"reassess-{astronaut_id}")
        self._pending[astronaut_id] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def idle(self) -> None:
        """Returns once no debounced reassessment is waiting or running, including those requested meanwhile."""
        while live := [task for task in self._tasks if not task.done()]:
            await asyncio.wait(live)

    async def close(self) -> None:
        """Shutdown: cancels every debounced reassessment, waiting or running, and waits for them to end."""
        self._pending.clear()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _cancel_pending(self, astronaut_id: str) -> None:
        if (pending := self._pending.pop(astronaut_id, None)) is not None:
            pending.cancel()

    async def _reassess_later(self, astronaut_id: str) -> None:
        try:
            await self._debounce(astronaut_id)
            outcome = await self.assess(astronaut_id)
        except Exception as exc:
            _log.error("%s: debounced assessment failed: %s", astronaut_id, type(exc).__name__)  # type only: text may quote health data
            return
        if outcome.skipped:
            _log.info("%s: debounced assessment skipped: %s", astronaut_id, outcome.skipped)

    async def _debounce(self, astronaut_id: str) -> None:
        """Waits out the window, then leaves the pending map. A cancelled one was already taken out by whoever cancelled
        it, and its place may hold the newer request."""
        try:
            await self._sleep(self._debounce_s)
        finally:
            if self._pending.get(astronaut_id) is asyncio.current_task():
                del self._pending[astronaut_id]

    def _announce(self, assessment: dict[str, Any]) -> None:
        """The assessment is already stored: what a listener does wrong must not cost it. Only the type is logged, its text may quote clinical data."""
        if self._on_assessment is None:
            return
        try:
            self._on_assessment(assessment)
        except Exception as exc:
            _log.error("%s: listener of the assessment failed: %s", assessment["astronaut_id"], type(exc).__name__)
