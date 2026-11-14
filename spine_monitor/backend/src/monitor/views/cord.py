"""Cord-resistance protocols view (`app/ui-cord-protocols`): two independent sessions, back straight and maximum flexion.

Each posture has a personal Earth reference (pre-flight, 1 g) and periodic in-flight follow-ups. The last VALID measurement of
a posture is the latest feature value of its protocol from a session that completed, with quality at or above the minimum score;
Earth and flight are told apart by phase and gravity together (the reference is pre-flight at 1 g, a follow-up is in flight at another gravity;
any other combination is neither, e.g. a pre-flight session in microgravity).

The cadence is an input (days between follow-ups), not a clinical rule: its default is an example, pending Aguado. The length estimate
is calculated by the engine (`/stretch-estimate`) from the bench cord measurements under explicit assumptions; it is labelled as
calculated, never as a measured or clinically validated length, and it is outside the triage.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from spine_contracts import SCHEMA_VERSION
from spine_contracts.catalog import CATALOG_DIR

from monitor.views.lumbar import EARTH_GRAVITY, instant, measured_at

STRAIGHT = "spine_resistance_straight_v1"
FLEXION = "spine_resistance_max_flexion_v1"
ASSUMPTIONS = ["ASSUMPTION_UNIFORM_RESISTANCE", "ASSUMPTION_NEGLIGIBLE_CONTACT_RESISTANCE", "ASSUMPTION_UNIFORM_DEFORMATION"]
_POSTURES = {
    STRAIGHT: ("straight", "stretch.full_spine.straight_resistance_ohm"),
    FLEXION: ("max_flexion", "stretch.full_spine.max_flexion_resistance_ohm"),
}
CORD_PROTOCOLS = tuple(_POSTURES)

type Measurement = dict[str, Any]
type Outcome = dict[str, Any] | str  # an estimate view, or the code of why there is none


class PostureMeasurements(NamedTuple):
    earth: Measurement | None
    follow_up: Measurement | None


type Measurements = dict[str, PostureMeasurements]


def _invalid_reason(session: Mapping[str, Any], feature: Mapping[str, Any] | None, min_score: float) -> str | None:
    """Why a cord measurement is not valid, or None when it is: the session completed, a positive value, quality at or above the minimum."""
    if session.get("end_reason") != "completed":
        return "CORD_INVALID_NOT_COMPLETED"
    if feature is None or not feature["value"] > 0:
        return "CORD_INVALID_NO_VALUE"
    if feature["quality"]["score"] < min_score:
        return "CORD_INVALID_LOW_QUALITY"
    return None


def _feature(feature_set: Mapping[str, Any], feature_id: str) -> Mapping[str, Any] | None:
    return next((f for f in feature_set["features"] if f["feature_id"] == feature_id), None)


def _gravity(session: Mapping[str, Any], feature_set: Mapping[str, Any] | None) -> str:
    return (feature_set or {}).get("gravity_environment") or session["mission_context"]["gravity_environment"]


def _measurement(session: Mapping[str, Any], feature_set: Mapping[str, Any], feature_id: str, min_score: float) -> Measurement | None:
    feature = _feature(feature_set, feature_id)
    if feature is None or _invalid_reason(session, feature, min_score) is not None:
        return None
    return {
        "session_id": session["session_id"],
        "measured_at": measured_at(feature_set).isoformat().replace("+00:00", "Z"),
        "resistance_ohm": feature["value"],
        "gravity_environment": _gravity(session, feature_set),
    }


def _kind(phase: str, gravity: str) -> str | None:
    """Phase and gravity together: the Earth reference is pre-flight at 1 g, a follow-up is in flight at another gravity; anything else is neither."""
    if phase == "preflight" and gravity == EARTH_GRAVITY:
        return "earth"
    if phase == "inflight" and gravity != EARTH_GRAVITY:
        return "follow_up"
    return None


def collect_measurements(rows: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]], min_score: float) -> Measurements:
    """Latest valid Earth reference and in-flight follow-up of each posture from (session, feature set) pairs."""
    found: dict[str, dict[str, Measurement]] = {protocol: {} for protocol in CORD_PROTOCOLS}
    for session, feature_set in rows:
        protocol = session["protocol_id"]
        if protocol not in _POSTURES or (m := _measurement(session, feature_set, _POSTURES[protocol][1], min_score)) is None:
            continue
        kind = _kind(session["mission_context"]["phase"], m["gravity_environment"])
        if kind is not None and (kind not in found[protocol] or instant(m["measured_at"]) >= instant(found[protocol][kind]["measured_at"])):
            found[protocol][kind] = m
    return {p: PostureMeasurements(found[p].get("earth"), found[p].get("follow_up")) for p in CORD_PROTOCOLS}


def _due(state: PostureMeasurements, phase: str | None, now: datetime, cadence_days: int) -> tuple[bool, str]:
    if phase == "preflight" and state.earth is None:
        return True, "CORD_NO_EARTH_REFERENCE"
    if phase == "inflight":
        if state.follow_up is None:
            return True, "CORD_NO_FOLLOW_UP"
        if instant(state.follow_up["measured_at"]) <= now - timedelta(days=cadence_days):
            return True, "CORD_FOLLOW_UP_OVERDUE"
    return False, "CORD_UP_TO_DATE"


def due_protocols(measurements: Measurements, phase: str | None, now: datetime, cadence_days: int) -> tuple[str, ...]:
    """Protocols whose session is due now, in the fixed order straight, max flexion."""
    return tuple(p for p in CORD_PROTOCOLS if _due(measurements[p], phase, now, cadence_days)[0])


@dataclass(frozen=True)
class EstimateRequest:
    """Body of the engine's POST /stretch-estimate and the span it refers to."""

    body: dict[str, Any]
    reference_length_mm: float


def _straight_back_span_mm() -> float:
    """Distance between the back contacts in the straight posture, from the cord measurements catalog."""
    data = json.loads((CATALOG_DIR / "cord-measurements.json").read_text(encoding="utf-8"))
    return float(data["back"]["contact_spacing_mm"])


def estimate_request(protocol_id: str, state: PostureMeasurements) -> EstimateRequest | str:
    """What to ask the engine for, or the code of why no estimate can be asked.

    The model compares the current resistance with the straight-back Earth reference over the straight-back contact span; the span between
    the contacts in maximum flexion is not measured (pending Aguado), so that posture gets no estimate rather than an invented length.
    """
    if state.earth is None:
        return "ESTIMATE_NO_EARTH_REFERENCE"
    if state.follow_up is None:
        return "ESTIMATE_NO_FOLLOW_UP"
    if protocol_id != STRAIGHT:
        return "ESTIMATE_REFERENCE_LENGTH_UNKNOWN"
    span = _straight_back_span_mm()
    body = {
        "schema_version": SCHEMA_VERSION,
        "baseline_resistance_ohm": state.earth["resistance_ohm"],
        "current_resistance_ohm": state.follow_up["resistance_ohm"],
        "reference_length_mm": span,
    }
    return EstimateRequest(body, span)


def estimate_view(request: EstimateRequest, response: Mapping[str, Any]) -> dict[str, Any]:
    """The engine's answer as the view's estimate: always flagged as calculated, not measured, not validated and outside the triage."""
    return {
        "calculated": True,
        "measured_length": False,
        "clinically_validated": False,
        "affects_triage": False,
        "model": response["model"],
        "reference_length_mm": request.reference_length_mm,
        "estimated_length_mm": response["estimated_length_mm"],
        "elongation_mm": response["elongation_mm"],
        "assumptions": list(ASSUMPTIONS),
    }


def build_cord_protocols(
    measurements: Measurements, phase: str | None, now: datetime, cadence_days: int, estimates: Mapping[str, Outcome]
) -> dict[str, Any]:
    """The view. `estimates` has, per protocol, the estimate or the code of why there is none (a missing protocol counts as no follow-up)."""
    postures = []
    for protocol in CORD_PROTOCOLS:
        state = measurements[protocol]
        due, reason = _due(state, phase, now, cadence_days)
        outcome = estimates.get(protocol, "ESTIMATE_NO_FOLLOW_UP")
        postures.append(
            {
                "protocol_id": protocol,
                "posture": _POSTURES[protocol][0],
                "due": due,
                "due_reason": reason,
                "earth_reference": state.earth,
                "last_follow_up": state.follow_up,
                "estimate": None if isinstance(outcome, str) else outcome,
                "estimate_unavailable": outcome if isinstance(outcome, str) else None,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "cadence_days": cadence_days, "postures": postures}


def build_cord_history(rows: Sequence[tuple[Mapping[str, Any], Mapping[str, Any] | None]], min_score: float, limit: int) -> dict[str, Any]:
    """The view `app/ui-cord-history`: per posture the `limit` newest measurements, valid or not, from (session, feature set) pairs.

    A session without feature set (aborted, rejected or whose analysis failed) is an invalid entry, dated at its start.

    Only measurements: the calculated estimate is never stored and has no place here.
    """
    postures = []
    for protocol in CORD_PROTOCOLS:
        posture, feature_id = _POSTURES[protocol]
        entries = []
        for session, feature_set in rows:
            if session["protocol_id"] != protocol:
                continue
            feature = None if feature_set is None else _feature(feature_set, feature_id)
            reason = _invalid_reason(session, feature, min_score)
            when = instant(session["started_at"]) if feature_set is None else measured_at(feature_set)
            entries.append(
                (
                    when,
                    {
                        "session_id": session["session_id"],
                        "measured_at": when.isoformat().replace("+00:00", "Z"),
                        "gravity_environment": _gravity(session, feature_set),
                        "resistance_ohm": None if feature is None else feature["value"],
                        "quality_score": None if feature is None else feature["quality"]["score"],
                        "valid": reason is None,
                        "invalid_reason": reason,
                    },
                )
            )
        newest = sorted(entries, key=lambda entry: entry[0], reverse=True)[:limit]
        postures.append({"protocol_id": protocol, "posture": posture, "measurements": [entry for _, entry in newest]})
    return {"schema_version": SCHEMA_VERSION, "limit": limit, "postures": postures}
