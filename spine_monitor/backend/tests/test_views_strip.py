"""build_strip: facts of the always-visible strip; every unknown is an explicit null."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from monitor.views.strip import build_strip, minutes_since
from spine_contracts import validation

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
AST = "AST-01"
STALE_DEVICE_HOURS = 0.25  # 15 minutes
PROFILE = {"launch_date": "2026-11-15", "landing_date": "2027-05-15"}


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def status(device_id: str = "wb-01", *, timestamp: str = "2026-11-13T09:59:00Z", state: str = "ok", **extra: Any) -> dict[str, Any]:
    base = {"schema_version": "1.0.0", "astronaut_id": AST, "device_id": device_id, "timestamp": timestamp, "status": state, "source": "device"}
    return base | extra


def strip(
    statuses: list[dict[str, Any]],
    profile: dict[str, Any] | None = PROFILE,
    daily: Any = None,
    odi: Any = None,
    ground_link: Any = None,
    last_contact_min_ago: int | None = None,
) -> dict[str, Any]:
    view = build_strip(AST, profile, statuses, daily, odi, NOW, 7, STALE_DEVICE_HOURS, ground_link, last_contact_min_ago)
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
    assert strip([status(battery_pct=71.5)])["harness"] == {
        "device_id": "wb-01",
        "battery_pct": 71.5,
        "weak_streams": [],
        "degraded": False,
        "stale": False,
        "last_seen_at": "2026-11-13T09:59:00Z",
    }


def test_a_fresh_device_is_not_stale_and_carries_its_timestamp() -> None:
    harness = strip([status(timestamp="2026-11-13T09:45:00Z")])["harness"]  # exactly at the threshold

    assert (harness["stale"], harness["last_seen_at"]) == (False, "2026-11-13T09:45:00Z")


def test_an_old_device_still_appears_marked_stale_with_its_last_known_state() -> None:
    streams = [{"stream_id": "stretch_spine", "stream_kind": "stretch", "state": "stalled"}]
    harness = strip([status(timestamp="2026-11-13T09:44:59Z", state="degraded", battery_pct=40, streams=streams)])["harness"]

    assert harness is not None
    assert (harness["stale"], harness["last_seen_at"]) == (True, "2026-11-13T09:44:59Z")
    assert (harness["device_id"], harness["battery_pct"], harness["degraded"]) == ("wb-01", 40, True)
    assert harness["weak_streams"] == [{"stream": "stretch_spine", "state": "stalled"}]


def test_a_stale_offline_device_is_still_null() -> None:
    assert strip([status(timestamp="2026-11-12T09:00:00Z", state="offline")])["harness"] is None


def test_the_staleness_threshold_is_the_one_given() -> None:
    old = status(timestamp="2026-11-13T07:00:00Z")  # 3 hours old

    assert build_strip(AST, PROFILE, [old], None, None, NOW, 7, 1.0, None, None)["harness"]["stale"] is True
    assert build_strip(AST, PROFILE, [old], None, None, NOW, 7, 4.0, None, None)["harness"]["stale"] is False


def test_missing_battery_is_null_not_zero() -> None:
    assert strip([status()])["harness"]["battery_pct"] is None


def test_offline_harness_is_null() -> None:
    assert strip([status(state="offline")])["harness"] is None


def test_degraded_with_weak_streams_only_stalled_and_disabled() -> None:
    streams = [
        {"stream_id": "stretch_spine", "stream_kind": "stretch", "state": "stalled"},
        {"stream_id": "imu_trunk", "stream_kind": "imu", "state": "active"},
        {"stream_id": "emg_ld_r", "stream_kind": "emg", "state": "disabled"},
    ]
    harness = strip([status(state="degraded", streams=streams)])["harness"]
    assert harness["degraded"] is True
    assert harness["weak_streams"] == [{"stream": "stretch_spine", "state": "stalled"}, {"stream": "emg_ld_r", "state": "disabled"}]


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


@pytest.mark.parametrize(
    ("ground_link", "minutes"),
    [(None, None), ({"state": "up"}, 0), ({"state": "down", "back_in_min": 12}, 41)],
)
def test_ground_link_and_last_contact_are_shown_as_given(ground_link: Any, minutes: int | None) -> None:
    view = strip([status(battery_pct=50)], ground_link=ground_link, last_contact_min_ago=minutes)
    assert view["ground_link"] == ground_link
    assert view["last_contact_min_ago"] == minutes


@pytest.mark.parametrize(
    ("elapsed", "minutes"),
    [(timedelta(seconds=59), 0), (timedelta(minutes=1), 1), (timedelta(minutes=7, seconds=59), 7), (timedelta(seconds=-90), 0)],
    ids=["under_a_minute", "exactly_one", "rounded_down", "ground_clock_ahead"],
)
def test_minutes_since_rounds_down_and_is_never_negative(elapsed: timedelta, minutes: int) -> None:
    assert minutes_since((NOW - elapsed).isoformat(), NOW) == minutes


def test_minutes_since_nothing_is_null() -> None:
    assert minutes_since(None, NOW) is None


def test_does_not_mutate_inputs() -> None:
    statuses = [status(state="degraded", streams=[{"stream_id": "stretch_spine", "stream_kind": "stretch", "state": "stalled"}])]
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


def test_a_device_id_describes_that_device_not_the_latest_one() -> None:
    paired = status("wb-01", timestamp="2026-11-13T09:30:00Z", battery_pct=80)
    latest = status("sim-01", timestamp="2026-11-13T09:59:00Z", battery_pct=10)
    view = build_strip(AST, PROFILE, [paired, latest], None, None, NOW, 7, STALE_DEVICE_HOURS, None, None, device_id="wb-01")

    assert view["harness"] is not None
    assert (view["harness"]["device_id"], view["harness"]["battery_pct"], view["harness"]["last_seen_at"]) == ("wb-01", 80, "2026-11-13T09:30:00Z")
    assert view["harness"]["stale"] is True  # 30 minutes old against a 15-minute threshold: the same rule as without device_id


def test_a_device_id_that_never_reported_is_null_even_if_others_did() -> None:
    view = build_strip(AST, PROFILE, [status("sim-01")], None, None, NOW, 7, STALE_DEVICE_HOURS, None, None, device_id="wb-01")

    assert view["harness"] is None


def test_a_device_id_of_another_astronaut_is_not_used() -> None:
    other = status("wb-01") | {"astronaut_id": "AST-02"}

    assert build_strip(AST, PROFILE, [other], None, None, NOW, 7, STALE_DEVICE_HOURS, None, None, device_id="wb-01")["harness"] is None


def test_an_offline_paired_device_is_null() -> None:
    view = build_strip(AST, PROFILE, [status("wb-01", state="offline")], None, None, NOW, 7, STALE_DEVICE_HOURS, None, None, device_id="wb-01")

    assert view["harness"] is None
