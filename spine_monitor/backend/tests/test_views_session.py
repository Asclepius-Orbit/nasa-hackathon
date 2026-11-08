"""Session view: protocol facts from the catalog, the five guided steps, sensor checks and calibration."""

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from monitor.db import CommandRow, SessionRow
from monitor.views.session import FINISHED_STATES, build_session_view
from spine_contracts import SCHEMA_VERSION, validation

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "app"

CATALOG = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "catalog" / "protocol-catalog.json").read_text(encoding="utf-8"))
ENTRIES = {e["protocol_id"]: e for e in CATALOG["entries"]}
LAT, REST = ENTRIES["lat_pulldown_standing_v1"], ENTRIES["spine_length_rest_v1"]
SESSION_ID = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"
STATES = ["starting", "running", "rejected", "ended", "processing", "processed", "processing_failed"]
IDS = ["fit", "check", "mvc", "reps", "finish"]


def session(state: str = "running", protocol: str = "lat_pulldown_standing_v1", started_at: str | None = "2026-11-13T10:02:31Z") -> SessionRow:
    payload: dict[str, Any] = {"session_id": SESSION_ID, "protocol_id": protocol}
    if started_at is not None:
        payload["started_at"] = started_at
    return SessionRow(payload, state, None)


def cid(n: int) -> str:
    return f"c0ffee00-0000-4000-8000-{n:012d}"


def calibrate(
    command_id: str,
    issued_at: str,
    ack_status: str | None = "completed",
    ack_at: str = "2026-11-13T10:00:06.400Z",
    kind: str = "emg_mvc",
) -> CommandRow:
    payload = {"command_id": command_id, "type": "calibrate", "issued_at": issued_at, "calibration_type": kind, "session_id": SESSION_ID}
    acks = [] if ack_status is None else [{"command_id": command_id, "status": ack_status, "timestamp": ack_at}]
    return CommandRow(payload, ack_status or "sent", acks)


def device(status: str = "ok", states: tuple[str, ...] = ("active", "active")) -> dict[str, Any]:
    streams = [{"stream_id": f"s{i}", "stream_kind": "emg", "state": state} for i, state in enumerate(states)]
    return {"status": status, "streams": streams}


def statuses(view: dict[str, Any]) -> list[str]:
    assert [s["id"] for s in view["steps"]] == IDS
    return [s["status"] for s in view["steps"]]


def build(
    state: str = "running", commands: list[CommandRow] | None = None, status: dict[str, Any] | None = None, entry: dict[str, Any] | None = LAT, **kw: Any
):
    return build_session_view(session(state, **kw), commands or [], status, entry)


GOOD, ACKED = device(), [calibrate("c0ffee00-0000-4000-8000-000000000004", "2026-11-13T10:00:00Z")]


# --- facts ---------------------------------------------------------------------------------------


def test_a_running_session_with_everything_ready_matches_the_contract_example():
    stream_states = {"emg_lat_left": "active", "imu_l3": "active", "stretch_lumbar": "stalled"}
    status = {"status": "degraded", "streams": [{"stream_id": k, "stream_kind": "emg", "state": v} for k, v in stream_states.items()]}
    result = build(commands=ACKED, status=status)

    assert validation.errors("app/ui-session", result) == []
    assert result == {
        "schema_version": SCHEMA_VERSION,
        "session_id": SESSION_ID,
        "state": "running",
        "protocol_id": "lat_pulldown_standing_v1",
        "band": {"color": "blue", "nominal_kg": 15},
        "total_reps": 10,
        "rest_seconds": 120,
        "fit_items": ["skin", "emg", "imu", "cord"],
        "started_at": "2026-11-13T10:02:31Z",
        "steps": [
            {"id": "fit", "status": "done"},
            {"id": "check", "status": "done"},
            {"id": "mvc", "status": "done"},
            {"id": "reps", "status": "now"},
            {"id": "finish", "status": "todo"},
        ],
        "sensor_checks": [
            {"stream": "emg_lat_left", "status": "good", "state": "active"},
            {"stream": "imu_l3", "status": "good", "state": "active"},
            {"stream": "stretch_lumbar", "status": "weak", "state": "stalled"},
        ],
        "calibration": {"command_id": "c0ffee00-0000-4000-8000-000000000004", "acked": True, "acked_at": "2026-11-13T10:00:06.400Z"},
        "stretched_cm": None,
        "planned_at": None,
    }


def test_a_protocol_without_band_reps_or_rest_has_nulls():
    result = build("starting", entry=REST, protocol="spine_length_rest_v1", started_at=None)

    assert validation.errors("app/ui-session", result) == []
    assert (result["band"], result["total_reps"], result["rest_seconds"], result["fit_items"], result["started_at"]) == (None, None, None, ["cord"], None)


def test_without_catalog_entry_facts_are_null_and_fit_items_empty():
    result = build(entry=None)

    assert validation.errors("app/ui-session", result) == []
    assert (result["band"], result["total_reps"], result["rest_seconds"], result["fit_items"]) == (None, None, None, [])


def test_the_band_is_a_copy_of_the_catalog_entry():
    result = build()

    assert result["band"] == LAT["band"]
    assert result["band"] is not LAT["band"]


def test_reserved_fields_are_always_null():
    result = build("processed", commands=ACKED, status=GOOD)

    assert (result["stretched_cm"], result["planned_at"]) == (None, None)


# --- steps ---------------------------------------------------------------------------------------


def test_fit_is_done_and_check_is_now_without_device_status():
    assert statuses(build("running")) == ["done", "now", "todo", "todo", "todo"]


def test_check_is_done_with_a_device_that_is_ok_and_streams_active():
    assert statuses(build("running", status=GOOD)) == ["done", "done", "now", "todo", "todo"]


def test_check_is_done_with_a_degraded_device_whose_streams_are_active():
    assert statuses(build("running", status=device("degraded"))) == ["done", "done", "now", "todo", "todo"]


@pytest.mark.parametrize("bad", [device("offline"), device("ok", ("active", "stalled")), device("ok", ("disabled",))])
def test_check_stays_now_with_an_offline_device_or_a_stalled_or_disabled_stream(bad):
    assert statuses(build("running", status=bad)) == ["done", "now", "todo", "todo", "todo"]


def test_a_device_without_streams_that_is_not_offline_passes_the_check():
    assert statuses(build("running", status={"status": "ok"})) == ["done", "done", "now", "todo", "todo"]


def test_mvc_is_done_with_an_acked_calibration_and_reps_is_now_while_running():
    assert statuses(build("running", commands=ACKED, status=GOOD)) == ["done", "done", "done", "now", "todo"]


@pytest.mark.parametrize("ack", [None, "accepted", "rejected", "failed"])
def test_a_calibration_without_a_completed_ack_does_not_finish_mvc(ack):
    commands = [calibrate(cid(1), "2026-11-13T10:00:00Z", ack)]

    assert statuses(build("running", commands=commands, status=GOOD)) == ["done", "done", "now", "todo", "todo"]


def test_an_earlier_acked_calibration_counts_even_if_a_later_one_is_pending():
    commands = [calibrate(cid(1), "2026-11-13T10:00:00Z"), calibrate(cid(2), "2026-11-13T10:01:00Z", None)]

    assert statuses(build("running", commands=commands, status=GOOD)) == ["done", "done", "done", "now", "todo"]


def test_other_commands_are_not_calibrations():
    other = CommandRow(
        {"command_id": "c9", "type": "mark_event", "issued_at": "2026-11-13T10:00:00Z"}, "completed", [{"status": "completed", "timestamp": "x"}]
    )

    assert statuses(build("running", commands=[other], status=GOOD)) == ["done", "done", "now", "todo", "todo"]


def test_fit_is_now_while_starting_and_everything_else_waits():
    assert statuses(build("starting")) == ["now", "todo", "todo", "todo", "todo"]
    assert statuses(build("starting", commands=ACKED, status=GOOD)) == ["now", "todo", "todo", "todo", "todo"]


def test_fit_is_done_from_running_onward():
    assert statuses(build("running"))[0] == "done"


def test_a_starting_session_matches_the_spine_check_contract_example():
    example = json.loads((EXAMPLES / "ui-session.spine-check.json").read_text(encoding="utf-8"))
    row = SessionRow({"session_id": example["session_id"], "protocol_id": "spine_length_rest_v1"}, "starting", None)

    result = build_session_view(row, [], None, REST)

    assert result == example


# --- mvc depends on the protocol -----------------------------------------------------------------


def stretch_calibration() -> list[CommandRow]:
    return [calibrate(cid(7), "2026-11-13T10:00:00Z", kind="stretch")]


def test_with_emg_in_the_fit_items_only_an_emg_mvc_calibration_completes_mvc():
    assert "emg" in [item["id"] for item in LAT["fit_items"]]
    assert statuses(build("running", commands=stretch_calibration(), status=GOOD, entry=LAT)) == ["done", "done", "now", "todo", "todo"]
    assert statuses(build("running", commands=ACKED, status=GOOD, entry=LAT)) == ["done", "done", "done", "now", "todo"]


def test_without_emg_in_the_fit_items_any_acked_calibration_completes_mvc():
    assert "emg" not in [item["id"] for item in REST["fit_items"]]
    assert statuses(build("running", commands=stretch_calibration(), status=GOOD, entry=REST)) == ["done", "done", "done", "now", "todo"]
    assert statuses(build("running", commands=ACKED, status=GOOD, entry=REST)) == ["done", "done", "done", "now", "todo"]


def test_without_a_catalog_entry_any_acked_calibration_completes_mvc():
    assert statuses(build("running", commands=stretch_calibration(), status=GOOD, entry=None)) == ["done", "done", "done", "now", "todo"]


def test_an_unacked_emg_mvc_does_not_count_even_with_an_acked_stretch_calibration_in_an_emg_protocol():
    commands = [calibrate(cid(1), "2026-11-13T10:00:00Z", None), *stretch_calibration()]

    assert statuses(build("running", commands=commands, status=GOOD, entry=LAT)) == ["done", "done", "now", "todo", "todo"]


def test_calibration_field_is_the_latest_calibrate_whatever_its_type():
    commands = [*ACKED, calibrate(cid(8), "2026-11-13T10:30:00Z", None, kind="stretch")]

    assert build(commands=commands, entry=LAT)["calibration"] == {"command_id": cid(8), "acked": False, "acked_at": None}


def test_a_stalled_stream_after_calibration_does_not_undo_the_check():
    # The calibration could only be sent after the sensors passed; the stall shows in sensor_checks (as in the contract example).
    assert statuses(build("running", commands=ACKED, status=device("ok", ("stalled",)))) == ["done", "done", "done", "now", "todo"]


@pytest.mark.parametrize("state", ["ended", "processing", "processed", "processing_failed"])
def test_a_finished_session_has_every_step_done_whatever_the_device_says(state):
    assert statuses(build(state)) == ["done"] * 5
    assert statuses(build(state, status=device("offline"))) == ["done"] * 5


def test_a_rejected_session_has_only_fit_done_and_no_step_now():
    assert statuses(build("rejected", commands=ACKED, status=GOOD)) == ["done", "todo", "todo", "todo", "todo"]


def test_finished_states_are_the_four_after_running():
    assert set(FINISHED_STATES) == {"ended", "processing", "processed", "processing_failed"}


# --- sensor checks and calibration ---------------------------------------------------------------


def test_no_device_status_means_no_sensor_checks():
    assert build()["sensor_checks"] == []


def test_a_device_without_streams_has_no_sensor_checks():
    assert build(status={"status": "ok"})["sensor_checks"] == []


@pytest.mark.parametrize(("state", "expected"), [("active", "good"), ("stalled", "weak"), ("disabled", "weak")])
def test_a_sensor_is_good_only_when_its_stream_is_active(state, expected):
    result = build(status=device("ok", (state,)))

    assert result["sensor_checks"] == [{"stream": "s0", "status": expected, "state": state}]


def test_calibration_is_null_without_calibrate_commands():
    assert build()["calibration"] is None


def test_calibration_is_the_latest_calibrate_command_by_issue_time():
    commands = [calibrate(cid(2), "2026-11-13T10:05:00Z", None), calibrate(cid(1), "2026-11-13T10:00:00Z", ack_at="2026-11-13T10:00:07Z")]
    result = build(commands=commands)

    assert result["calibration"] == {"command_id": cid(2), "acked": False, "acked_at": None}
    assert validation.errors("app/ui-session", result) == []


def test_calibration_ack_time_is_that_of_the_completed_ack():
    command = calibrate(cid(1), "2026-11-13T10:00:00Z")
    command.acks.insert(0, {"command_id": cid(1), "status": "accepted", "timestamp": "2026-11-13T10:00:01Z"})

    assert build(commands=[command])["calibration"] == {"command_id": cid(1), "acked": True, "acked_at": "2026-11-13T10:00:06.400Z"}


# --- property ------------------------------------------------------------------------------------

calibrations = st.lists(
    st.builds(
        lambda n, ack, kind: calibrate(cid(n), f"2026-11-13T10:{n:02d}:00Z", ack, kind=kind),
        st.integers(0, 59),
        st.sampled_from([None, "accepted", "completed", "failed", "rejected"]),
        st.sampled_from(["emg_mvc", "stretch", "imu"]),
    ),
    max_size=4,
)
devices = st.none() | st.builds(
    lambda status, streams: device(status, tuple(streams)),
    st.sampled_from(["ok", "degraded", "offline"]),
    st.lists(st.sampled_from(["active", "stalled", "disabled"]), max_size=4),
)


@pytest.mark.property
@given(state=st.sampled_from(STATES), commands=calibrations, status=devices, entry=st.sampled_from([LAT, REST, None]))
def test_property_steps_are_ordered_with_at_most_one_now_and_no_done_after_a_todo(state, commands, status, entry):
    result = build(state, commands=commands, status=status, entry=entry)
    flow = statuses(result)

    assert flow.count("now") <= 1
    assert "done" not in flow[flow.index("todo") :] if "todo" in flow else True
    if "now" in flow:
        assert set(flow[flow.index("now") + 1 :]) == {"todo"}
    assert flow[0] == ("now" if state == "starting" else "done")
    if state == "starting":
        assert flow == ["now"] + ["todo"] * 4
    assert validation.errors("app/ui-session", result) == []
    if state in FINISHED_STATES:
        assert flow == ["done"] * 5
    if state == "rejected":
        assert "now" not in flow
    if state == "running" and flow[3] == "now":
        assert flow[:3] == ["done"] * 3
