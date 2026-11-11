"""The ground outbox wired into the app: an assessment queues the summary of its day, the sender delivers it, the UI routes show it."""

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from monitor.api import OUTBOX_VIEW_LIMIT
from monitor.db import GroundKey, Store, utc_key
from monitor.ground.enqueue import enqueue_ground_summary
from monitor.ground.transport import FakeGroundTransport, GroundReceipt
from monitor.main import create_app
from monitor.settings import AppSettings
from spine_contracts import validation

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

# The harness clock starts here: the link is up for 10 minutes, then down for 80.
ANCHOR = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
DAY = date(2026, 11, 13)
WINDOWS = {"ground_window_anchor": ANCHOR, "ground_window_period_min": 90, "ground_window_up_min": 10}
DAILY = {"kind": "daily", "pain_nrs": 2, "stiffness_nrs": 3, "red_flags": []}
MARKER = "MARKER-pain-in-the-lower-back"


@pytest.fixture
def harness_settings() -> dict[str, Any]:
    # No test may depend on the tick: a wake has to do. The retry delay is fixed by its limits.
    return WINDOWS | {"ground_tick_s": 3600, "ground_retry_min_s": 3, "ground_retry_max_s": 3}


async def assessed(harness) -> None:
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.send_chunks(session_id)
    await harness.stop_session(session_id, start)


async def report(harness) -> dict[str, Any]:
    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json=DAILY)
    assert response.status_code == 201, response.text
    return response.json()


async def view(harness, name: str, schema: str) -> dict[str, Any]:
    response = await harness.http.get(f"/ui/crew/{harness.astronaut_id}/{name}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert validation.errors(schema, body) == []
    return body


def outbox(harness) -> list[Any]:
    return harness.store.ground_outbox_items(harness.astronaut_id, 50)


# --- queueing --------------------------------------------------------------------------------------------


async def test_the_end_of_a_session_queues_its_day_and_nothing_is_sent_by_itself(harness):
    await assessed(harness)

    (item,) = outbox(harness)

    assert (item.key, item.state, item.mission_day) == (GroundKey(harness.astronaut_id, DAY, 1), "queued", None)
    assert item.payload["assessment_id"] == harness.store.latest_assessment(harness.astronaut_id)["assessment_id"]
    assert item.payload["as_of"] == harness.clock().isoformat().replace("+00:00", "Z")
    assert validation.errors("app/ground-summary", item.payload) == []
    (session,) = harness.store.session_rows(harness.astronaut_id)
    expected = {"session_id": session.payload["session_id"], "state": "processed", "protocol_id": session.payload["protocol_id"], "pain_stop": None}
    assert item.payload["sessions"] == [expected]
    assert harness.runtime.ground.deliveries == []


async def test_a_self_report_replaces_the_queued_summary_of_the_day(harness):
    await assessed(harness)

    body = await report(harness)

    (item,) = outbox(harness)
    assert item.key.revision == 1
    assert item.payload["assessment_id"] == body["assessment"]["assessment_id"]


async def test_the_summary_carries_the_red_flags_reported_that_day(harness):
    await assessed(harness)

    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json={"kind": "red_flag", "red_flags": ["leg_weakness"]})

    assert response.status_code == 201, response.text
    (item,) = outbox(harness)
    assert item.payload["red_flags"] == ["leg_weakness"]
    assert item.key.revision == 1, "one assessment, one update: no extra revision from the report"


async def test_a_red_flag_without_an_assessment_queues_the_day_as_nodata_with_the_red_flag(harness):
    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/self-reports", json={"kind": "red_flag", "red_flags": ["leg_weakness"]})

    assert response.status_code == 201, response.text
    assert response.json()["assessment"] is None
    (item,) = outbox(harness)
    assert (item.key, item.state) == (GroundKey(harness.astronaut_id, DAY, 1), "queued")
    assert (item.payload["level"], item.payload["assessment_id"], item.payload["red_flags"]) == ("nodata", None, ["leg_weakness"])
    assert validation.errors("app/ground-summary", item.payload) == []


async def test_a_day_without_an_assessment_queues_nothing(harness):
    body = await report(harness)

    assert body["assessment"] is None
    assert outbox(harness) == []


async def test_the_mission_day_comes_from_the_launch_date_of_the_profile(harness):
    await assessed(harness)
    profile = harness.example("domain/astronaut-profile") | {"launch_date": "2026-11-03"}
    assert (await harness.http.put(f"/ui/crew/{harness.astronaut_id}/profile", json=profile)).status_code == 200

    await report(harness)

    (item,) = outbox(harness)
    assert (item.mission_day, item.payload["mission_day"]) == (11, 11)


async def test_the_day_is_the_utc_day_of_as_of_and_the_summary_keeps_its_latest_assessment(harness):
    await assessed(harness)
    analysis = harness.runtime.analysis

    # 22:30 UTC of the 13th, written as 00:30 of the 14th in another zone.
    latest = await analysis.assess(harness.astronaut_id, datetime.fromisoformat("2026-11-14T00:30:00+02:00"))
    # A later call about an earlier moment of the same day.
    await analysis.assess(harness.astronaut_id, datetime.fromisoformat("2026-11-13T11:00:00+00:00"))

    (item,) = outbox(harness)
    assert item.key.day == DAY
    assert latest.assessment is not None
    assert item.payload["assessment_id"] == latest.assessment["assessment_id"]


async def test_an_as_of_written_in_another_zone_belongs_to_its_utc_day(harness):
    assessment = harness.example("domain/assessment.amber") | {"as_of": "2026-11-14T00:30:00+02:00"}
    harness.store.put_assessment(assessment)

    enqueue_ground_summary(harness.store, assessment, harness.clock())

    (item,) = outbox(harness)
    assert item.key.day == DAY


def stored_assessment(harness, minute: int, spoil: Callable[[dict[str, Any]], Any] = lambda _: None) -> dict[str, Any]:
    assessment = harness.example("domain/assessment.amber")
    assessment |= {"assessment_id": f"a55e55e0-0000-4000-8000-{minute:012d}", "as_of": (ANCHOR + timedelta(minutes=minute)).isoformat()}
    spoil(assessment)
    harness.store.put_assessment(assessment)
    return assessment


@pytest.mark.parametrize(
    ("spoil", "pointer", "value"),
    [
        (lambda a: a.update(level="purple"), "/level", "purple"),
        (lambda a: a.update(confidence=float("nan")), "/confidence", "nan"),
        (lambda a: a["drivers"][0].update(deviation_z=float("nan")), "/drivers/0/deviation_z", "nan"),
        (lambda a: a["drivers"][0].update(deviation_z=float("inf")), "/drivers/0/deviation_z", "inf"),
    ],
    ids=["not_valid", "confidence_nan", "deviation_z_nan", "deviation_z_inf"],
)
async def test_a_summary_not_valid_or_not_finite_writes_nothing_and_holds_nothing_back(harness, caplog, spoil, pointer, value):
    with caplog.at_level(logging.WARNING):
        assert enqueue_ground_summary(harness.store, stored_assessment(harness, 1, spoil), harness.clock()) is None
    assert outbox(harness) == []
    assert pointer in caplog.text
    assert value not in caplog.text

    valid = stored_assessment(harness, 2)
    assert enqueue_ground_summary(harness.store, valid, harness.clock()) == 1
    (queued,) = outbox(harness)

    assert enqueue_ground_summary(harness.store, stored_assessment(harness, 3, spoil), harness.clock()) is None
    assert outbox(harness) == [queued], "it replaced the valid entry that was queued"

    await harness.runtime.sender.pump()

    assert [d.payload["assessment_id"] for d in harness.runtime.ground.deliveries] == [valid["assessment_id"]]


async def test_a_failure_while_queueing_never_breaks_the_analysis_and_logs_no_text(harness, monkeypatch, caplog):
    await assessed(harness)

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError(MARKER)

    monkeypatch.setattr(harness.store, "upsert_ground_summary", boom)

    with caplog.at_level(logging.DEBUG):
        body = await report(harness)

    assert harness.store.latest_assessment(harness.astronaut_id)["assessment_id"] == body["assessment"]["assessment_id"]
    assert "RuntimeError" in caplog.text
    assert MARKER not in caplog.text


# --- delivery --------------------------------------------------------------------------------------------


async def test_queueing_wakes_the_sender_without_waiting_for_the_tick(harness):
    await assessed(harness)
    await harness.runtime.sender.pump()
    task = asyncio.create_task(harness.runtime.sender.run())
    try:
        for _ in range(5):
            await asyncio.sleep(0)  # parked in its wait, for an hour

        body = await report(harness)

        for _ in range(100):
            await asyncio.sleep(0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    item = outbox(harness)[0]
    assert (item.key.revision, item.state) == (2, "sent")
    assert harness.runtime.ground.received[item.key]["assessment_id"] == body["assessment"]["assessment_id"]


async def test_the_profile_never_reaches_the_ground_or_the_routes(harness):
    markers = {
        "date_of_birth": "1971-03-09",
        "gender_identity": "MARKER-GENDER",
        "notes": "MARKER-NOTES",
        "body_mass_kg": "83.25",
        "height_standing_cm": "187.5",
        "last_mission_end_date": "2019-07-21",
        "launch_date": "2026-11-03",
    }
    profile = harness.example("domain/astronaut-profile")
    profile |= {"date_of_birth": markers["date_of_birth"], "gender_identity": markers["gender_identity"], "launch_date": markers["launch_date"]}
    profile["spine_history"]["notes"] = markers["notes"]
    profile["anthropometry"] |= {"body_mass_kg": 83.25, "height_standing_cm": 187.5}
    profile["space_history"]["last_mission_end_date"] = markers["last_mission_end_date"]
    await assessed(harness)
    assert (await harness.http.put(f"/ui/crew/{harness.astronaut_id}/profile", json=profile)).status_code == 200
    await report(harness)

    await harness.runtime.sender.pump()

    delivered = json.dumps([d.payload for d in harness.runtime.ground.deliveries])
    assert harness.runtime.ground.deliveries != []
    shown = json.dumps([await view(harness, "houston", "app/ui-houston"), await view(harness, "outbox", "app/ui-outbox")])
    assert [m for m in markers.values() if m in delivered or m in shown] == []


# --- routes ----------------------------------------------------------------------------------------------


async def test_the_routes_without_data_answer_200_with_nulls(harness):
    houston = await view(harness, "houston", "app/ui-houston")
    box = await view(harness, "outbox", "app/ui-outbox")

    assert houston == {
        "schema_version": "1.0.0",
        "astronaut_id": harness.astronaut_id,
        "summary": None,
        "ground_link": {"state": "up"},
        "last_contact_at": None,
        "transport": "simulated",
        "content_validated": False,
    }
    assert box == {"schema_version": "1.0.0", "astronaut_id": harness.astronaut_id, "ground_link": {"state": "up"}, "last_contact_at": None, "items": []}


async def test_the_routes_show_what_was_queued_before_it_is_sent(harness):
    await assessed(harness)
    harness.clock.advance(minutes=30)

    houston = await view(harness, "houston", "app/ui-houston")
    box = await view(harness, "outbox", "app/ui-outbox")

    (item,) = outbox(harness)
    assert houston["summary"] == item.payload
    assert houston["ground_link"] == {"state": "down", "back_in_min": 60}
    assert houston["last_contact_at"] is None
    assert box["ground_link"] == {"state": "down", "back_in_min": 60}
    assert box["items"] == [
        {"date": "2026-11-13", "mission_day": None, "revision": 1, "state": "queued", "enqueued_at": item.enqueued_at, "received_at": None, "attempts": 0}
    ]


async def test_the_routes_show_the_confirmed_delivery_and_the_strip_its_age(harness):
    await assessed(harness)
    harness.runtime.ground.fail_next("no_carrier", times=2)
    for _ in range(2):
        await harness.runtime.sender.pump()
        assert outbox(harness)[0].next_attempt_at == utc_key(harness.clock() + timedelta(seconds=3))  # the settings, not the defaults
        harness.clock.advance(seconds=3)
    await harness.runtime.sender.pump()
    received = utc_key(harness.clock())
    harness.clock.advance(minutes=7, seconds=58)

    houston = await view(harness, "houston", "app/ui-houston")
    box = await view(harness, "outbox", "app/ui-outbox")
    strip = await view(harness, "views/strip", "app/ui-strip")

    assert houston["last_contact_at"] == received
    assert box["last_contact_at"] == received
    assert (box["items"][0]["state"], box["items"][0]["received_at"], box["items"][0]["attempts"]) == ("sent", received, 2)
    assert (strip["ground_link"], strip["last_contact_min_ago"]) == ({"state": "up"}, 7)


async def test_the_outbox_route_shows_the_most_recent_entries_up_to_its_limit(harness):
    days = [DAY - timedelta(days=n) for n in range(OUTBOX_VIEW_LIMIT + 1)]
    for day in days:
        harness.store.upsert_ground_summary(harness.astronaut_id, day, None, lambda revision: {"revision": revision}, harness.clock())

    box = await view(harness, "outbox", "app/ui-outbox")

    assert [i["date"] for i in box["items"]] == [d.isoformat() for d in days[:OUTBOX_VIEW_LIMIT]]


async def test_a_stored_summary_that_does_not_conform_is_never_served(harness):
    harness.store.upsert_ground_summary(harness.astronaut_id, DAY, None, lambda _: {"notes": MARKER}, harness.clock())

    response = await harness.http.get(f"/ui/crew/{harness.astronaut_id}/houston")

    assert response.status_code == 500
    assert MARKER not in response.text


# --- lifespan --------------------------------------------------------------------------------------------


def _queued_db(tmp_path: Path, harness) -> tuple[Path, GroundKey]:
    path = tmp_path / "lifespan.db"
    store = Store(path)
    day_summary = harness.example("app/ground-summary")
    key = GroundKey(harness.astronaut_id, date.fromisoformat(day_summary["date"]), 1)
    store.upsert_ground_summary(key.astronaut_id, key.day, day_summary["mission_day"], lambda revision: day_summary | {"revision": revision}, harness.clock())
    store.close()
    return path, key


async def test_the_lifespan_runs_the_sender_and_what_it_sent_survives(harness, tmp_path):
    path, key = _queued_db(tmp_path, harness)
    delivered = asyncio.Event()

    class Ground(FakeGroundTransport):
        async def send(self, key: GroundKey, payload: dict[str, Any]) -> GroundReceipt:
            receipt = await super().send(key, payload)
            delivered.set()
            return receipt

    settings = AppSettings(_env_file=None, db_path=path, ground_tick_s=3600, **WINDOWS)
    app = create_app(settings, mqtt_client_factory=harness.mqtt.factory, clock=harness.clock, ground_transport=Ground(harness.clock))

    async with app.router.lifespan_context(app):
        await asyncio.wait_for(delivered.wait(), 5)
        await asyncio.sleep(0)

    after = Store(path)
    try:
        (item,) = after.ground_outbox_items(key.astronaut_id, 50)
    finally:
        after.close()
    assert item.state == "sent"


async def test_the_lifespan_cancels_a_send_in_flight_before_closing_the_store(harness, tmp_path):
    path, key = _queued_db(tmp_path, harness)
    entered = asyncio.Event()
    contact_seen_on_cancel: list[str | None] = []

    class Hanging(FakeGroundTransport):
        async def send(self, key: GroundKey, payload: dict[str, Any]) -> GroundReceipt:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                contact_seen_on_cancel.append(app.state.runtime.store.last_ground_contact(key.astronaut_id))  # raises if it is closed
            raise AssertionError("never gets here")

    settings = AppSettings(_env_file=None, db_path=path, ground_tick_s=3600, **WINDOWS)
    app = create_app(settings, mqtt_client_factory=harness.mqtt.factory, clock=harness.clock, ground_transport=Hanging(harness.clock))

    async with app.router.lifespan_context(app):
        await asyncio.wait_for(entered.wait(), 5)

    assert contact_seen_on_cancel == [None]
    after = Store(path)
    try:
        (item,) = after.ground_outbox_items(key.astronaut_id, 50)
    finally:
        after.close()
    assert (item.state, item.attempts, item.last_error) == ("queued", 0, None)
