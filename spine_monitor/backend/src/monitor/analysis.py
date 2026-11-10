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
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from spine_contracts import SCHEMA_VERSION, validation

from monitor.db import Store
from monitor.engine import EngineClient, EngineError

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssessOutcome:
    assessment: dict[str, Any] | None = None
    skipped: str | None = None


class Analysis:
    def __init__(
        self,
        store: Store,
        engine: EngineClient,
        *,
        window_days: int,
        clock=lambda: datetime.now(UTC),
        on_assessment: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._store = store
        self._engine = engine
        self._window = timedelta(days=window_days)
        self._clock = clock
        self._on_assessment = on_assessment
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

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
            return
        self._store.transition_session(session_id, ("processing",), "processed")

        async with self._locks[astronaut_id]:
            if session["mission_context"]["phase"] == "preflight" and feature_set["features"]:
                await self._rebuild_baseline(astronaut_id)
            outcome = await self._assess_locked(astronaut_id)
        if outcome.skipped:
            _log.info("session %s: assessment skipped: %s", session_id, outcome.skipped)

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
        return baseline

    # --- assessment ------------------------------------------------------------------------------

    async def assess(self, astronaut_id: str, as_of: datetime | None = None) -> AssessOutcome:
        async with self._locks[astronaut_id]:
            return await self._assess_locked(astronaut_id, as_of)

    async def _assess_locked(self, astronaut_id: str, as_of: datetime | None = None) -> AssessOutcome:
        as_of = as_of or self._clock()
        profile = self._store.profile(astronaut_id)
        if profile is None:
            return AssessOutcome(skipped="no_profile")
        baseline = self._store.latest_baseline(astronaut_id)
        if baseline is None:
            return AssessOutcome(skipped="no_baseline")
        start = as_of - self._window
        feature_sets = self._store.feature_sets_between(astronaut_id, start, as_of)
        if not feature_sets:
            return AssessOutcome(skipped="no_feature_sets")
        contexts = self._store.daily_contexts_between(astronaut_id, start.date().isoformat(), as_of.date().isoformat())
        request: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "astronaut_id": astronaut_id,
            "as_of": as_of.isoformat(),
            "profile": profile,
            "baseline": baseline,
            "feature_sets": feature_sets,
            "self_reports": self._store.self_reports_between(astronaut_id, start, as_of),
            "daily_context": contexts,
        }
        today = next((c for c in contexts if c["date"] == as_of.date().isoformat()), None)
        if today is not None and "mission_day" in today:
            request["mission_day"] = today["mission_day"]
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

    def _announce(self, assessment: dict[str, Any]) -> None:
        """The assessment is already stored: what a listener does wrong must not cost it. Only the type is logged, its text may quote clinical data."""
        if self._on_assessment is None:
            return
        try:
            self._on_assessment(assessment)
        except Exception as exc:
            _log.error("%s: listener of the assessment failed: %s", assessment["astronaut_id"], type(exc).__name__)
