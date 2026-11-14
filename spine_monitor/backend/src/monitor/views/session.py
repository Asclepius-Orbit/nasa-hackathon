"""Session view (`app/ui-session`): the guided session screen.

Pure: protocol facts come from a catalog entry given by the caller, the rest from the stored session, its
commands with their acks and the last device status. Live repetitions are not part of this view.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from spine_contracts import SCHEMA_VERSION

from monitor.db import CommandRow, SessionRow
from monitor.views.lumbar import instant

FINISHED_STATES = ("ended", "processing", "processed", "processing_failed")
STEP_IDS = ("fit", "check", "mvc", "reps", "finish")
_REPS = STEP_IDS.index("reps")


def _acked_at(command: CommandRow) -> str | None:
    """Timestamp of the `completed` ack of the command, the only one that counts as acknowledged."""
    return next((ack["timestamp"] for ack in command.acks if ack["status"] == "completed"), None)


def _calibrations(commands: Sequence[CommandRow]) -> list[CommandRow]:
    """The calibrate commands, oldest issued first."""
    return sorted((c for c in commands if c.type == "calibrate"), key=lambda c: instant(c.payload["issued_at"]))


def _mvc_done(calibrations: Sequence[CommandRow], protocol_entry: Mapping[str, Any] | None) -> bool:
    """Whether a calibration finished the `mvc` step.

    A protocol with `emg` among its fit items needs an acked `emg_mvc` calibration; any other protocol, and a
    session without catalog entry, accept any acked calibration (`stretch`, `emg_mvc` or `imu`).
    """
    needs_emg = protocol_entry is not None and any(item["id"] == "emg" for item in protocol_entry.get("fit_items", []))
    return any(_acked_at(c) is not None and (not needs_emg or c.payload["calibration_type"] == "emg_mvc") for c in calibrations)


def _sensors_ok(device_status: Mapping[str, Any] | None, now: datetime, stale_device_hours: float) -> bool:
    """A recent device status that is not offline and reports at least one stream, none of them stalled or disabled.

    A device that reports no streams has nothing to check, so it does not pass; neither does an old status
    (older than `stale_device_hours`), which says nothing about the sensors now.
    """
    if device_status is None or device_status["status"] == "offline":
        return False
    if now - instant(device_status["timestamp"]) > timedelta(hours=stale_device_hours):
        return False
    streams = device_status.get("streams")
    return bool(streams) and all(stream["state"] == "active" for stream in streams)


def _steps(state: str, sensors_ok: bool, calibrated: bool) -> list[dict[str, str]]:
    """The five steps in order. At most one is `now` and none is `done` after a `todo`.

    - starting: the gateway has not started it yet, so `fit` is `now` and the rest `todo`, whatever the device says.
    - rejected: the session never ran; only `fit` (the session exists) is done and nothing is `now`.
    - finished (ended, processing, processed, processing_failed): everything is done, whatever the device says now.
    - otherwise the first step not done is `now` (`reps` only while `running`), the following ones `todo`.
      `check` counts as done once a calibration was acknowledged: it could only be sent after the sensors passed,
      so a stream that stalls later shows in `sensor_checks` without sending the flow back.
    """
    if state in FINISHED_STATES:
        return [{"id": step, "status": "done"} for step in STEP_IDS]
    if state == "starting":
        return [{"id": step, "status": "now" if step == "fit" else "todo"} for step in STEP_IDS]
    if state == "rejected":
        return [{"id": step, "status": "done" if step == "fit" else "todo"} for step in STEP_IDS]
    done = [True, sensors_ok or calibrated, calibrated, False, False]
    first = done.index(False)
    now_allowed = first != _REPS or state == "running"
    return [{"id": step, "status": "done" if i < first else "now" if i == first and now_allowed else "todo"} for i, step in enumerate(STEP_IDS)]


def build_session_view(
    session: SessionRow,
    commands_with_acks: Sequence[CommandRow],
    device_status: Mapping[str, Any] | None,
    protocol_entry: Mapping[str, Any] | None,
    now: datetime,
    stale_device_hours: float,
) -> dict[str, Any]:
    """SessionView. `protocol_entry` is the catalog entry of the session protocol (band, planned_sets, reps_per_set, total_reps,
    rest_between_sets_s, rest_seconds, fit_items as `[{id}]`); without it those fields are null / empty. `calibration` is the latest calibrate command
    of any type, while the `mvc` step depends on the protocol (see `_mvc_done`). `stretched_cm` and `planned_at` are reserved: null."""
    payload = session.payload
    calibrations = _calibrations(commands_with_acks)
    latest = calibrations[-1] if calibrations else None
    streams = [] if device_status is None else device_status.get("streams", [])
    entry = protocol_entry or {}
    band = entry.get("band")
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": payload["session_id"],
        "astronaut_id": payload["astronaut_id"],
        "state": session.state,
        "protocol_id": payload["protocol_id"],
        "band": None if band is None else dict(band),
        "planned_sets": entry.get("planned_sets"),
        "reps_per_set": entry.get("reps_per_set"),
        "total_reps": entry.get("total_reps"),
        "rest_between_sets_s": entry.get("rest_between_sets_s"),
        "rest_seconds": entry.get("rest_seconds"),
        "fit_items": [item["id"] for item in entry.get("fit_items", [])],
        "started_at": payload.get("started_at"),
        "steps": _steps(session.state, _sensors_ok(device_status, now, stale_device_hours), _mvc_done(calibrations, protocol_entry)),
        "sensor_checks": [{"stream": s["stream_id"], "status": "good" if s["state"] == "active" else "weak", "state": s["state"]} for s in streams],
        "calibration": None
        if latest is None
        else {"command_id": latest.payload["command_id"], "acked": _acked_at(latest) is not None, "acked_at": _acked_at(latest)},
        "stretched_cm": None,
        "planned_at": None,
    }
