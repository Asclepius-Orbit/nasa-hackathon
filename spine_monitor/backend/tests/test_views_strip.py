"""build_strip: facts of the always-visible strip; every unknown is an explicit null."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from monitor.views.strip import build_strip
from spine_contracts import validation

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
AST = "AST-01"
PROFILE = {"launch_date": "2026-11-15", "landing_date": "2027-05-15"}


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def status(device_id: str = "wb-01", *, timestamp: str = "2026-11-13T09:59:00Z", state: str = "ok", **extra: Any) -> dict[str, Any]:
    base = {"schema_version": "1.0.0", "astronaut_id": AST, "device_id": device_id, "timestamp": timestamp, "status": state, "source": "device"}
    return base | extra


def strip(statuses: list[dict[str, Any]], profile: dict[str, Any] | None = PROFILE, daily: Any = None, odi: Any = None) -> dict[str, Any]:
    view = build_strip(AST, profile, statuses, daily, odi, NOW, 7)
    assert validation.errors("app/ui-strip", view) == []
    return view


def test_nothing_known_is_all_null_and_everything_due() -> None:
    view = strip([], profile=None)
    assert view == {
        "schema_version": "1.0.0",
        "astronaut_id": AST,
        "mission": {"day": None, "start": None, "phase": None},
        "harness": None,
        "ground_link": None,
        "last_contact_min_ago": None,
        "reports_due": {"daily": True, "weekly_odi": True},
    }


def test_mission_comes_from_the_profile_and_now() -> None:
    assert strip([])["mission"] == {"day": -2, "start": "2026-11-15", "phase": "preflight"}


def test_harness_ok_with_battery() -> None:
    assert strip([status(battery_pct=71.5)])["harness"] == {"device_id": "wb-01", "battery_pct": 71.5, "weak_streams": [], "degraded": False}


def test_missing_battery_is_null_not_zero() -> None:
    assert strip([status()])["harness"]["battery_pct"] is None


def test_offline_harness_is_null() -> None:
    assert strip([status(state="offline")])["harness"] is None


def test_degraded_with_weak_streams_only_stalled_and_disabled() -> None:
    streams = [
        {"stream_id": "stretch_lumbar", "stream_kind": "stretch", "state": "stalled"},
        {"stream_id": "imu_trunk", "stream_kind": "imu", "state": "active"},
        {"stream_id": "emg_ld_r", "stream_kind": "emg", "state": "disabled"},
    ]
    harness = strip([status(state="degraded", streams=streams)])["harness"]
    assert harness["degraded"] is True
    assert harness["weak_streams"] == [{"stream": "stretch_lumbar", "state": "stalled"}, {"stream": "emg_ld_r", "state": "disabled"}]


def test_ok_status_is_not_degraded() -> None:
    assert strip([status(state="ok")])["harness"]["degraded"] is False


def test_most_recent_device_of_the_astronaut_wins() -> None:
    old = status("wb-01", timestamp="2026-11-13T08:00:00Z", battery_pct=10)
    new = status("wb-02", timestamp="2026-11-13T09:00:00Z", battery_pct=90)
    assert strip([old, new])["harness"]["device_id"] == "wb-02"
    assert strip([new, old])["harness"]["device_id"] == "wb-02"


def test_the_most_recent_one_decides_even_if_offline() -> None:
    older_ok = status("wb-01", timestamp="2026-11-13T08:00:00Z")
    newer_offline = status("wb-02", timestamp="2026-11-13T09:00:00Z", state="offline")
    assert strip([older_ok, newer_offline])["harness"] is None


def test_timestamp_tie_is_broken_by_device_id() -> None:
    a, b = status("wb-01"), status("wb-02")
    assert strip([a, b])["harness"]["device_id"] == "wb-02"
    assert strip([b, a])["harness"]["device_id"] == "wb-02"


def test_other_astronauts_devices_are_ignored() -> None:
    other = status("wb-09") | {"astronaut_id": "AST-02"}
    assert strip([other])["harness"] is None


def test_reports_due_uses_the_latest_reports() -> None:
    daily = {"timestamp": (NOW - timedelta(hours=1)).isoformat()}
    odi = {"timestamp": (NOW - timedelta(days=1)).isoformat()}
    assert strip([], daily=daily, odi=odi)["reports_due"] == {"daily": False, "weekly_odi": False}
    assert strip([], daily=None, odi=odi)["reports_due"] == {"daily": True, "weekly_odi": False}


def test_ground_link_and_last_contact_are_always_null() -> None:
    view = strip([status(battery_pct=50)])
    assert view["ground_link"] is None
    assert view["last_contact_min_ago"] is None


def test_does_not_mutate_inputs() -> None:
    statuses = [status(state="degraded", streams=[{"stream_id": "stretch_lumbar", "stream_kind": "stretch", "state": "stalled"}])]
    before = deepcopy(statuses)
    strip(statuses)
    assert statuses == before


@pytest.mark.contract
@pytest.mark.parametrize("name", ["ui-strip", "ui-strip.unpaired"])
def test_has_the_keys_of_the_contract_examples(name: str) -> None:
    sample = example(f"app/{name}")
    view = strip([])
    assert set(view) == set(sample)
    assert set(view["mission"]) == set(sample["mission"])
