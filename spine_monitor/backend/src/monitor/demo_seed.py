"""Demo seeder: `python -m monitor.demo_seed` fills the app database with two astronauts and about 60 days of history.

AST-01 (green and stable, amber over the last days) and AST-02 (red at the end, with an urgent red-flag report that reached Houston)
get a complete profile, a daily context and a daily self-report every day, a weekly Oswestry, one to three processed sessions a day
and what the real engine made of them. The baselines and the assessments are the engine's own answers (POST /baseline and POST
/assess over HTTP, one assessment per day with `as_of` of that day); only what the engine draws at random is rewritten, so two runs
give the same bytes: the ids (uuid5 of astronaut, kind and instant) and the `created_at` of each baseline.

The levels are never forced. The synthetic trajectories are written in z units against the baseline the engine returned (the value
of a feature is the baseline median plus `z` times the scale the engine's own z-score divides by), and the arcs below were tuned until
the real engine, with its shipped thresholds and default `risk_saturation`, answered the intended level on each day. Nothing of the
engine is configured here. Two properties of the engine shape the data and are worth knowing:

- the completeness of an assessment drops below 1 (an amber by itself) unless the three measured device limits (cord resistance,
  lumbar IMU, EMG reference) arrive as a valid five-second window ending at `as_of`; so every daily assessment has its measured
  window, stored apart as the app stores them;
- a symptom aggregate is a single window per assessment, so it never has the three points a sustained change needs: the drivers of
  the risk are the cord, EMG and IMU features, and the symptoms act through the triage rules (pain, red flags).

It seeds an astronaut only when it has no sessions and no complete profile (`skipped` otherwise, logged), so it can run on every
start. The telemetry itself is not replayed: the sessions are `processed` with their feature sets and no raw chunks. A run that
fails half way (the engine was reachable at the start and gone later) leaves what it wrote; reseed from an empty database.
"""

import argparse
import asyncio
import hashlib
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from spine_contracts import SCHEMA_VERSION, validation

from monitor.assess_request import build_assess_request
from monitor.db import Store
from monitor.engine import EngineClient, EngineError
from monitor.ground.enqueue import enqueue_ground_day
from monitor.ground.link import GroundWindows, link_state
from monitor.settings import AppSettings
from monitor.views.mission import mission_day_of

_log = logging.getLogger(__name__)

HISTORY_DAYS = 60
LAUNCH_DAYS_AGO = 44  # today is the 45th mission day
MISSION_LENGTH_DAYS = 180
ASSESS_WINDOW = timedelta(days=14)  # AppSettings.assess_window_days
# app/docker-compose.yml: SPINE_APP_GROUND_WINDOW_ANCHOR / _PERIOD_MIN / _UP_MIN.
GROUND_WINDOWS = GroundWindows(datetime(2026, 1, 1, tzinfo=UTC), 15, 5)
DEFAULT_ENGINE_URL = "http://engine:8001"
ENGINE_TIMEOUT_S = 60.0

_NAMESPACE = uuid.UUID("a5c1e910-0000-4000-8000-5eed0000d3a0")
_LAT = "lat_pulldown_standing_v1"
_CHECK = "spine_length_rest_v1"
_EARTH, _MICRO = "earth_1g", "microgravity"
_REP_COUNT = "exercise.rep_count"
_STRETCH_CHANGE, _IMU, _EMG = "stretch.full_spine.resistance_change_pct", "imu.lumbar.sensor_x.orientation_deg", "emg.reference.normalized_amplitude"
_COMPLETE_PROFILE = ("launch_date", "landing_date", "spine_history", "space_history", "fitness")

# Time of day (UTC) of each thing of a day.
_CONTEXT_AT, _REPORT_AT, _ODI_AT = (6, 0), (8, 30), (8, 45)
_CHECK_AT, _EXERCISE_AT, _POST_CHECK_AT = (9, 30), (10, 0), (10, 30)
_ASSESS_AT = (21, 0)
_FLAG_AT, _CONTACT_AT, _FLAG_ASSESS_AT = (20, 0), (20, 20), (20, 41)

type Knots = tuple[tuple[int, float], ...]  # (days before today, value), oldest first; linear in between, flat outside
type Feature = tuple[str, str, float, float]  # id, unit, centre, pre-flight half spread

_EXERCISE: tuple[Feature, ...] = (
    ("emg.latissimus_dorsi.left.rms_pct_mvc", "%", 41.5, 3.0),
    ("emg.latissimus_dorsi.left.median_freq_hz", "Hz", 87.3, 4.0),
    ("imu.t9.sagittal.rom_deg", "deg", 28.4, 2.0),
    ("stretch.full_spine.peak_elongation_mm", "mm", 6.8, 0.6),
    (_REP_COUNT, "count", 60.0, 0.0),
)
_CHECKS: tuple[Feature, ...] = (
    ("stretch.full_spine.elongation_mm", "mm", 4.0, 0.3),
    ("stretch.full_spine.elongation_pct", "%", 0.62, 0.05),
)
_NOISE_Z = 0.3  # in-flight scatter around the arc, in z units: below the 0.5 tolerance of the change detector


@dataclass(frozen=True)
class RedFlag:
    days_back: int
    flag: str
    note: str


@dataclass(frozen=True)
class Crew:
    astronaut_id: str
    device_id: str
    profile: Mapping[str, Any]
    scale: float  # of the centres of the features (people differ)
    rep_count: int
    exercise_every: int  # days between in-flight exercise sessions
    z: Mapping[str, Knots]  # feature id -> z against the baseline, by days before today (absent: no drift)
    pain: Knots
    stiffness: Knots
    sleep_hours: Knots
    stretch_change_pct: Knots  # measured device limit: below 4 normal, 4 caution, 7.5 danger
    imu_deg: float
    emg_counts: float
    medication_from_pain: float
    red_flag: RedFlag | None = None


def _profile(**parts: Any) -> dict[str, Any]:
    return parts


CREW: tuple[Crew, ...] = (
    Crew(
        astronaut_id="AST-01",
        device_id="sim-01",
        profile=_profile(
            date_of_birth="1988-04-12",
            sex_at_birth="female",
            gender_identity="woman",
            anthropometry={
                "height_standing_cm": 172,
                "height_sitting_cm": 90,
                "body_mass_kg": 68,
                "skinfold_mm": [
                    {"muscle": "latissimus_dorsi", "side": "left", "thickness_mm": 11},
                    {"muscle": "latissimus_dorsi", "side": "right", "thickness_mm": 11.5},
                ],
            },
            spine_history={
                "low_back_pain_history": True,
                "disc_herniation": {"has_history": False},
                "spine_surgery": {"has_history": False},
                "scoliosis": {"present": False},
                "degenerative_discs_on_preflight_mri": {"present": True, "levels": ["l4", "l5"]},
                "notes": "Fictional profile for the demo.",
            },
            space_history={"previous_missions": 1, "cumulative_microgravity_days": 186, "last_mission_end_date": "2023-03-05"},
            fitness={"dominant_side": "right", "training_level": "high", "lat_pulldown_reference_load_kg": 45},
        ),  # fmt: skip
        scale=1.0,
        rep_count=60,
        exercise_every=2,
        z={
            "stretch.full_spine.elongation_mm": ((15, 0.0), (11, 1.3), (0, 1.6)),
            "stretch.full_spine.peak_elongation_mm": ((15, 0.0), (11, 1.2), (0, 1.5)),
            "emg.latissimus_dorsi.left.median_freq_hz": ((15, 0.0), (9, -0.8), (0, -1.0)),
        },
        pain=((59, 1.5), (14, 1.8), (6, 2.5), (0, 4.0)),
        stiffness=((59, 2.0), (14, 2.2), (6, 3.0), (0, 4.5)),
        sleep_hours=((59, 7.6), (14, 7.4), (6, 6.8), (0, 6.2)),
        stretch_change_pct=((59, 1.2), (0, 1.8)),
        imu_deg=29.5,
        emg_counts=965.0,
        medication_from_pain=4.0,
    ),  # fmt: skip
    Crew(
        astronaut_id="AST-02",
        device_id="sim-02",
        profile=_profile(
            date_of_birth="1981-09-30",
            sex_at_birth="male",
            gender_identity="man",
            anthropometry={
                "height_standing_cm": 181,
                "height_sitting_cm": 95,
                "body_mass_kg": 82,
                "skinfold_mm": [
                    {"muscle": "latissimus_dorsi", "side": "left", "thickness_mm": 9},
                    {"muscle": "latissimus_dorsi", "side": "right", "thickness_mm": 9.5},
                ],
            },
            spine_history={
                "low_back_pain_history": False,
                "disc_herniation": {"has_history": False},
                "spine_surgery": {"has_history": False},
                "scoliosis": {"present": False},
                "degenerative_discs_on_preflight_mri": {"present": False},
                "notes": "Fictional profile for the demo.",
            },
            space_history={"previous_missions": 0, "cumulative_microgravity_days": 0},
            fitness={"dominant_side": "left", "training_level": "moderate", "lat_pulldown_reference_load_kg": 60},
        ),  # fmt: skip
        scale=1.12,
        rep_count=48,
        exercise_every=3,
        z={
            "stretch.full_spine.elongation_mm": ((20, 0.0), (16, 1.1), (8, 1.6), (3, 2.4), (0, 3.0)),
            "stretch.full_spine.elongation_pct": ((20, 0.0), (16, 1.1), (8, 1.6), (3, 2.4), (0, 3.0)),
            "stretch.full_spine.peak_elongation_mm": ((17, 0.0), (13, 1.3), (6, 2.0), (0, 2.8)),
            "emg.latissimus_dorsi.left.median_freq_hz": ((14, 0.0), (8, -1.5), (0, -2.6)),
            "imu.t9.sagittal.rom_deg": ((10, 0.0), (4, 1.5), (0, 2.2)),
        },
        pain=((59, 2.0), (22, 2.4), (12, 4.0), (5, 5.5), (2, 7.0), (0, 7.8)),
        stiffness=((59, 2.5), (22, 3.0), (12, 4.5), (5, 6.0), (0, 7.5)),
        sleep_hours=((59, 7.4), (22, 7.2), (10, 6.2), (0, 4.8)),
        stretch_change_pct=((59, 1.4), (4, 1.8), (2, 3.2), (1, 4.6), (0, 4.9)),
        imu_deg=30.5,
        emg_counts=950.0,
        medication_from_pain=4.0,
        red_flag=RedFlag(1, "leg_weakness", "Left leg gave way for a moment after the last exercise."),
    ),  # fmt: skip
)


# --- deterministic helpers -------------------------------------------------------------------


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid(astronaut_id: str, kind: str, key: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{astronaut_id}/{kind}/{key}"))


def _noise(*key: object) -> float:
    """Deterministic pseudo-random value in [-1, 1) of the key (no random state, no clock)."""
    digest = hashlib.sha256("/".join(map(str, key)).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**63 - 1


def _curve(knots: Knots, days_back: int) -> float:
    if days_back >= knots[0][0]:
        return knots[0][1]
    for (k0, v0), (k1, v1) in zip(knots, knots[1:], strict=False):
        if k1 <= days_back <= k0:
            return v1 + (v0 - v1) * (days_back - k1) / (k0 - k1)
    return knots[-1][1]


def robust_scale(entry: Mapping[str, Any]) -> float:
    """What the engine's robust z-score divides by (1.4826 MAD, else the std, else a floor of 5 % of the median)."""
    for scale in (1.4826 * entry["mad"], entry["std"]):
        if scale > 1e-6:
            return scale
    return max(0.05 * abs(entry["median"]), 1e-6)


def ground_receipt(enqueued: datetime, windows: GroundWindows = GROUND_WINDOWS) -> datetime:
    """When the ground station confirms an entry queued at `enqueued`: a few seconds after the first moment the link is up."""
    received = enqueued + timedelta(seconds=2 + int((_noise("receipt", enqueued.isoformat()) + 1) * 9))
    while (link := link_state(windows, received)) is not None and link["state"] == "down":
        received += timedelta(minutes=link["back_in_min"])
    return received


def _checked(schema: str, payload: dict[str, Any]) -> dict[str, Any]:
    found = validation.errors(schema, payload)
    if found:
        raise ValueError(f"demo seed: {schema} does not validate: {'; '.join(found[:3])}")
    return payload


# --- the timeline of one astronaut -----------------------------------------------------------


@dataclass(frozen=True)
class _Day:
    index: int
    date: date
    days_back: int
    phase: str
    gravity: str
    mission_day: int

    def at(self, hhmm: tuple[int, int], seconds: int = 0) -> datetime:
        return datetime(self.date.year, self.date.month, self.date.day, hhmm[0], hhmm[1], seconds, tzinfo=UTC)

    @property
    def context(self) -> dict[str, str]:
        return {"phase": self.phase, "gravity_environment": self.gravity}


def _days(today: date) -> tuple[date, list[_Day]]:
    launch = today - timedelta(days=LAUNCH_DAYS_AGO)
    first = today - timedelta(days=HISTORY_DAYS - 1)
    days = []
    for index in range(HISTORY_DAYS):
        day = first + timedelta(days=index)
        inflight = day >= launch
        days.append(
            _Day(index, day, HISTORY_DAYS - 1 - index, "inflight" if inflight else "preflight", _MICRO if inflight else _EARTH, mission_day_of(launch, day))
        )
    return launch, days


def _profile_of(crew: Crew, launch: date, first_day: date) -> dict[str, Any]:
    updated = datetime(first_day.year, first_day.month, first_day.day, 7, 0, tzinfo=UTC)
    profile = {"schema_version": SCHEMA_VERSION, "astronaut_id": crew.astronaut_id, "updated_at": _iso(updated), **crew.profile}
    profile["anthropometry"] = {**profile["anthropometry"], "body_mass_measured_at": _iso(updated + timedelta(minutes=-30))}
    profile["launch_date"] = launch.isoformat()
    profile["landing_date"] = (launch + timedelta(days=MISSION_LENGTH_DAYS)).isoformat()
    return _checked("domain/astronaut-profile", profile)


def _skip_reason(store: Store, astronaut_id: str) -> str | None:
    if sessions := store.session_rows(astronaut_id):
        return f"it already has {len(sessions)} sessions"
    profile = store.profile(astronaut_id)
    if profile is not None and all(key in profile for key in _COMPLETE_PROFILE) and "height_sitting_cm" in profile["anthropometry"]:
        return "it already has a complete profile"
    return None


# --- what a day contains ---------------------------------------------------------------------


def _context(crew: Crew, day: _Day, pain: float) -> dict[str, Any]:
    inflight = day.phase == "inflight"
    context: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "astronaut_id": crew.astronaut_id, "date": day.date.isoformat(), "mission_day": day.mission_day,
        **day.context,
        "exercise": [{"modality": "ared", "duration_min": 45, "load_kg": 60, "rpe": 7}] if inflight else [{"modality": "other", "duration_min": 40, "rpe": 6}],
        "countermeasures": [{"type": "axial_loading_suit", "duration_min": 120}] if inflight else [{"type": "stretching", "duration_min": 15}],
    }  # fmt: skip
    if pain >= crew.medication_from_pain:
        context["medication"] = [{"name": "ibuprofen", "category": "anti_inflammatory", "dose_mg": 400, "taken_at": _iso(day.at((7, 50)))}]
    return _checked("domain/daily-context", context)


def _nrs(value: float) -> int:
    return max(0, min(10, round(value)))


def _daily_report(crew: Crew, day: _Day) -> dict[str, Any]:
    key = (crew.astronaut_id, day.date)
    pain = _nrs(_curve(crew.pain, day.days_back) + 0.7 * _noise("pain", *key))
    hours = round(_curve(crew.sleep_hours, day.days_back) + 0.4 * _noise("sleep", *key), 1)
    stamp = day.at(_REPORT_AT) + timedelta(minutes=int(10 * _noise("report-time", *key)))
    report = {
        "schema_version": SCHEMA_VERSION, "report_id": _uid(crew.astronaut_id, "daily", _iso(stamp)), "astronaut_id": crew.astronaut_id,
        "timestamp": _iso(stamp), "mission_day": day.mission_day, "source": "simulator", "kind": "daily",
        "pain_nrs": pain, "pain_nrs_worst_24h": _nrs(pain + 1 + 0.9 * _noise("worst", *key)) if pain else 0,
        "pain_locations": [{"region": "lumbar", "side": "bilateral"}],
        "stiffness_nrs": _nrs(_curve(crew.stiffness, day.days_back) + 0.7 * _noise("stiffness", *key)),
        "sleep": {"hours": hours, "quality_nrs": _nrs(hours - 2.5 + _noise("quality", *key))},
        "red_flags": [],
    }  # fmt: skip
    report["pain_nrs_worst_24h"] = max(report["pain_nrs_worst_24h"], pain)
    return _checked("domain/self-report", report)


def _weekly_odi(crew: Crew, day: _Day) -> dict[str, Any]:
    stamp = day.at(_ODI_AT)
    base = _curve(crew.pain, day.days_back) / 2.5
    items = [
        {"item_id": item, "score": max(0, min(5, round(base + weight + 0.5 * _noise("odi", crew.astronaut_id, day.date, item))))}
        for item, weight in (("pain_intensity", 0.0), ("lifting", 0.6), ("sitting", -0.3), ("sleeping", 0.2))
    ]
    report = {
        "schema_version": SCHEMA_VERSION, "report_id": _uid(crew.astronaut_id, "weekly_odi", _iso(stamp)), "astronaut_id": crew.astronaut_id,
        "timestamp": _iso(stamp), "mission_day": day.mission_day, "source": "simulator", "kind": "weekly_odi", "items": items,
    }  # fmt: skip
    return _checked("domain/self-report", report)


def _red_flag_report(crew: Crew, day: _Day, flag: RedFlag) -> dict[str, Any]:
    stamp = day.at(_FLAG_AT)
    report = {
        "schema_version": SCHEMA_VERSION, "report_id": _uid(crew.astronaut_id, "red_flag", _iso(stamp)), "astronaut_id": crew.astronaut_id,
        "timestamp": _iso(stamp), "mission_day": day.mission_day, "source": "simulator", "kind": "red_flag", "red_flags": [flag.flag],
        "onset": _iso(stamp - timedelta(minutes=25)), "note": flag.note,
    }  # fmt: skip
    return _checked("domain/self-report", report)


def _contact(crew: Crew, day: _Day) -> dict[str, Any]:
    stamp = day.at(_CONTACT_AT)
    contact = {
        "schema_version": SCHEMA_VERSION, "contact_id": _uid(crew.astronaut_id, "contact", _iso(stamp)), "astronaut_id": crew.astronaut_id,
        "at": _iso(stamp), "contact": "crew_medical_officer", "text": "Called the crew medical officer about the new symptom.",
    }  # fmt: skip
    return _checked("domain/medical-contact", contact)


@dataclass(frozen=True)
class _Planned:
    kind: str  # "exercise" | "check"
    start: datetime
    phase: str | None  # of a spine check next to an exercise: "pre" | "post"


def _planned_sessions(crew: Crew, day: _Day) -> list[_Planned]:
    exercising = day.index % 3 == 1 if day.phase == "preflight" else day.days_back % crew.exercise_every == 0
    if not exercising:
        return [_Planned("check", day.at(_CHECK_AT), None)]
    return [_Planned("check", day.at(_CHECK_AT), "pre"), _Planned("exercise", day.at(_EXERCISE_AT), None), _Planned("check", day.at(_POST_CHECK_AT), "post")]


def _session(crew: Crew, day: _Day, planned: _Planned, related: str | None) -> dict[str, Any]:
    start = planned.start
    stretch = {
        "stream_id": "stretch_spine",
        "stream_kind": "stretch",
        "placement": {"spine_region": "full_spine", "span": {"from_level": "c3", "to_level": "s1"}},
        "sample_rate_hz": 20,
    }
    session: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "session_id": _uid(crew.astronaut_id, "session", _iso(start)), "astronaut_id": crew.astronaut_id,
        "device_id": crew.device_id, "source": "simulator", "started_at": _iso(start), "end_reason": "completed",
        "mission_context": {"mission_day": day.mission_day, **day.context},
    }  # fmt: skip
    if planned.kind == "check":
        session |= {"session_type": "spine_check", "protocol_id": _CHECK, "ended_at": _iso(start + timedelta(minutes=5)), "streams": [stretch]}
        if planned.phase is not None and related is not None:
            session |= {"phase": planned.phase, "related_session_id": related}
        return _checked("domain/session", session)
    emg = {"stream_kind": "emg", "sample_rate_hz": 1000}
    placement = {
        "muscle": "latissimus_dorsi",
        "electrode_spacing_cm": 10,
        "span": {"from_level": "t11", "to_level": "l2"},
        "ground": {"location": "iliac_crest"},
    }
    session |= {
        "session_type": "exercise", "protocol_id": _LAT, "ended_at": _iso(start + timedelta(minutes=20)),
        "load_declared": {
            "type": "elastic_band", "nominal_kg": 25, "band_id": "green", "band_stretch_cm": 40,
            "description": "Standing, band anchored overhead, pronated grip.",
        },
        "planned_reps": 60, "planned_sets": 4, "reps_per_set": 15,
        "streams": [
            {"stream_id": "emg_lat_left", **emg, "placement": {**placement, "side": "left"}},
            {"stream_id": "emg_lat_right", **emg, "placement": {**placement, "side": "right"}},
            {"stream_id": "imu_t9", "stream_kind": "imu", "placement": {"spine_level": "t9"}, "sample_rate_hz": 100},
            stretch,
        ],
    }  # fmt: skip
    return _checked("domain/session", session)


def _baseline_entry(baseline: Mapping[str, Any] | None, feature_id: str, protocol_id: str) -> Mapping[str, Any]:
    entries = baseline["entries"] if baseline is not None else []
    for entry in entries:
        if (entry["feature_id"], entry.get("protocol_id"), entry["gravity_environment"]) == (feature_id, protocol_id, _EARTH):
            return entry
    raise ValueError(f"demo seed: the baseline has no Earth entry for {feature_id} ({protocol_id})")


def _feature_value(crew: Crew, day: _Day, feature: Feature, protocol_id: str, baseline: Mapping[str, Any] | None, key: str) -> float:
    feature_id, _, centre, spread = feature
    if feature_id == _REP_COUNT:
        return float(crew.rep_count)
    if day.phase == "preflight":
        return round(centre * crew.scale + spread * crew.scale * _noise("pre", crew.astronaut_id, feature_id, key), 3)
    entry = _baseline_entry(baseline, feature_id, protocol_id)
    knots = crew.z.get(feature_id)
    z = (0.0 if knots is None else _curve(knots, day.days_back)) + _NOISE_Z * _noise("z", crew.astronaut_id, feature_id, key)
    return round(entry["median"] + z * robust_scale(entry), 3)


def _quality(key: str) -> dict[str, Any]:
    return {"score": round(0.9 + 0.08 * (_noise("quality", key) + 1) / 2, 2), "flags": [], "assessed_by": "engine"}


def _feature_set(crew: Crew, day: _Day, session: Mapping[str, Any], baseline: Mapping[str, Any] | None) -> dict[str, Any]:
    start = datetime.fromisoformat(session["started_at"])
    end = datetime.fromisoformat(session["ended_at"])
    protocol_id = session["protocol_id"]
    exercise = protocol_id == _LAT
    features = [
        {
            "feature_id": feature[0], "value": _feature_value(crew, day, feature, protocol_id, baseline, session["session_id"]), "unit": feature[1],
            "n": 10 if exercise else 100, "quality": _quality(f"{session['session_id']}/{feature[0]}"),
        }
        for feature in (_EXERCISE if exercise else _CHECKS)
    ]  # fmt: skip
    window = {"start": _iso(start + timedelta(minutes=1)), "end": _iso(start + timedelta(minutes=6))} if exercise else {"start": _iso(start), "end": _iso(end)}
    feature_set = {
        "schema_version": SCHEMA_VERSION, "astronaut_id": crew.astronaut_id, "session_id": session["session_id"], "protocol_id": protocol_id,
        "gravity_environment": day.gravity, "source": "simulator", "computed_at": _iso(end + timedelta(minutes=3)), "engine_version": "demo-seed",
        "window": window, "features": features,
    }  # fmt: skip
    return _checked("domain/feature-set", feature_set)


def _measured_window(crew: Crew, day: _Day, session: Mapping[str, Any], as_of: datetime) -> dict[str, Any]:
    key = (crew.astronaut_id, as_of.isoformat())
    values = {
        _STRETCH_CHANGE: (round(_curve(crew.stretch_change_pct, day.days_back) + 0.2 * _noise("stretch", *key), 2), "%"),
        _IMU: (round(crew.imu_deg + 1.2 * _noise("imu", *key), 1), "deg"),
        _EMG: (round(crew.emg_counts + 12 * _noise("emg", *key)), "counts"),
    }
    feature_set = {
        "schema_version": SCHEMA_VERSION, "astronaut_id": crew.astronaut_id, "session_id": session["session_id"], "protocol_id": session["protocol_id"],
        "gravity_environment": day.gravity, "source": "simulator", "computed_at": _iso(as_of), "engine_version": "demo-seed",
        "window": {"start": _iso(as_of - timedelta(seconds=5)), "end": _iso(as_of)},
        "features": [
            {"feature_id": fid, "value": value, "unit": unit, "n": 1, "quality": {"score": 0.95, "flags": [], "assessed_by": "engine"}}
            for fid, (value, unit) in values.items()
        ],
    }  # fmt: skip
    return _checked("domain/feature-set", feature_set)


# --- the engine --------------------------------------------------------------------------------


class SeedEngine(EngineClient):
    """The app's engine client without the check of the requests: the engine validates them itself, and checking each one twice is two
    thirds of the run (a request carries two weeks of data). The responses are still validated, and so is everything that is stored."""

    async def _post(self, path: str, request_schema: str, response_schema: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise EngineError(f"{path}: engine unreachable: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            raise EngineError(f"{path}: engine returned non-JSON (HTTP {response.status_code})", status=response.status_code) from None
        if response.is_error:
            detail = payload.get("detail") or payload.get("title") or "error" if isinstance(payload, dict) else "error"
            raise EngineError(f"{path}: HTTP {response.status_code}: {detail}", status=response.status_code)
        found = validation.errors(response_schema, payload)
        if found:
            raise EngineError(f"{path}: response violates {response_schema}: {'; '.join(found[:3])}")
        return payload


async def _rebuild_baseline(store: Store, engine: EngineClient, astronaut_id: str, created_at: datetime) -> None:
    """What `Analysis` does after a pre-flight session: every pre-flight feature set and the self-reports around them."""
    feature_sets = store.feature_sets_of_phase(astronaut_id, "preflight")
    anchors = [datetime.fromisoformat((fs.get("window") or {}).get("end", fs["computed_at"])) for fs in feature_sets]
    request: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "astronaut_id": astronaut_id, "feature_sets": feature_sets,
        "self_reports": store.self_reports_between(astronaut_id, min(anchors) - timedelta(days=7), max(anchors)),
    }  # fmt: skip
    previous = store.latest_baseline(astronaut_id)
    if previous is not None:
        request["previous_baseline_version"] = previous["version"]
    baseline = await engine.baseline(request)
    baseline["baseline_id"] = _uid(astronaut_id, "baseline", str(baseline["version"]))
    baseline["created_at"] = _iso(created_at)
    store.put_baseline(_checked("domain/baseline", baseline))


async def _assess(
    store: Store, engine: EngineClient, crew: Crew, day: _Day, session: Mapping[str, Any], as_of: datetime, end: datetime
) -> dict[str, Any] | None:
    """The assessment of `as_of` with the measured window the device would have sent at that moment."""
    if store.latest_baseline(crew.astronaut_id) is None:
        return None
    store.put_measured_window(_measured_window(crew, day, session, as_of), boot_id=1000 + day.index)
    built = build_assess_request(store, crew.astronaut_id, as_of, ASSESS_WINDOW)
    if built.request is None:
        raise ValueError(f"demo seed: {crew.astronaut_id} cannot be assessed on {day.date}: {built.skipped}")
    assessment = await engine.assess(built.request)
    assessment["assessment_id"] = _uid(crew.astronaut_id, "assessment", _iso(as_of))
    store.put_assessment(_checked("domain/assessment", assessment))
    _send_to_ground(store, crew.astronaut_id, as_of, end)
    return assessment


def _send_to_ground(store: Store, astronaut_id: str, as_of: datetime, end: datetime) -> None:
    """Queues the summary of the day as the app does after an assessment, and confirms it when the ground answered by `end`."""
    enqueued = as_of + timedelta(seconds=20 + int((_noise("enqueue", astronaut_id, as_of.isoformat()) + 1) * 400))
    if enqueue_ground_day(store, astronaut_id, as_of.date(), enqueued) is None:
        raise ValueError(f"demo seed: the ground summary of {astronaut_id} on {as_of.date()} was rejected")
    item = store.ground_outbox_items(astronaut_id, 1)[0]
    received = ground_receipt(enqueued)
    if received <= end:
        store.mark_ground_sent(item.key, received, item.payload)


async def _seed_astronaut(store: Store, engine: EngineClient, crew: Crew, end: datetime) -> None:
    launch, days = _days(end.date())
    store.register_crew(crew.astronaut_id, datetime.combine(days[0].date, datetime.min.time(), UTC))
    store.put_profile(_profile_of(crew, launch, days[0].date))
    odi_days = {end.date() - timedelta(days=3 + 7 * n) for n in range(9)}
    last_as_of: datetime | None = None
    last_session: tuple[_Day, Mapping[str, Any]] | None = None

    for day in days:
        if day.at(_CONTEXT_AT) <= end:
            store.put_daily_context(_context(crew, day, _curve(crew.pain, day.days_back)))
        if day.date < end.date() and day.at(_REPORT_AT) <= end:
            store.put_self_report(_daily_report(crew, day))
        if day.date in odi_days and day.at(_ODI_AT) <= end:
            store.put_self_report(_weekly_odi(crew, day))

        sessions: list[Mapping[str, Any]] = []
        planned_today = [p for p in _planned_sessions(crew, day) if p.start + timedelta(minutes=20) <= end]
        exercise = next((p for p in planned_today if p.kind == "exercise"), None)
        related = None if exercise is None else _uid(crew.astronaut_id, "session", _iso(exercise.start))
        for planned in planned_today:
            session = _session(crew, day, planned, related)
            store.insert_session(session, "processed")
            feature_set = _feature_set(crew, day, session, store.latest_baseline(crew.astronaut_id))
            store.put_feature_set(feature_set, phase=day.phase, anchor_at=session["started_at"])
            sessions.append(session)
        if sessions and day.phase == "preflight":
            # The app rebuilds it after each pre-flight session; once a day gives the same last version and a third of the calls.
            last = store.feature_set(sessions[-1]["session_id"])
            assert last is not None
            await _rebuild_baseline(store, engine, crew.astronaut_id, datetime.fromisoformat(last["computed_at"]) + timedelta(minutes=2))
        if sessions:
            last_session = (day, sessions[-1])

        flagged = crew.red_flag is not None and day.days_back == crew.red_flag.days_back
        if flagged and crew.red_flag is not None and day.at(_FLAG_AT) <= end:
            store.put_self_report(_red_flag_report(crew, day, crew.red_flag))
            store.put_contact(_contact(crew, day))
        as_of = day.at(_FLAG_ASSESS_AT) if flagged else day.at(_ASSESS_AT)
        if sessions and as_of <= end and await _assess(store, engine, crew, day, sessions[-1], as_of, end) is not None:
            last_as_of = as_of

    # A run late in the evening: yesterday's assessment would be about to go stale, so one more is made just before `end`.
    if last_session is not None and (last_as_of is None or end - last_as_of >= timedelta(hours=23)):
        day, session = last_session
        as_of = (end - timedelta(minutes=10)).replace(second=0, microsecond=0)
        await _assess(store, engine, crew, next(d for d in days if d.date == as_of.date()), session, as_of, end)


async def seed_demo(store: Store, engine: EngineClient, end: datetime) -> dict[str, str]:
    """Seeds every astronaut of `CREW` that is empty; returns `seeded` or `skipped: reason` for each. `end` is the instant 'now'."""
    if end.tzinfo is None:
        raise ValueError("end must be timezone-aware")
    result: dict[str, str] = {}
    for crew in CREW:
        reason = _skip_reason(store, crew.astronaut_id)
        if reason is not None:
            _log.info("%s: skipped, %s", crew.astronaut_id, reason)
            result[crew.astronaut_id] = f"skipped: {reason}"
            continue
        await _seed_astronaut(store, engine, crew, end)
        _log.info("%s: seeded", crew.astronaut_id)
        result[crew.astronaut_id] = "seeded"
    return result


# --- command line ------------------------------------------------------------------------------


def _aware(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 instant: {text!r}") from exc
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError(f"the instant needs a UTC offset: {text!r}")
    return moment.astimezone(UTC)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m monitor.demo_seed", description="Seed the demo astronauts with ~60 days of history.")
    parser.add_argument("--end", type=_aware, default=None, help="the instant 'now' of the history, with offset (default: the current time)")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL, help=f"engine base URL (default {DEFAULT_ENGINE_URL})")
    parser.add_argument("--db-path", type=Path, default=None, help="SQLite file (default: SPINE_APP_DB_PATH, as the app)")
    return parser.parse_args(argv)


async def _run(end: datetime, engine_url: str, db_path: Path, transport: httpx.AsyncBaseTransport | None) -> int:
    async with httpx.AsyncClient(base_url=engine_url, timeout=ENGINE_TIMEOUT_S, transport=transport) as http:
        try:
            (await http.get("/health")).raise_for_status()  # before the database is touched: a half-seeded file is skipped forever
        except httpx.HTTPError as exc:
            _log.error("the engine at %s is not ready: %s", engine_url, type(exc).__name__)
            return 1
        store = Store(db_path)
        try:
            await seed_demo(store, SeedEngine(http), end)
        except (EngineError, ValueError) as exc:
            _log.error("seeding failed: %s", exc)
            return 1
        finally:
            store.close()
    return 0


def main(argv: Sequence[str] | None = None, *, engine_transport: httpx.AsyncBaseTransport | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    end = args.end or datetime.now(UTC)
    db_path = args.db_path or AppSettings().db_path
    return asyncio.run(_run(end, args.engine_url, db_path, engine_transport))


if __name__ == "__main__":
    raise SystemExit(main())
