"""Sessions: day schedule, Session record, calibrations and synthesis of a session's streams.

- `plan_day`: planned sessions of the day (those of `schedule.sessions` whose phase and `every_n_days`
  apply) with start jitter (RNG `(astronaut_id, day.index, slot, "plan")`, rounded to ms) and without
  overlapping (one that would start before the previous one ends is delayed until its end). If
  `schedule.background.enabled`, the background session `spine_check` (IMU + cord at the reduced
  frequencies) covers the window `start_time_utc..end_time_utc` with `pauses` where there are
  planned sessions. Ids: `derive_uuid(seed, astronaut_id, day.index, slot)`. Every session lasts a whole
  number of chunks, so all its streams share the chunk grid. The chunk of sessions
  is `signals.chunk_ms`; the background one is its own, `schedule.background.chunk_ms` (each `SessionPlan` and
  each `SynthSpec` carries its `chunk_ms`: there is no global chunk size).
  The spine_checks with `bracket` (pre/post) have no time or jitter: they are placed `gap_s` right before or
  after the already planned exercise `of`, with `phase` and `related_session_id`; if the exercise is not
  done that day, or the spine_check does not fit in the power-on window or clashes with another session, they are omitted.
- `active_chunks`: spans of chunks that are actually emitted (the background loses the chunks touching a
  pause: it resumes at the first chunk of its background grid that starts after the session ends).
- `synthesize`: publishable arrays of each stream (signal + quality faults + cord derivatives),
  plus the deterministic intermediates used by the ground truth. RNG per `spec.keys` (in mission
  `(astronaut_id, day.index, slot)`): `(..., stream_id, "signal")`, `(..., stream_id, "faults")` and
  `(..., "motion")` for the background flexions (shared by IMU and cord: it is the same trunk).
  Nothing is carried between sessions: the cord starts each session recovered (residual 0) except in the
  live blocks, which chain the residual of the previous block.
- Calibrations: stretch and imu are registered on days with index % `every_n_days` == 0 and the first day
  of each segment, in the first session of the day that uses that stream (or at device power-on if
  none uses it) and hold until the next one; emg_mvc at the start of each `exercise` session. The
  `calibration_id` in force goes in `Session.streams` and in the cord chunks (length_mm requires it).
  Each calibration is also emitted over MQTT (retained, `topics.calibration`): see `generator.calibration_envelope`.
- `build_session`: Session record (mission_context, streams with placement and calibration,
  load_declared, planned_reps (total), planned_sets and reps_per_set in `exercise`, events
  set_start/set_end (set_index), rep_start/rep_end (rep_index counted across the session, plus
  set_index) and rest_start/rest_end between sets, all with device_ms and boot_id; phase and
  related_session_id in the pre/post spine_checks; in the background pause/resume notes).
- `resimulate`: the same session with another gravity (exact A/B pair).

Modelling decisions (documented because the config does not fix them):
- The reference for the cord hysteresis is its static length at the start of the session (rest +
  latent elongation − diurnal loss, without flexion): only dynamic stretch leaves a residual and
  marks `band_recovering`; a column elongated at rest does not "recover" forever.
- No mvc_start/mvc_end events are emitted: the protocol has no MVC phase in the signal and events
  without the matching contraction in the EMG would mislead the engine. The MVC is the emg_mvc calibration.
- In spine_check and background the movement plane is the sagittal one (trunk flexion); at rest it does not matter.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from spine_contracts import (
    SCHEMA_VERSION,
    AnatomicalPlane,
    EmgMvcCalibration,
    GravityEnvironment,
    ImuCalibration,
    QualityFlag,
    Session,
    StreamKind,
    StretchCalibration,
)

from spine_physiology.config import (
    CrewMember,
    ExerciseProtocol,
    MissionConfig,
    SpineCheckProtocol,
)
from spine_physiology.device import DevicePlan
from spine_physiology.envelope import Envelope
from spine_physiology.faults import (
    ChunkQuality,
    FaultEpisode,
    apply_quality_faults,
    mask_quality,
    merge_quality,
    plan_quality_faults,
)
from spine_physiology.rng import Key, RngTree
from spine_physiology.signals import (
    Rep,
    exercise_duration_s,
    rep_schedule,
    sample_times,
    set_schedule,
)
from spine_physiology.signals import emg as emg_signal
from spine_physiology.signals import imu as imu_signal
from spine_physiology.signals import stretch as stretch_signal
from spine_physiology.state import GRAVITY_G, DayInfo, LatentState, MissionTimeline

if TYPE_CHECKING:
    from spine_physiology.generator import MissionGenerator

SOURCE = "simulator"
_US = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class SessionRef:
    """Identifies a mission session stably: `slot` is the schedule's (or the background's) `name`."""

    astronaut_id: str
    day: int
    slot: str


@dataclass(frozen=True, slots=True)
class SessionPlan:
    ref: SessionRef
    session_id: str
    session_type: Literal["exercise", "spine_check"]
    protocol_id: str
    start: datetime
    end: datetime
    stream_ids: tuple[str, ...]
    reps: tuple[Rep, ...]  # empty in spine_check
    background: bool
    chunk_ms: float  # chunk duration of this session: `signals.chunk_ms`, or `schedule.background.chunk_ms` in the background
    # Background only: intervals [start, end) occupied by planned sessions (no background chunks).
    pauses: tuple[tuple[datetime, datetime], ...]
    # Only in a spine_check that wraps an exercise (`schedule.sessions[].bracket`): pre/post and the exercise session.
    phase: Literal["pre", "post"] | None = None
    related_session_id: str | None = None


# --- Schedule ---------------------------------------------------------------------------------------


def chunk_ms_for(config: MissionConfig, *, background: bool) -> float:
    """Chunk duration of a session: the sessions' one, or the background's own."""
    return config.schedule.background.chunk_ms if background else config.signals.chunk_ms


def chunk_delta(chunk_ms: float) -> timedelta:
    return timedelta(milliseconds=chunk_ms)


def chunks_for(duration_s: float, *, chunk_ms: float) -> int:
    """Whole chunks covering `duration_s` (at least one), same as `signals.n_samples`."""
    return max(1, math.ceil(duration_s * 1000.0 / chunk_ms - 1e-9))


def protocol_duration_s(protocol: ExerciseProtocol | SpineCheckProtocol) -> float:
    return exercise_duration_s(protocol) if isinstance(protocol, ExerciseProtocol) else protocol.duration_s


def n_chunks(plan: SessionPlan) -> int:
    return (plan.end - plan.start) // chunk_delta(plan.chunk_ms)


def stream_rate(config: MissionConfig, stream_id: str, *, background: bool) -> float:
    """Rate of a stream: that of its type, or the reduced background one."""
    kind = config.device.stream(stream_id).stream_kind
    if not background:
        return config.signals.rate(kind)
    rates = config.schedule.background.sample_rates_hz
    if kind is StreamKind.IMU:
        return rates.imu
    if kind is StreamKind.STRETCH:
        return rates.stretch
    raise ValueError(f"background streams are IMU and cord only, got {kind.value} stream {stream_id}")


def plan_day(config: MissionConfig, seed: int, astronaut_id: str, day: DayInfo) -> tuple[SessionPlan, ...]:
    """Sessions of the day ordered by start (the background, if any, included)."""
    tree = RngTree(seed)
    chunk_ms = chunk_ms_for(config, background=False)
    chunk = chunk_delta(chunk_ms)
    candidates: list[tuple[datetime, int, Any, ExerciseProtocol | SpineCheckProtocol]] = []
    for order, slot in enumerate(config.schedule.sessions):
        if slot.bracket is not None or slot.time_utc is None or slot.start_jitter_s is None:
            continue  # the pre/post spine_checks are placed after the exercise they wrap
        if day.phase not in slot.phases or day.index % slot.every_n_days:
            continue
        rng = tree.rng(astronaut_id, day.index, slot.name, "plan")
        jitter_ms = round(float(rng.normal(0.0, slot.start_jitter_s)) * 1000.0)
        candidates.append((day.at(slot.time_utc) + timedelta(milliseconds=jitter_ms), order, slot, config.protocol(slot.protocol_id)))
    candidates.sort(key=lambda c: (c[0], c[1]))

    plans: list[SessionPlan] = []
    previous_end: datetime | None = None
    power_on, power_off = day.at(config.device.power_on_time_utc), day.at(config.device.power_off_time_utc)
    for start, _, slot, protocol in candidates:
        # The device only exists within its power-on window of the day: the session (with its duration
        # in whole chunks) is shifted to fit between power-on, the previous session and power-off.
        earliest = power_on if previous_end is None else max(power_on, previous_end)
        reps = rep_schedule(protocol, rng=tree.rng(astronaut_id, day.index, slot.name, "exercise")) if isinstance(protocol, ExerciseProtocol) else ()
        duration_s = exercise_duration_s(protocol, reps) if isinstance(protocol, ExerciseProtocol) else protocol_duration_s(protocol)
        duration = chunks_for(duration_s, chunk_ms=chunk_ms) * chunk
        start = max(start, earliest)
        if start + duration > power_off:
            start = power_off - duration
        if start < earliest:
            continue  # does not fit in the power-on window: the session is not performed that day
        end = start + duration
        plans.append(
            SessionPlan(
                ref=SessionRef(astronaut_id, day.index, slot.name),
                session_id=tree.uuid(astronaut_id, day.index, slot.name),
                session_type=protocol.session_type,
                protocol_id=slot.protocol_id,
                start=start,
                end=end,
                stream_ids=tuple(protocol.streams),
                reps=reps,
                background=False,
                chunk_ms=chunk_ms,
                pauses=(),
            )
        )
        previous_end = end

    for slot in config.schedule.sessions:
        bracket = slot.bracket
        if bracket is None or day.phase not in slot.phases or day.index % slot.every_n_days:
            continue
        anchor = next((p for p in plans if p.ref.slot == bracket.of), None)
        if anchor is None:
            continue  # the exercise is not done that day: neither is its pre/post spine_check
        protocol = config.protocol(slot.protocol_id)
        duration = chunks_for(protocol_duration_s(protocol), chunk_ms=chunk_ms) * chunk
        gap = timedelta(seconds=bracket.gap_s)
        start = anchor.start - gap - duration if bracket.phase == "pre" else anchor.end + gap
        end = start + duration
        if start < power_on or end > power_off or any(p.start < end and p.end > start for p in plans):
            continue  # does not fit in the power-on window or between the other sessions
        plans.append(
            SessionPlan(
                ref=SessionRef(astronaut_id, day.index, slot.name),
                session_id=tree.uuid(astronaut_id, day.index, slot.name),
                session_type=protocol.session_type,
                protocol_id=slot.protocol_id,
                start=start,
                end=end,
                stream_ids=tuple(protocol.streams),
                reps=(),
                background=False,
                chunk_ms=chunk_ms,
                pauses=(),
                phase=bracket.phase,
                related_session_id=anchor.session_id,
            )
        )

    background = config.schedule.background
    if background.enabled:
        start = day.at(background.start_time_utc)
        window_s = (day.at(background.end_time_utc) - start).total_seconds()
        end = start + chunks_for(window_s, chunk_ms=background.chunk_ms) * chunk_delta(background.chunk_ms)
        pauses = tuple(sorted((max(p.start, start), min(p.end, end)) for p in plans if p.start < end and p.end > start))
        plans.append(
            SessionPlan(
                ref=SessionRef(astronaut_id, day.index, background.name),
                session_id=tree.uuid(astronaut_id, day.index, background.name),
                session_type="spine_check",
                protocol_id=background.protocol_id,
                start=start,
                end=end,
                stream_ids=tuple(background.streams),
                reps=(),
                background=True,
                chunk_ms=background.chunk_ms,
                pauses=pauses,
            )
        )
    plans.sort(key=lambda p: (p.start, p.background))
    return tuple(plans)


def active_chunks(plan: SessionPlan) -> tuple[tuple[int, int], ...]:
    """Spans [k0, k1) of emitted chunks, on the grid of `plan.chunk_ms`. Background: without the chunks that
    end after the start of a pause and start before its end."""
    total = n_chunks(plan)
    if not plan.pauses:
        return ((0, total),)
    chunk_us = round(plan.chunk_ms * 1000)
    blocked: list[tuple[int, int]] = []
    for pause_start, pause_end in plan.pauses:
        a = max(0, ((pause_start - plan.start) // _US) // chunk_us)
        b = min(total, -(-((pause_end - plan.start) // _US) // chunk_us))
        if b > a:
            blocked.append((a, b))
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for a, b in sorted(blocked):
        if a > cursor:
            ranges.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < total:
        ranges.append((cursor, total))
    return tuple(ranges)


# --- Calibrations -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalibrationRecord:
    """An emitted (or in-force) calibration and what is needed to apply it."""

    calibration_id: str
    stream_id: str
    calibration_type: Literal["stretch", "emg_mvc", "imu"]
    created_at: datetime
    session_id: str | None  # session in which it was registered (None: at power-on, outside a session)
    day: int  # day index on which it was registered
    model: StretchCalibration | EmgMvcCalibration | ImuCalibration
    curve: tuple[tuple[float, float], ...] | None  # stretch only: (resistance_ohm, length_mm)


def calibration_day(timeline: MissionTimeline, every_n_days: int, day: int) -> int:
    """Last day <= `day` with a scheduled calibration (index % every_n_days == 0 or first day of a segment)."""
    for k in range(day, -1, -1):
        info = timeline.day(k)
        if k % every_n_days == 0 or info.day_in_segment == 0:
            return k
    raise ValueError("day 0 always calibrates")  # pragma: no cover


def _base(config: MissionConfig, member: CrewMember, *, calibration_id: str, stream_id: str, session_id: str | None, created_at: datetime) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "calibration_id": calibration_id,
        "astronaut_id": member.astronaut_id,
        "device_id": member.device_id,
        "stream_id": stream_id,
        "created_at": created_at,
        "source": SOURCE,
    }
    if session_id is not None:
        base["session_id"] = session_id
    return base


def member_lot_gain(config: MissionConfig, seed: int, astronaut_id: str) -> float:
    """Resistance gain of the astronaut's cord lot. RNG `(astronaut_id, "stretch_lot")`: its own branch,
    once per astronaut, shared by the calibration curve and the signal."""
    return stretch_signal.lot_gain(RngTree(seed).rng(astronaut_id, "stretch_lot"), config.signals.stretch)


def stretch_calibration(
    config: MissionConfig,
    rng: np.random.Generator,
    member: CrewMember,
    *,
    calibration_id: str,
    stream_id: str,
    session_id: str | None,
    created_at: datetime,
    day: int,
    lot_gain: float = 1.0,
) -> CalibrationRecord:
    """Measured (noisy) curve of the true cord curve (with the lot gain); the first point is the rest."""
    rest = member.baseline.cord_rest_length_mm
    curve = stretch_signal.measured_curve(rng, config.signals.stretch, config.calibration.stretch, rest_length_mm=rest, lot_gain=lot_gain)
    cfg = config.calibration.stretch
    model = StretchCalibration.model_validate({
        **_base(config, member, calibration_id=calibration_id, stream_id=stream_id, session_id=session_id, created_at=created_at),
        "calibration_type": "stretch", "rest_resistance_ohm": curve[0][0], "rest_length_mm": rest,
        "recovery_wait_s": cfg.recovery_wait_s, "temperature_c": cfg.temperature_c,
        "curve": {"model": "piecewise_linear", "points": [{"resistance_ohm": r, "length_mm": length} for r, length in curve]},
    })  # fmt: skip
    return CalibrationRecord(calibration_id, stream_id, "stretch", created_at, session_id, day, model, curve)


def imu_calibration(
    config: MissionConfig,
    rng: np.random.Generator,
    member: CrewMember,
    *,
    calibration_id: str,
    stream_id: str,
    session_id: str | None,
    created_at: datetime,
    day: int,
) -> CalibrationRecord:
    """Estimated bias = true bias of `signals.imu` + measurement error; ranges and mounting from the config."""
    imu = config.signals.imu
    cfg = config.calibration.imu
    gyro = rng.normal(0.0, cfg.gyro_bias_noise_dps, 3)
    accel = rng.normal(0.0, cfg.accel_bias_noise_g, 3)
    gb, ab = imu.gyro_bias_dps, imu.accel_bias_g
    level = config.device.stream(stream_id).placement.spine_level
    model = ImuCalibration.model_validate({
        **_base(config, member, calibration_id=calibration_id, stream_id=stream_id, session_id=session_id, created_at=created_at),
        "calibration_type": "imu", "spine_level": level.value if level is not None else None,
        "accel_range_g": imu.accel_range_g, "gyro_range_dps": imu.gyro_range_dps,
        "gyro_bias_dps": {"x": gb.x + float(gyro[0]), "y": gb.y + float(gyro[1]), "z": gb.z + float(gyro[2])},
        "accel_bias_g": {"x": ab.x + float(accel[0]), "y": ab.y + float(accel[1]), "z": ab.z + float(accel[2])},
        "mounting": {"sensor_x": imu.mounting.sensor_x.value, "sensor_y": imu.mounting.sensor_y.value, "sensor_z": imu.mounting.sensor_z.value},
        "static_duration_s": cfg.static_duration_s,
    })  # fmt: skip
    return CalibrationRecord(calibration_id, stream_id, "imu", created_at, session_id, day, model, None)


def emg_mvc_calibration(
    config: MissionConfig,
    rng: np.random.Generator,
    member: CrewMember,
    *,
    calibration_id: str,
    stream_id: str,
    session_id: str | None,
    created_at: datetime,
    day: int,
) -> CalibrationRecord:
    """Measured MVC = the true one × (1 + N(0, measurement_noise_rel)); rest = RMS of the rest tone and the noise."""
    cfg = config.calibration.emg_mvc
    emg = config.signals.emg
    true_mvc = member.baseline.emg_mvc_rms_mv[stream_id]
    measured = abs(true_mvc * (1.0 + float(rng.normal(0.0, cfg.measurement_noise_rel))))
    placement = config.device.stream(stream_id).placement
    model = EmgMvcCalibration.model_validate({
        **_base(config, member, calibration_id=calibration_id, stream_id=stream_id, session_id=session_id, created_at=created_at),
        "calibration_type": "emg_mvc",
        "muscle": placement.muscle.value if placement.muscle is not None else None,
        "side": placement.side.value if placement.side is not None else None,
        "mvc_rms_mv": measured, "window_ms": cfg.window_ms, "n_trials": cfg.n_trials, "signal_kind": "raw",
        "resting_baseline_mv": math.hypot(true_mvc * emg.rest_pct_mvc / 100.0, emg.noise_rms_mv),
    })  # fmt: skip
    return CalibrationRecord(calibration_id, stream_id, "emg_mvc", created_at, session_id, day, model, None)


_SCHEDULED = {StreamKind.STRETCH: ("stretch", stretch_calibration), StreamKind.IMU: ("imu", imu_calibration)}


def scheduled_calibrations(
    config: MissionConfig,
    seed: int,
    timeline: MissionTimeline,
    astronaut_id: str,
    day: DayInfo,
) -> dict[str, CalibrationRecord]:
    """stretch/imu calibrations in force on day `day`, per stream (registered on their calibration day).

    RNG `(astronaut_id, calibration_day, "calibration", type, stream_id)`; id with the same keys.
    """
    tree = RngTree(seed)
    member = config.member(astronaut_id)
    out: dict[str, CalibrationRecord] = {}
    for stream in config.device.streams:
        if stream.stream_kind not in _SCHEDULED:
            continue
        cal_type, build = _SCHEDULED[stream.stream_kind]
        every = config.calibration.stretch.every_n_days if cal_type == "stretch" else config.calibration.imu.every_n_days
        cal_index = calibration_day(timeline, every, day.index)
        cal_day = timeline.day(cal_index)
        host = next((p for p in plan_day(config, seed, astronaut_id, cal_day) if stream.stream_id in p.stream_ids), None)
        keys: tuple[Key, ...] = (astronaut_id, cal_index, "calibration", cal_type, stream.stream_id)
        extra = {"lot_gain": member_lot_gain(config, seed, astronaut_id)} if cal_type == "stretch" else {}
        out[stream.stream_id] = build(
            config, tree.rng(*keys), member, calibration_id=tree.uuid(*keys), stream_id=stream.stream_id,
            session_id=host.session_id if host is not None else None,
            created_at=host.start if host is not None else cal_day.at(config.device.power_on_time_utc), day=cal_index, **extra,
        )  # fmt: skip
    return out


def session_mvc_calibrations(config: MissionConfig, seed: int, plan: SessionPlan) -> dict[str, CalibrationRecord]:
    """emg_mvc of each EMG stream of an `exercise` session, at the start. RNG `(astronaut_id, day, slot, stream_id, "emg_mvc")`."""
    if plan.session_type != "exercise":
        return {}
    tree = RngTree(seed)
    member = config.member(plan.ref.astronaut_id)
    out: dict[str, CalibrationRecord] = {}
    for stream_id in plan.stream_ids:
        if config.device.stream(stream_id).stream_kind is not StreamKind.EMG:
            continue
        keys: tuple[Key, ...] = (plan.ref.astronaut_id, plan.ref.day, plan.ref.slot, stream_id, "emg_mvc")
        out[stream_id] = emg_mvc_calibration(
            config, tree.rng(*keys), member, calibration_id=tree.uuid(*keys), stream_id=stream_id,
            session_id=plan.session_id, created_at=plan.start, day=plan.ref.day,
        )  # fmt: skip
    return out


# --- Session record ---------------------------------------------------------------------------------


def _event(event_type: str, at: datetime, device_plan: DevicePlan, **extra: Any) -> dict[str, Any]:
    event: dict[str, Any] = {"event_type": event_type, "timestamp": at}
    epoch = device_plan.epoch_at(at)
    if epoch is not None:
        event["device_ms"] = epoch.device_ms(at)
        event["boot_id"] = epoch.boot_id
    event.update(extra)
    return event


def _exercise_events(plan: SessionPlan, protocol: ExerciseProtocol, device_plan: DevicePlan) -> list[dict[str, Any]]:
    """set_start/set_end per set, rep_start/rep_end of each rep (the session's rep_index, its set's set_index)
    and rest_start/rest_end between sets; ordered by instant (at equal instant: set_start, rep_start, rep_end, set_end)."""
    at = lambda seconds: plan.start + timedelta(seconds=seconds)  # noqa: E731
    sets = set_schedule(protocol, plan.reps)
    timed: list[tuple[float, int, dict[str, Any]]] = []
    for window in sets:
        timed.append((window.start_s, 0, _event("set_start", at(window.start_s), device_plan, set_index=window.index)))
        timed.append((window.end_s, 3, _event("set_end", at(window.end_s), device_plan, set_index=window.index)))
    for previous, following in zip(sets, sets[1:], strict=False):
        timed.append((previous.end_s, 4, _event("rest_start", at(previous.end_s), device_plan, set_index=previous.index)))
        timed.append((following.start_s, -1, _event("rest_end", at(following.start_s), device_plan, set_index=previous.index)))
    for rep in plan.reps:
        timed.append((rep.start_s, 1, _event("rep_start", at(rep.start_s), device_plan, set_index=rep.set_index, rep_index=rep.index)))
        timed.append((rep.end_s, 2, _event("rep_end", at(rep.end_s), device_plan, set_index=rep.set_index, rep_index=rep.index)))
    timed.sort(key=lambda t: (t[0], t[1]))
    return [event for _, _, event in timed]


def build_session(
    config: MissionConfig,
    plan: SessionPlan,
    *,
    day: DayInfo,
    device_plan: DevicePlan,
    gravity_environment: GravityEnvironment,
    calibration_ids: Mapping[str, str],
) -> Session:
    """Complete Session record (with `ended_at`): emitted when the session starts."""
    member = config.member(plan.ref.astronaut_id)
    streams = []
    for stream_id in plan.stream_ids:
        cfg = config.device.stream(stream_id)
        declaration: dict[str, Any] = {
            "stream_id": stream_id,
            "stream_kind": cfg.stream_kind.value,
            "placement": cfg.placement.model_dump(mode="json", exclude_none=True),
            "sample_rate_hz": stream_rate(config, stream_id, background=plan.background),
        }
        if stream_id in calibration_ids:
            declaration["calibration_id"] = calibration_ids[stream_id]
        streams.append(declaration)

    events: list[dict[str, Any]] = []
    if plan.session_type == "exercise" and plan.reps:
        protocol = config.protocol(plan.protocol_id)
        assert isinstance(protocol, ExerciseProtocol)
        events = _exercise_events(plan, protocol, device_plan)
    if plan.background:
        chunk = chunk_delta(plan.chunk_ms)
        ranges = active_chunks(plan)
        total = n_chunks(plan)
        for k0, k1 in ranges:
            if k0 > 0:
                events.append(_event("note", plan.start + k0 * chunk, device_plan, note="background resumed"))
            if k1 < total:
                events.append(_event("note", plan.start + k1 * chunk, device_plan, note="background paused"))

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": plan.session_id,
        "astronaut_id": plan.ref.astronaut_id,
        "device_id": member.device_id,
        "session_type": plan.session_type,
        "protocol_id": plan.protocol_id,
        "source": SOURCE,
        "started_at": plan.start,
        "ended_at": plan.end,
        "end_reason": "completed",
        "mission_context": {"mission_day": day.mission_day, "phase": day.phase.value, "gravity_environment": gravity_environment.value},
        "streams": streams,
        "events": events,
    }
    if plan.session_type == "exercise":
        protocol = config.protocol(plan.protocol_id)
        assert isinstance(protocol, ExerciseProtocol)
        payload["load_declared"] = protocol.load.model_dump(mode="json", exclude_none=True)
        payload["planned_reps"] = protocol.planned_reps
        payload["planned_sets"] = protocol.planned_sets
        payload["reps_per_set"] = protocol.reps_per_set
    if plan.phase is not None and plan.related_session_id is not None:
        payload["phase"] = plan.phase
        payload["related_session_id"] = plan.related_session_id
    return Session.model_validate(payload)


# --- Synthesis --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SynthSpec:
    """What is needed to synthesize the streams of a session (or of a live block)."""

    keys: tuple[Key, ...]  # prefix of the RNG branches
    start: datetime  # real instant of sample 0 of all streams
    n_chunks: int
    chunk_ms: float  # duration of each chunk of the span (sessions: `signals.chunk_ms`; background: its own)
    stream_ids: tuple[str, ...]
    background: bool  # reduced frequencies and spontaneous flexions
    reps: tuple[Rep, ...]
    protocol: ExerciseProtocol | None  # %MVC, ROM and plane of the reps (None: rest)
    gravity_environment: GravityEnvironment
    awake_from: datetime  # power-on -> power-off window for the cord's diurnal loss
    awake_to: datetime
    initial_residual_mm: Mapping[str, float] = field(default_factory=dict)  # per cord stream (live)


@dataclass(frozen=True, slots=True)
class StreamSynthesis:
    """Publishable arrays of a stream and its deterministic (noise-free) intermediates for the ground truth."""

    stream_id: str
    kind: StreamKind
    rate_hz: float
    samples_per_chunk: int
    start: datetime
    channels: dict[str, np.ndarray]  # keys expected by `SimDevice.chunks`
    quality: list[ChunkQuality]  # one per chunk
    calibration_id: str | None
    episodes: tuple[FaultEpisode, ...]
    truth: dict[str, Any]  # emg: pct_mvc, fatigue, mvc_rms_mv | imu: angle_deg | stretch: length_mm, residual_mm, rest_length_mm, angle_deg, lot_gain

    def slice(self, k0: int, k1: int) -> tuple[dict[str, np.ndarray], list[ChunkQuality]]:
        """Channels and quality of chunks [k0, k1)."""
        a, b = k0 * self.samples_per_chunk, k1 * self.samples_per_chunk
        return {name: arr[a:b] for name, arr in self.channels.items()}, self.quality[k0:k1]

    def chunk_start(self, k: int) -> datetime:
        return self.start + timedelta(microseconds=round(k * self.samples_per_chunk / self.rate_hz * 1e6))


@dataclass(frozen=True, slots=True)
class SessionSynthesis:
    spec: SynthSpec
    streams: tuple[StreamSynthesis, ...]

    def stream(self, stream_id: str) -> StreamSynthesis:
        for s in self.streams:
            if s.stream_id == stream_id:
                return s
        raise KeyError(stream_id)

    @property
    def final_residual_mm(self) -> dict[str, float]:
        """Hysteresis residual at the end of each cord stream (continuity between live blocks)."""
        return {s.stream_id: float(s.truth["residual_mm"][-1]) for s in self.streams if s.kind is StreamKind.STRETCH and s.truth["residual_mm"].size}


def _angle(config: MissionConfig, tree: RngTree, spec: SynthSpec, state: LatentState, n: int, rate: float) -> np.ndarray:
    if spec.background:
        bg = config.schedule.background
        return imu_signal.background_angle_profile(
            tree.rng(*spec.keys, "motion"), n_samples=n, rate_hz=rate,
            bends_per_hour=bg.bends_per_hour, bend_rom_deg=bg.bend_rom_deg, bend_duration_s=bg.bend_duration_s,
        )  # fmt: skip
    if spec.protocol is not None and spec.reps:
        rom = imu_signal.effective_rom_deg(spec.protocol.rom_deg, state, config.signals.imu)
        return imu_signal.angle_profile(spec.reps, rom_deg=[rom * r.amplitude_factor for r in spec.reps], n_samples=n, rate_hz=rate)
    return np.zeros(n, dtype=np.float64)


def synthesize(
    config: MissionConfig,
    seed: int,
    spec: SynthSpec,
    *,
    member: CrewMember,
    state: LatentState,
    calibrations: Mapping[str, CalibrationRecord],
) -> SessionSynthesis:
    """Signal, quality faults and derivatives of each stream of `spec` (not chunked)."""
    tree = RngTree(seed)
    signals = config.signals
    gravity_g = GRAVITY_G[spec.gravity_environment]
    plane = spec.protocol.plane if spec.protocol is not None else AnatomicalPlane.SAGITTAL
    reps = spec.reps if spec.protocol is not None else ()
    streams: list[StreamSynthesis] = []
    for stream_id in spec.stream_ids:
        kind = config.device.stream(stream_id).stream_kind
        rate = stream_rate(config, stream_id, background=spec.background)
        spc = signals.samples_per_chunk(rate, chunk_ms=spec.chunk_ms)
        n = spec.n_chunks * spc
        rng = tree.rng(*spec.keys, stream_id, "signal")
        truth: dict[str, Any] = {}
        calibration = calibrations.get(stream_id)
        if kind is StreamKind.EMG:
            params = signals.emg
            mvc = member.baseline.emg_mvc_rms_mv[stream_id]
            peak = (
                emg_signal.effective_pct_mvc(spec.protocol.pct_mvc, state.muscle_deconditioning, params) if spec.protocol is not None else params.rest_pct_mvc
            )
            peaks = [peak * (1.0 + 0.4 * r.fatigue) for r in reps]
            pct = emg_signal.activation_profile(reps, peak_pct_mvc=peaks, rest_pct_mvc=params.rest_pct_mvc, n_samples=n, rate_hz=rate)
            fatigue = emg_signal.exercise_fatigue_profile(
                reps, n_samples=n, rate_hz=rate, recovery_per_s=spec.protocol.recovery_per_s if spec.protocol else 0.0,
                fatigue_per_rep=emg_signal.effective_fatigue_per_rep(state.muscle_deconditioning, params),
            )
            raw = {"emg": emg_signal.synth_emg(rng, params, mvc_rms_mv=mvc, pct_mvc=pct, fatigue=fatigue, rate_hz=rate)}
            truth = {"pct_mvc": pct, "fatigue": fatigue, "mvc_rms_mv": mvc}
        elif kind is StreamKind.IMU:
            angle = _angle(config, tree, spec, state, n, rate)
            tremor = signals.imu.tremor_dps_per_unit * state.rom_motor_control
            raw = imu_signal.synth_imu(rng, signals.imu, angle_deg=angle, plane=plane, gravity_g=gravity_g, rate_hz=rate, tremor_dps=tremor)
            truth = {"angle_deg": angle}
        else:
            params = signals.stretch
            angle = _angle(config, tree, spec, state, n, rate)
            span_s = (spec.awake_to - spec.awake_from).total_seconds()
            awake = ((spec.start - spec.awake_from).total_seconds() + sample_times(n, rate)) / span_s
            offset = stretch_signal.diurnal_loss_mm(params, gravity_g=gravity_g, awake_fraction=awake)
            rest = member.baseline.cord_rest_length_mm
            length = stretch_signal.cord_length(params, rest_length_mm=rest, elongation_mm=state.spine_elongation_mm, angle_deg=angle, offset_mm=offset)
            static = float(length[0] - params.flexion_mm_per_deg * abs(angle[0])) if n else rest
            lot = member_lot_gain(config, seed, member.astronaut_id)
            cord = stretch_signal.synth_stretch(
                rng, params, length_mm=length, rest_length_mm=static, rate_hz=rate,
                initial_residual_mm=spec.initial_residual_mm.get(stream_id, 0.0),
                lot_gain=lot,
            )  # fmt: skip
            raw = {"adc_counts": cord.adc_counts}
            truth = {"length_mm": length, "residual_mm": cord.residual_mm, "rest_length_mm": rest, "angle_deg": angle, "lot_gain": lot}

        faults_rng = tree.rng(*spec.keys, stream_id, "faults")
        episodes = plan_quality_faults(faults_rng, config.faults.quality, stream_id=stream_id, kind=kind, n_samples=n, rate_hz=rate, samples_per_chunk=spc)
        channels, quality = apply_quality_faults(
            faults_rng, episodes, kind=kind, channels=raw, rate_hz=rate, samples_per_chunk=spc, signals=signals, faults=config.faults.quality
        )
        calibration_id: str | None = None
        if kind is StreamKind.STRETCH:
            params = signals.stretch
            if calibration is not None and calibration.curve is not None:
                resistance, length_mm = stretch_signal.derive(channels["adc_counts"], params, calibration.curve)
                channels["length_mm"] = length_mm
                calibration_id = calibration.calibration_id
            else:
                resistance = stretch_signal.adc_to_resistance(channels["adc_counts"], params)
            channels["resistance_ohm"] = resistance
            recovering = mask_quality(
                truth["residual_mm"] >= params.band_recovering_threshold_mm, kind=kind, key="length_mm",
                flag=QualityFlag.BAND_RECOVERING, score=params.band_recovering_score, samples_per_chunk=spc,
            )  # fmt: skip
            quality = merge_quality(quality, recovering)
        streams.append(StreamSynthesis(stream_id, kind, rate, spc, spec.start, channels, quality, calibration_id, episodes, truth))
    return SessionSynthesis(spec, tuple(streams))


def mission_spec(
    config: MissionConfig,
    plan: SessionPlan,
    *,
    day: DayInfo,
    gravity_environment: GravityEnvironment,
) -> SynthSpec:
    """SynthSpec of a mission session (RNG `(astronaut_id, day.index, slot, ...)`)."""
    protocol = None
    if plan.session_type == "exercise" and not plan.background:
        candidate = config.protocol(plan.protocol_id)
        protocol = candidate if isinstance(candidate, ExerciseProtocol) else None
    return SynthSpec(
        keys=(plan.ref.astronaut_id, plan.ref.day, plan.ref.slot),
        start=plan.start,
        n_chunks=n_chunks(plan),
        chunk_ms=plan.chunk_ms,
        stream_ids=plan.stream_ids,
        background=plan.background,
        reps=plan.reps,
        protocol=protocol,
        gravity_environment=gravity_environment,
        awake_from=day.at(config.device.power_on_time_utc),
        awake_to=day.at(config.device.power_off_time_utc),
    )


def session_calibrations(
    config: MissionConfig,
    seed: int,
    plan: SessionPlan,
    scheduled: Mapping[str, CalibrationRecord],
) -> tuple[dict[str, CalibrationRecord], list[CalibrationRecord]]:
    """(calibration in force per session stream, calibrations registered in this session)."""
    in_force: dict[str, CalibrationRecord] = {s: scheduled[s] for s in plan.stream_ids if s in scheduled}
    mvc = session_mvc_calibrations(config, seed, plan)
    in_force.update(mvc)
    recorded = [r for r in scheduled.values() if r.session_id == plan.session_id] + list(mvc.values())
    return in_force, recorded


def resimulate(generator: MissionGenerator, ref: SessionRef, gravity_environment: GravityEnvironment) -> list[Envelope]:
    """The session `ref` regenerated with another gravity (exact A/B pair with `generator.session_messages(ref)`)."""
    return list(generator.session_messages(ref, gravity_environment=gravity_environment))


__all__ = [
    "CalibrationRecord",
    "SessionPlan",
    "SessionRef",
    "SessionSynthesis",
    "StreamSynthesis",
    "SynthSpec",
    "active_chunks",
    "build_session",
    "plan_day",
    "resimulate",
    "scheduled_calibrations",
    "session_calibrations",
    "synthesize",
]
