"""Mission generator: lazy iterator of `Envelope`s, bit-for-bit reproducible per branch.

`MissionGenerator`:
- `iter_messages`: profiles (at the start, `at` = start_date 00:00 UTC) and then day by day. Within each
  day, the messages of all astronauts merged by `at` (stable merge in `crew` order).
  Per astronaut and day, in `at` order (ties by production order): daily_context and daily ground truth
  (latent_state, device_events) at 00:00; clock_sync at each boot and every
  `device.clock_sync_interval_s` (retained); device_status (retained) at power-on, every
  `device.status_interval_s` and an `offline` one at power-off; per session, the Session record and the
  calibrations registered in it at its start (each also over MQTT: topic
  `topics.calibration`, retained, see `calibration_envelope`), the stream_chunks at their `received_at` and the ground
  truth of the session (quality_faults, lost_chunks, expected_features) at its end; the self_report at its time.
- `day_messages` / `session_messages`: the same output as within `iter_messages`, regenerated on its own
  (a single day or session gives exactly the same payloads, in the same relative order). With
  `include_carried_calibrations` (default) the carried calibrations also come first: those
  registered earlier (another day or, in a session, outside it) that are still in force and that the output cites, with
  the same envelope the full mission emitted (original ids, created_at, `at` and `day`). So a single day or
  session resolves on its own. `iter_messages` does not repeat them: they already came out on their day.
- `resimulate`: the session with another gravity (delegates to `sessions.resimulate`).
- `calibration_in_force`: the calibration in force for a stream (the latest live one; otherwise the mission's
  scheduled one for that day). Used by the live blocks and the gateway.
- Live API for the gateway: `live_session`, `live_calibration` and `live_background`. Each live block
  looks up the calibration in force when it starts, so after a `calibrate` the chunks cite the new one from the
  next block.
- Chunks: sessions use `signals.chunk_ms`; the background, `schedule.background.chunk_ms`. Each span carries
  its `chunk_ms` (SessionPlan / SynthSpec) and it is passed to `SimDevice.chunks` / `advance`; the background pause
  is aligned to its own grid.
Topics with `spine_contracts.topics`. `source` always `simulator`.

How an astronaut-day is walked (what guarantees per-branch reproducibility):
- The device starts clean each day (`plan_device_day` + new `SimDevice`): nothing is carried from
  one day to the next except the dose drift of the latents, which is regenerable per day.
- All the day's ClockSyncs are registered at the beginning (an anchor only affects later
  chunks, so the registration order changes nothing).
- Streaming spans (sessions and background spans between pauses) are processed in temporal order on
  the same `SimDevice`; in `session_messages` the spans of other sessions are consumed with
  `SimDevice.advance` (same seq without synthesizing). Each span uses its own RNG
  `(astronaut_id, day, slot, stream_id, "device", k0)`, never the state of another session.
- `SimDevice.chunks` assigns seq and registers battery and `stalled` bursts of the whole span when called;
  the chunks are consumed lazily. In each DeviceStatus the `last_seq` of each stream is corrected
  with the last seq (emitted or lost) whose chunk finished before the status instant.
- The output is reordered by `at` with a heap that is emptied at each status instant: memory
  bounded to one status interval, even if the background lasts hours.
"""

import heapq
import itertools
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

import numpy as np
from spine_contracts import GravityEnvironment, QualityFlag, StreamKind, dump, topics

from spine_physiology import reports, truth
from spine_physiology import sessions as sess
from spine_physiology.config import ExerciseProtocol, MissionConfig
from spine_physiology.device import ChunkOutcome, DevicePlan, SimDevice, plan_device_day
from spine_physiology.envelope import Envelope, EnvelopeKind
from spine_physiology.faults import QualityState
from spine_physiology.rng import RngTree
from spine_physiology.sessions import (
    CalibrationRecord,
    SessionPlan,
    SessionRef,
    SessionSynthesis,
    StreamSynthesis,
    SynthSpec,
)
from spine_physiology.signals import exercise_duration_s, rep_schedule
from spine_physiology.state import DayInfo, LatentModel, MissionTimeline

_US = timedelta(microseconds=1)


def calibration_envelope(astronaut_id: str, device_id: str, calibration: CalibrationRecord, at: datetime) -> Envelope:
    """MQTT envelope of a calibration: `topics.calibration(astronaut_id, device_id, stream_id)`, QoS 1 and
    retained (the one in force for each stream); `day` is the calibration's."""
    return Envelope.of(
        EnvelopeKind.CALIBRATION, calibration.model, at=at, astronaut_id=astronaut_id, day=calibration.day,
        topic=topics.calibration(astronaut_id, device_id, calibration.stream_id), retained=True,
    )  # fmt: skip


def _chunk_start(start: datetime, k: int, samples_per_chunk: int, rate_hz: float) -> datetime:
    """Real instant of the start of chunk k (same arithmetic as `SimDevice`)."""
    return start + timedelta(microseconds=round(k * samples_per_chunk / rate_hz * 1e6))


def _existing_chunk_ends(plan: DevicePlan, start: datetime, n_chunks: int, samples_per_chunk: int, rate_hz: float) -> list[datetime]:
    """Real end of each chunk that exists (device powered on when the chunk starts), with the same
    split into boots as `SimDevice`: each one corresponds to a ChunkOutcome, in order."""
    idx = np.arange(n_chunks, dtype=np.int64)
    start_us = np.rint(idx * samples_per_chunk / rate_hz * 1e6).astype(np.int64)
    end_us = np.rint((idx + 1) * samples_per_chunk / rate_hz * 1e6).astype(np.int64)
    epoch = np.full(n_chunks, -1, dtype=np.int64)
    for k, boot in enumerate(plan.boots):
        inside = (start_us >= (boot.boot_at - start) // _US) & (epoch < 0)
        if boot.end_at is not None:
            inside &= start_us < (boot.end_at - start) // _US
        epoch[inside] = k
    return [start + timedelta(microseconds=int(e)) for e in end_us[epoch >= 0]]


class _Reorder:
    """Heap by (`at`, arrival order): releases in order everything before a mark."""

    def __init__(self) -> None:
        self._items: list[tuple[datetime, int, Envelope]] = []
        self._count = itertools.count()

    def push(self, envelope: Envelope) -> None:
        heapq.heappush(self._items, (envelope.at, next(self._count), envelope))

    def release(self, before: datetime | None) -> Iterator[Envelope]:
        """Everything with `at` < `before` (everything if None)."""
        while self._items and (before is None or self._items[0][0] < before):
            yield heapq.heappop(self._items)[2]


class _Cursor:
    """Lazy consumption of a stream's ChunkOutcomes in a span, up to a chunk-end instant."""

    def __init__(self, outcomes: Iterator[ChunkOutcome], ends: list[datetime]) -> None:
        self._outcomes = outcomes
        self._ends = ends
        self._next = 0

    def drain(self, until: datetime | None) -> Iterator[ChunkOutcome]:
        while self._next < len(self._ends) and (until is None or self._ends[self._next] <= until):
            self._next += 1
            yield next(self._outcomes)


@dataclass(slots=True)
class _Run:
    """A session in progress within the walk of a day."""

    plan: SessionPlan
    synthesis: SessionSynthesis
    gravity_environment: GravityEnvironment
    cursors: list[_Cursor] = field(default_factory=list)
    lost: dict[str, list[ChunkOutcome]] = field(default_factory=dict)


class MissionGenerator:
    def __init__(self, config: MissionConfig, seed: int | None = None) -> None:
        """`seed=None` uses `config.seed`."""
        self._config = config
        self._seed = config.seed if seed is None else seed
        self._tree = RngTree(self._seed)
        self._timeline = MissionTimeline(config)
        self._latents = LatentModel(config, self._timeline, self._seed)
        self._plans: dict[tuple[str, int], tuple[SessionPlan, ...]] = {}
        # Scheduled calibrations in force per (astronaut, day): deterministic, computed once.
        self._scheduled: dict[tuple[str, int], dict[str, CalibrationRecord]] = {}
        # Calibrations made live (gateway), per (astronaut, stream): they replace the scheduled ones.
        self._live_calibrations: dict[tuple[str, str], CalibrationRecord] = {}
        # All those made live per (astronaut, calibration_id), including already replaced ones (see `calibration`).
        self._live_by_id: dict[tuple[str, str], CalibrationRecord] = {}

    @property
    def config(self) -> MissionConfig:
        return self._config

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def timeline(self) -> MissionTimeline:
        return self._timeline

    @property
    def latents(self) -> LatentModel:
        return self._latents

    def iter_days(self) -> Iterator[DayInfo]:
        return iter(self._timeline)

    def iter_messages(self) -> Iterator[Envelope]:
        yield from self.profiles()
        for day in self._timeline:
            # No carried ones: each calibration already comes out once, on the day it is registered.
            yield from self.day_messages(day.index, include_carried_calibrations=False)

    def profiles(self) -> Iterator[Envelope]:
        at = self._timeline.day(0).start
        for member in self._config.crew:
            profile = reports.build_profile(member, updated_at=at)
            yield Envelope.of(EnvelopeKind.PROFILE, profile, at=at, astronaut_id=member.astronaut_id, day=None)

    def day_messages(self, day: int, astronaut_id: str | None = None, *, include_carried_calibrations: bool = True) -> Iterator[Envelope]:
        """Messages of a day (of one astronaut, or of all merged by `at`).

        `include_carried_calibrations`: first the calibrations from earlier days still in force that day
        (those cited by its sessions and chunks), identical to how the full mission emitted them on their day;
        their `at` is before the day, so they also go first when merging astronauts. With False, the
        exact slice of the day within `iter_messages`.
        """
        self._timeline.day(day)  # IndexError now, not on consumption
        carried = include_carried_calibrations
        if astronaut_id is not None:
            self._config.member(astronaut_id)
            return self._astronaut_day(astronaut_id, day, carried=carried)
        streams = [self._astronaut_day(m.astronaut_id, day, carried=carried) for m in self._config.crew]
        return heapq.merge(*streams, key=lambda e: e.at)

    def sessions(self, day: int, astronaut_id: str) -> tuple[SessionPlan, ...]:
        key = (astronaut_id, day)
        plans = self._plans.get(key)
        if plans is None:
            self._config.member(astronaut_id)
            plans = sess.plan_day(self._config, self._seed, astronaut_id, self._timeline.day(day))
            self._plans[key] = plans
        return plans

    def session_plan(self, ref: SessionRef) -> SessionPlan:
        """Plan of session `ref` (KeyError if there is no session in that slot that day)."""
        for plan in self.sessions(ref.day, ref.astronaut_id):
            if plan.ref == ref:
                return plan
        raise KeyError(f"no session {ref.slot!r} for {ref.astronaut_id} on day {ref.day}")

    def session_messages(
        self,
        ref: SessionRef,
        *,
        gravity_environment: GravityEnvironment | None = None,
        include_carried_calibrations: bool = True,
    ) -> Iterator[Envelope]:
        """Session, calibrations registered in it, chunks and ground truth of the session, as in the mission
        (with a different `gravity_environment` only what depends on gravity changes).

        `include_carried_calibrations`: first the calibrations in force in the session registered outside
        it (another day, another session or at power-on), identical to how the full mission emitted them. With
        False, the exact slice of the session within the day.
        """
        self.session_plan(ref)
        return self._astronaut_day(ref.astronaut_id, ref.day, target=ref.slot, gravity=gravity_environment, carried=include_carried_calibrations)

    def resimulate(self, ref: SessionRef, gravity_environment: GravityEnvironment) -> list[Envelope]:
        return sess.resimulate(self, ref, gravity_environment)

    def calibration_in_force(self, astronaut_id: str, stream_id: str, day: int) -> CalibrationRecord | None:
        """Calibration in force for a stream on mission day `day`: the latest one made live
        (`live_calibration`) or, if none, the mission's scheduled one for that day (stretch and imu; None if the
        stream has none). The single rule: used by the live blocks and the gateway."""
        self._config.member(astronaut_id)
        self._config.device.stream(stream_id)
        live = self._live_calibrations.get((astronaut_id, stream_id))
        if live is not None:
            return live
        return self._scheduled_calibrations(astronaut_id, day).get(stream_id)

    def calibration(self, astronaut_id: str, calibration_id: str, day: int) -> CalibrationRecord | None:
        """The calibration `calibration_id` that a chunk of day `day` may cite: one made live (even if
        a later one already replaced it) or a mission-scheduled one in force that day. None if it is neither."""
        self._config.member(astronaut_id)
        live = self._live_by_id.get((astronaut_id, calibration_id))
        if live is not None:
            return live
        return next((r for r in self._scheduled_calibrations(astronaut_id, day).values() if r.calibration_id == calibration_id), None)

    def _scheduled_calibrations(self, astronaut_id: str, day: int) -> dict[str, CalibrationRecord]:
        """`sessions.scheduled_calibrations` cached per (astronaut, day); the result is not modified."""
        key = (astronaut_id, day)
        scheduled = self._scheduled.get(key)
        if scheduled is None:
            scheduled = sess.scheduled_calibrations(self._config, self._seed, self._timeline, astronaut_id, self._timeline.day(day))
            self._scheduled[key] = scheduled
        return scheduled

    # --- Walk of an astronaut-day -----------------------------------------------------------------

    def _astronaut_day(
        self,
        astronaut_id: str,
        day_index: int,
        *,
        target: str | None = None,
        gravity: GravityEnvironment | None = None,
        carried: bool = False,
    ) -> Iterator[Envelope]:
        config, seed, tree = self._config, self._seed, self._tree
        member = config.member(astronaut_id)
        device_id = member.device_id
        day = self._timeline.day(day_index)
        state = self._latents.state(astronaut_id, day_index)
        device_plan = plan_device_day(config, seed, astronaut_id, day)
        device = SimDevice(config, device_plan)
        plans = self.sessions(day_index, astronaut_id)
        scheduled = self._scheduled_calibrations(astronaut_id, day_index)
        full = target is None
        out = _Reorder()

        if carried:
            # Carried calibrations, in front of everything: the same envelope as on their day (everything comes from the seed
            # and their registration day, with no RNG state from other days). Their `at` is not later than anything that follows.
            for record in self._carried_calibrations(scheduled, plans, day_index, target):
                yield calibration_envelope(astronaut_id, device_id, record, record.created_at)

        def envelope(kind: EnvelopeKind, model: Any, at: datetime, **kw: Any) -> Envelope:
            return Envelope.of(kind, model, at=at, astronaut_id=astronaut_id, day=day_index, **kw)

        def ground_truth(record: dict[str, Any], at: datetime) -> Envelope:
            return Envelope(EnvelopeKind.GROUND_TRUTH, None, record, at, False, astronaut_id, day_index)

        clock_rng = tree.rng(astronaut_id, day_index, "clock_sync")
        for at in _clock_times(config, device_plan):
            sync = device.clock_sync(clock_rng, at)
            if full and sync is not None:
                out.push(envelope(EnvelopeKind.CLOCK_SYNC, sync, at, topic=topics.clock(astronaut_id, device_id), retained=True))

        if full:
            activity = self._latents.activity(astronaut_id, day_index)
            context = reports.build_daily_context(tree.rng(astronaut_id, day_index, "daily_context"), config, member, day, activity=activity, state=state)
            out.push(envelope(EnvelopeKind.DAILY_CONTEXT, context, day.start))
            out.push(ground_truth(truth.latent_record(state, day, activity), day.start))
            out.push(ground_truth(truth.device_record(device_plan, day), day.start))
            report = reports.build_daily_self_report(
                tree.rng(astronaut_id, day_index, "self_report"), config, member, day, state=state,
                report_id=tree.uuid(astronaut_id, day_index, "self_report"),
            )  # fmt: skip
            out.push(envelope(EnvelopeKind.SELF_REPORT, report, report.timestamp))
            for record in scheduled.values():
                if record.day == day_index and record.session_id is None:
                    out.push(calibration_envelope(astronaut_id, device_id, record, record.created_at))

        # Events of the day in order: span start (0) < session end (1) < status instant (2).
        events: list[tuple[datetime, int, int, str, SessionPlan | None, int, int]] = []
        order = itertools.count()
        for plan in plans:
            for k0, k1 in sess.active_chunks(plan):
                events.append((plan.start + k0 * sess.chunk_delta(plan.chunk_ms), 0, next(order), "start", plan, k0, k1))
            events.append((plan.end, 1, next(order), "end", plan, 0, 0))
        for at in _status_times(config, device_plan):
            events.append((at, 2, next(order), "tick", None, 0, 0))
        events.sort(key=lambda e: e[:3])

        runs: dict[str, _Run] = {}
        last_seq: dict[tuple[int, str], int] = {}
        status_rng = tree.rng(astronaut_id, day_index, "status")

        def drain(run: _Run, until: datetime | None) -> None:
            for cursor in run.cursors:
                for outcome in cursor.drain(until):
                    key = (outcome.boot_id, outcome.stream_id)
                    last_seq[key] = max(last_seq.get(key, -1), outcome.seq)
                    if outcome.chunk is None:
                        run.lost[outcome.stream_id].append(outcome)
                    else:
                        topic = topics.stream(astronaut_id, device_id, outcome.stream_id)
                        out.push(envelope(EnvelopeKind.STREAM_CHUNK, outcome.chunk, outcome.received_at, topic=topic))

        for at, _, _, kind, plan, k0, k1 in events:
            if kind == "start":
                assert plan is not None
                if full or plan.ref.slot == target:
                    run = runs.get(plan.ref.slot)
                    if run is None:
                        g = gravity if gravity is not None else day.gravity_environment
                        in_force, recorded = sess.session_calibrations(config, seed, plan, scheduled)
                        session = sess.build_session(
                            config, plan, day=day, device_plan=device_plan, gravity_environment=g,
                            calibration_ids={s: r.calibration_id for s, r in in_force.items()},
                        )  # fmt: skip
                        out.push(envelope(EnvelopeKind.SESSION, session, plan.start))
                        for record in recorded:
                            out.push(calibration_envelope(astronaut_id, device_id, record, record.created_at))
                        spec = sess.mission_spec(config, plan, day=day, gravity_environment=g)
                        synthesis = sess.synthesize(config, seed, spec, member=member, state=state, calibrations=in_force)
                        run = _Run(plan, synthesis, g, lost={s: [] for s in plan.stream_ids})
                        runs[plan.ref.slot] = run
                    for stream in run.synthesis.streams:
                        channels, quality = stream.slice(k0, k1)
                        start = stream.chunk_start(k0)
                        outcomes = device.chunks(
                            tree.rng(*run.synthesis.spec.keys, stream.stream_id, "device", k0), stream_id=stream.stream_id,
                            session_id=plan.session_id, start=start, rate_hz=stream.rate_hz, channels=channels,
                            chunk_ms=plan.chunk_ms, quality=quality, calibration_id=stream.calibration_id,
                        )  # fmt: skip
                        ends = _existing_chunk_ends(device_plan, start, k1 - k0, stream.samples_per_chunk, stream.rate_hz)
                        run.cursors.append(_Cursor(outcomes, ends))
                else:
                    for stream_id in plan.stream_ids:
                        rate = sess.stream_rate(config, stream_id, background=plan.background)
                        spc = config.signals.samples_per_chunk(rate, chunk_ms=plan.chunk_ms)
                        device.advance(
                            stream_id, start=_chunk_start(plan.start, k0, spc, rate), n_samples=(k1 - k0) * spc, rate_hz=rate, chunk_ms=plan.chunk_ms
                        )
            elif kind == "end":
                assert plan is not None
                run = runs.get(plan.ref.slot)
                if run is not None:
                    drain(run, None)
                    for record in self._session_truth(run, day_index, state):
                        out.push(ground_truth(record, plan.end))
            else:
                for run in runs.values():
                    drain(run, at)
                if full:
                    out.push(self._status(device, device_plan, status_rng, at, last_seq, astronaut_id, day_index))
                yield from out.release(at)
        for run in runs.values():
            drain(run, None)
        yield from out.release(None)

    @staticmethod
    def _carried_calibrations(
        scheduled: dict[str, CalibrationRecord], plans: Sequence[SessionPlan], day_index: int, target: str | None
    ) -> list[CalibrationRecord]:
        """Calibrations in force that the output cites but does not register. Whole day: the scheduled ones from an earlier
        day (in `device.streams` order). Session: those in force on its streams registered outside
        it (another day, another session, at power-on), in the order of its streams. The emg_mvc ones are always registered
        in their own session, so they are never carried."""
        if target is None:
            return [record for record in scheduled.values() if record.day < day_index]
        plan = next(p for p in plans if p.ref.slot == target)
        return [scheduled[s] for s in plan.stream_ids if s in scheduled and scheduled[s].session_id != plan.session_id]

    def _session_truth(self, run: _Run, day_index: int, state: Any) -> list[dict[str, Any]]:
        plan = run.plan
        session = truth.TruthSession(plan.ref.astronaut_id, day_index, plan.session_id, plan.ref.slot, plan.protocol_id, run.gravity_environment)
        active = sess.active_chunks(plan) if plan.background else None
        features = truth.expected_features(self._config, run.synthesis, state=state, active=active)
        return [
            truth.quality_faults_record(session, run.synthesis.streams, active=active),
            truth.lost_chunks_record(session, run.lost),
            truth.expected_features_record(session, features),
        ]

    def _status(
        self,
        device: SimDevice,
        device_plan: DevicePlan,
        rng: np.random.Generator,
        at: datetime,
        last_seq: dict[tuple[int, str], int],
        astronaut_id: str,
        day_index: int,
    ) -> Envelope:
        """DeviceStatus with `last_seq` of what was sent up to `at` (SimDevice already counts whole spans)."""
        epoch = device_plan.epoch_at(at)
        published = {stream_id: seq for (boot_id, stream_id), seq in last_seq.items() if epoch is not None and boot_id == epoch.boot_id}
        payload = dump(device.status(rng, at, published=published))
        topic = topics.status(astronaut_id, device_plan.device_id)
        return Envelope(EnvelopeKind.DEVICE_STATUS, topic, payload, at, True, astronaut_id, day_index)

    # --- Live API (gateway) -------------------------------------------------------------------------

    def live_session(
        self,
        *,
        astronaut_id: str,
        day: int,
        device: SimDevice,
        session_id: str,
        session_type: Literal["exercise", "spine_check"],
        protocol_id: str,
        stream_ids: Sequence[str],
        start: datetime,
    ) -> Iterator[Envelope]:
        """Chunks (and ground truth) of a session requested by `start_session`, in chunk order.

        Uses the config's `protocol_id` protocol if it exists; once it finishes (or from the start if it does not
        exist) it continues at rest indefinitely: the iterator does not end, the gateway cuts it with
        `stop_session` or on reaching `max_duration_s`. Empty `stream_ids` = those of the protocol (or all of the
        device). `session_type` is the command's; the signal is decided by the protocol. RNG:
        `(astronaut_id, "live", session_id, ...)`. See `_live_chunks` for the chunking.
        """
        config = self._config
        member = config.member(astronaut_id)
        info = self._timeline.day(day)
        protocol = config.protocols.get(protocol_id)
        if stream_ids:
            streams = tuple(stream_ids)
        elif protocol is not None:
            streams = tuple(protocol.streams)
        else:
            streams = tuple(s.stream_id for s in config.device.streams)
        for stream_id in streams:
            config.device.stream(stream_id)
        return self._live_session_iter(
            astronaut_id=member.astronaut_id, info=info, device=device, session_id=session_id,
            protocol_id=protocol_id, streams=streams, start=start,
        )  # fmt: skip

    def _live_session_iter(
        self,
        *,
        astronaut_id: str,
        info: DayInfo,
        device: SimDevice,
        session_id: str,
        protocol_id: str,
        streams: tuple[str, ...],
        start: datetime,
    ) -> Iterator[Envelope]:
        config = self._config
        protocol = config.protocols.get(protocol_id)
        state = self._latents.state(astronaut_id, info.index)
        keys = (astronaut_id, "live", session_id)
        session = truth.TruthSession(astronaut_id, info.index, session_id, None, protocol_id, info.gravity_environment)
        residual: dict[str, float] = {}
        cursor = start
        chunk_ms = sess.chunk_ms_for(config, background=False)
        if protocol is not None:
            exercise = isinstance(protocol, ExerciseProtocol)
            reps = rep_schedule(protocol, rng=RngTree(self._seed).rng(*keys, "exercise")) if exercise else ()
            duration = exercise_duration_s(protocol, reps) if exercise else sess.protocol_duration_s(protocol)
            spec = self._live_spec(
                (*keys, "protocol"), cursor, sess.chunks_for(duration, chunk_ms=chunk_ms), chunk_ms, streams, info,
                background=False, reps=reps, protocol=protocol if exercise else None, residual=residual,
            )  # fmt: skip
            calibrations = self._in_force(astronaut_id, info.index, streams)  # per block: a `calibrate` applies from the next one
            synthesis = sess.synthesize(config, self._seed, spec, member=config.member(astronaut_id), state=state, calibrations=calibrations)
            features = truth.expected_features(config, synthesis, state=state)
            yield Envelope(EnvelopeKind.GROUND_TRUTH, None, truth.expected_features_record(session, features), start, False, astronaut_id, info.index)
            yield from self._live_chunks(device, synthesis, session, astronaut_id, info.index)
            residual = synthesis.final_residual_mm
            cursor = cursor + spec.n_chunks * sess.chunk_delta(chunk_ms)
        for block in itertools.count():
            spec = self._live_spec(
                (*keys, "rest", block),
                cursor,
                _live_block_chunks(config, chunk_ms),
                chunk_ms,
                streams,
                info,
                background=False,
                reps=(),
                protocol=None,
                residual=residual,
            )
            calibrations = self._in_force(astronaut_id, info.index, streams)
            synthesis = sess.synthesize(config, self._seed, spec, member=config.member(astronaut_id), state=state, calibrations=calibrations)
            yield from self._live_chunks(device, synthesis, session, astronaut_id, info.index)
            residual = synthesis.final_residual_mm
            cursor = cursor + spec.n_chunks * sess.chunk_delta(chunk_ms)

    def live_calibration(
        self,
        *,
        astronaut_id: str,
        day: int,
        session_id: str,
        calibration_type: Literal["stretch", "emg_mvc", "imu"],
        stream_ids: Sequence[str],
        at: datetime,
    ) -> list[Envelope]:
        """Calibrations (one per stream) of a `calibrate`; its calibration_id goes in the `completed` ack.

        They become the ones in force for those streams in the following `live_session`/`live_background`.
        ValueError if `stream_ids` is empty or some stream is not of the calibration's type (nothing is registered).
        """
        config = self._config
        member = config.member(astronaut_id)
        info = self._timeline.day(day)
        kinds = {"stretch": StreamKind.STRETCH, "emg_mvc": StreamKind.EMG, "imu": StreamKind.IMU}
        builders: dict[str, Callable[..., CalibrationRecord]] = {
            "stretch": sess.stretch_calibration, "emg_mvc": sess.emg_mvc_calibration, "imu": sess.imu_calibration,
        }  # fmt: skip
        if calibration_type not in kinds:
            raise ValueError(f"unknown calibration_type {calibration_type!r}")
        if not stream_ids:
            raise ValueError("calibrate needs at least one stream_id")
        for stream_id in stream_ids:
            kind = config.device.stream(stream_id).stream_kind
            if kind is not kinds[calibration_type]:
                raise ValueError(f"{calibration_type} calibration does not apply to {kind.value} stream {stream_id}")
        out: list[Envelope] = []
        for stream_id in stream_ids:
            keys = (astronaut_id, "live", session_id, "calibration", calibration_type, stream_id, at.isoformat())
            record = builders[calibration_type](
                config, self._tree.rng(*keys), member, calibration_id=self._tree.uuid(*keys), stream_id=stream_id,
                session_id=session_id, created_at=at, day=info.index,
            )  # fmt: skip
            self._live_calibrations[(astronaut_id, stream_id)] = record
            self._live_by_id[(astronaut_id, record.calibration_id)] = record
            out.append(calibration_envelope(astronaut_id, member.device_id, record, at))
        return out

    def live_background(
        self,
        *,
        astronaut_id: str,
        day: int,
        device: SimDevice,
        session_id: str,
        start: datetime,
    ) -> Iterator[Envelope]:
        """Background chunks (IMU + cord at the reduced rates) from `start`, endless.

        To pause, the gateway stops consuming; to resume it asks for another iterator with the new `start` and the
        same `session_id` (the seq continues in `device`). RNG `(astronaut_id, "live", session_id, start, ...)`.
        """
        config = self._config
        config.member(astronaut_id)
        info = self._timeline.day(day)
        return self._live_background_iter(astronaut_id, info, device, session_id, start)

    def _live_background_iter(self, astronaut_id: str, info: DayInfo, device: SimDevice, session_id: str, start: datetime) -> Iterator[Envelope]:
        config = self._config
        background = config.schedule.background
        streams = tuple(background.streams)
        state = self._latents.state(astronaut_id, info.index)
        session = truth.TruthSession(astronaut_id, info.index, session_id, None, background.protocol_id, info.gravity_environment)
        keys = (astronaut_id, "live", session_id, start.isoformat())
        residual: dict[str, float] = {}
        cursor = start
        chunk_ms = background.chunk_ms
        for block in itertools.count():
            spec = self._live_spec(
                (*keys, block),
                cursor,
                _live_block_chunks(config, chunk_ms),
                chunk_ms,
                streams,
                info,
                background=True,
                reps=(),
                protocol=None,
                residual=residual,
            )
            calibrations = self._in_force(astronaut_id, info.index, streams)  # per block: a `calibrate` applies from the next one
            synthesis = sess.synthesize(config, self._seed, spec, member=config.member(astronaut_id), state=state, calibrations=calibrations)
            yield from self._live_chunks(device, synthesis, session, astronaut_id, info.index)
            residual = synthesis.final_residual_mm
            cursor = cursor + spec.n_chunks * sess.chunk_delta(chunk_ms)

    def _in_force(self, astronaut_id: str, day: int, streams: Sequence[str]) -> dict[str, CalibrationRecord]:
        """`calibration_in_force` of each stream that has a calibration."""
        out: dict[str, CalibrationRecord] = {}
        for stream_id in streams:
            record = self.calibration_in_force(astronaut_id, stream_id, day)
            if record is not None:
                out[stream_id] = record
        return out

    def _live_spec(
        self,
        keys: tuple[Any, ...],
        start: datetime,
        n_chunks: int,
        chunk_ms: float,
        streams: tuple[str, ...],
        info: DayInfo,
        *,
        background: bool,
        reps: tuple[Any, ...],
        protocol: ExerciseProtocol | None,
        residual: dict[str, float],
    ) -> SynthSpec:
        device = self._config.device
        return SynthSpec(
            keys=keys, start=start, n_chunks=n_chunks, chunk_ms=chunk_ms, stream_ids=streams, background=background, reps=reps, protocol=protocol,
            gravity_environment=info.gravity_environment,
            awake_from=info.at(device.power_on_time_utc),
            awake_to=info.at(device.power_off_time_utc),  # of the mission day, not the block: past 00:00 UTC the awake fraction does not restart
            initial_residual_mm=dict(residual),
        )  # fmt: skip

    def _live_chunks(
        self, device: SimDevice, synthesis: SessionSynthesis, session: truth.TruthSession, astronaut_id: str, day_index: int
    ) -> Iterator[Envelope]:
        """Slices a live block by calling `SimDevice.chunks` chunk by chunk (lazily), so the device
        seq never runs ahead of what was consumed: if the gateway cuts the iterator, the next
        session continues the seq without gaps. Trade-off: each loss is a single chunk (no bursts or
        `stalled`). The `packet_gap` of the chunk after a loss is added here. Ground truth of the block
        (quality_faults / lost_chunks) at the end, only if there is something.
        """
        config = self._config
        loss = config.faults.device.packet_loss
        gap = QualityState(loss.packet_gap_score, frozenset({QualityFlag.PACKET_GAP}))
        device_id = config.member(astronaut_id).device_id
        spec = synthesis.spec
        previous_lost = device.live_previous_lost  # per stream and on the device: a loss at the end of a block marks the first chunk of the next
        lost: dict[str, list[ChunkOutcome]] = {s.stream_id: [] for s in synthesis.streams}
        for k in range(spec.n_chunks):
            batch: list[Envelope] = []
            for stream in synthesis.streams:
                channels, quality = stream.slice(k, k + 1)
                if previous_lost.get(stream.stream_id, False):
                    quality = [{key: state.merge(gap) for key, state in quality[0].items()}]
                outcomes = list(
                    device.chunks(
                        self._tree.rng(*spec.keys, stream.stream_id, "device", k), stream_id=stream.stream_id,
                        session_id=session.session_id, start=stream.chunk_start(k), rate_hz=stream.rate_hz, channels=channels,
                        chunk_ms=spec.chunk_ms, quality=quality, calibration_id=stream.calibration_id,
                    )
                )  # fmt: skip
                if not outcomes:
                    previous_lost[stream.stream_id] = False
                    continue
                outcome = outcomes[0]
                previous_lost[stream.stream_id] = outcome.chunk is None
                if outcome.chunk is None:
                    lost[stream.stream_id].append(outcome)
                else:
                    topic = topics.stream(astronaut_id, device_id, stream.stream_id)
                    batch.append(
                        Envelope.of(EnvelopeKind.STREAM_CHUNK, outcome.chunk, at=outcome.received_at, astronaut_id=astronaut_id, day=day_index, topic=topic)
                    )
            batch.sort(key=lambda e: e.at)
            yield from batch
        end = spec.start + spec.n_chunks * sess.chunk_delta(spec.chunk_ms)
        if any(s.episodes for s in synthesis.streams):
            record = truth.quality_faults_record(session, synthesis.streams)
            yield Envelope(EnvelopeKind.GROUND_TRUTH, None, record, end, False, astronaut_id, day_index)
        if any(lost.values()):
            yield Envelope(EnvelopeKind.GROUND_TRUTH, None, truth.lost_chunks_record(session, lost), end, False, astronaut_id, day_index)


def _live_block_chunks(config: MissionConfig, chunk_ms: float) -> int:
    """Chunks (of `chunk_ms`) per live synthesis block: one DeviceStatus interval (the background
    flexions do not cross blocks; the cord chains its residual between blocks)."""
    return max(1, round(config.device.status_interval_s * 1000.0 / chunk_ms))


def _clock_times(config: MissionConfig, plan: DevicePlan) -> list[datetime]:
    """ClockSync at boot and every `clock_sync_interval_s` while each boot lasts."""
    step = timedelta(seconds=config.device.clock_sync_interval_s)
    times: list[datetime] = []
    for boot in plan.boots:
        end = boot.end_at if boot.end_at is not None else plan.power_off
        at = boot.boot_at
        while end is None or at < end:
            times.append(at)
            at += step
            if end is None:  # pragma: no cover  (daily plans always have a power-off)
                break
    return times


def _status_times(config: MissionConfig, plan: DevicePlan) -> list[datetime]:
    """DeviceStatus at power-on, every `status_interval_s` and one at power-off (offline)."""
    assert plan.power_off is not None
    step = timedelta(seconds=config.device.status_interval_s)
    times: list[datetime] = []
    at = plan.power_on
    while at < plan.power_off:
        times.append(at)
        at += step
    times.append(plan.power_off)
    return times


__all__ = ["MissionGenerator", "SessionRef", "StreamSynthesis", "calibration_envelope"]
