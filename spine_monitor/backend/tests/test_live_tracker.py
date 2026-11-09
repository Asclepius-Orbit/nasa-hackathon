"""Live repetition state per session: tracker, hub, ingestion hook and rebuild from the store (real simulator chunks)."""

import json
from collections.abc import Iterable
from typing import Any

import pytest
from monitor.ingest import Ingest
from monitor.live.reps import RepDetector, RepDetectorParams
from monitor.live.tracker import RepHub, RepMessage, RepTracker, Subscription, has_live_reps, rep_total
from monitor.settings import AppSettings
from spine_contracts import topics, validation

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

STATE_SCHEMA = "app/ui-rep-state"
LAT = "lat_pulldown_standing_v1"
SPINE_CHECK = "spine_length_rest_v1"
OTHER_SESSION = "aaaaaaaa-bbbb-4ccc-8ddd-000000000009"


def drain(subscription: Subscription) -> list[RepMessage]:
    messages = []
    while not subscription.queue.empty():
        messages.append(subscription.queue.get_nowait())
    return messages


def only_view(subscription: Subscription) -> dict[str, Any]:
    messages = drain(subscription)
    assert len(messages) == 1
    assert messages[0].view is not None
    return messages[0].view


def chunks_until(chunks: list[dict[str, Any]], *, rep: int, phase: str) -> int:
    """Index of the first chunk after which the detector is in that rep and phase."""
    detector = RepDetector(RepDetectorParams.default())
    for index, chunk in enumerate(chunks):
        detector.feed(chunk)
        snapshot = detector.snapshot()
        if (snapshot["rep"], snapshot["phase"]) == (rep, phase):
            return index
    raise AssertionError(f"rep {rep} {phase} never reached")


def counter(view: dict[str, Any]) -> dict[str, Any]:
    return {key: view[key] for key in ("rep", "total", "last")}


async def running_session(harness, **overrides: Any) -> str:
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session(**overrides)
    return session_id


def restarted_hub(harness) -> RepHub:
    """What a backend restart leaves: nothing in memory, the same database."""
    return RepHub(harness.store, harness.clock, no_signal_s=2.0)


def store_and_tell(harness, hub: RepHub, session_id: str, chunks: Iterable[dict[str, Any]]) -> None:
    """The order of the ingestion: stored first, then told to the hub."""
    row = harness.store.session(session_id)
    for chunk in chunks:
        assert harness.store.add_chunk(chunk)
        hub.on_chunk(row, chunk)


# --- settings and totals ---------------------------------------------------------------------------


def test_live_settings_defaults_and_env_prefix(monkeypatch) -> None:
    defaults = AppSettings(_env_file=None)
    assert (defaults.live_no_signal_s, defaults.live_heartbeat_s) == (2, 15)
    assert defaults.live_tick_s <= 0.5
    monkeypatch.setenv("SPINE_APP_LIVE_NO_SIGNAL_S", "3.5")
    assert AppSettings(_env_file=None).live_no_signal_s == 3.5


@pytest.mark.parametrize(
    ("session", "expected"),
    [
        ({"planned_reps": 6, "protocol_id": LAT}, 6),
        ({"protocol_id": LAT}, 10),
        ({"protocol_id": SPINE_CHECK}, None),
        ({"protocol_id": "not_in_the_catalog"}, None),
    ],
)
def test_total_is_the_planned_reps_then_the_catalog_then_null(session: dict[str, Any], expected: int | None) -> None:
    assert rep_total(session) == expected


# --- the tracker -----------------------------------------------------------------------------------


async def test_tracker_view_has_no_signal_until_a_chunk_and_from_2_s_after_the_last_one(harness) -> None:
    chunks = harness.imu_chunks(OTHER_SESSION)
    tracker = RepTracker(OTHER_SESSION, 10, clock=harness.clock, no_signal_s=2.0)

    fresh = tracker.view()
    assert validation.errors(STATE_SCHEMA, fresh) == []
    assert fresh == {
        "schema_version": "1.0.0",
        "session_id": OTHER_SESSION,
        "rep": 0,
        "total": 10,
        "phase": None,
        "progress_pct": None,
        "last": None,
        "signal": "no_signal",
        "as_of": harness.clock().isoformat(),
    }

    # The reception time is the local clock: an old `received_at` in the payload does not matter.
    tracker.on_chunk({**chunks[0], "received_at": "2020-01-01T00:00:00Z"})
    harness.clock.advance(seconds=1.999)
    alive = tracker.view()
    assert (alive["signal"], alive["phase"]) == ("ok", "resting")
    assert alive["as_of"] == harness.clock().isoformat()

    harness.clock.advance(seconds=0.001)
    assert (tracker.view()["signal"], tracker.view()["phase"]) == ("no_signal", None)


async def test_no_signal_keeps_rep_total_and_last_and_clears_phase_and_progress(harness) -> None:
    chunks = harness.imu_chunks(OTHER_SESSION)
    tracker = RepTracker(OTHER_SESSION, 10, clock=harness.clock, no_signal_s=2.0)
    for chunk in chunks[: chunks_until(chunks, rep=2, phase="pulling") + 5]:
        tracker.on_chunk(chunk)
    live = tracker.view()
    assert live["phase"] == "pulling" and live["progress_pct"] is not None and live["last"]["rep"] == 1

    harness.clock.advance(seconds=2)
    silent = tracker.view()
    assert validation.errors(STATE_SCHEMA, silent) == []
    assert (silent["signal"], silent["phase"], silent["progress_pct"]) == ("no_signal", None, None)
    assert counter(silent) == counter(live)


# --- the hub and the ingestion hook ----------------------------------------------------------------


async def test_redelivered_chunk_and_chunk_of_another_stream_do_not_reach_the_detector(harness) -> None:
    session_id = await running_session(harness)
    chunks = harness.imu_chunks(session_id)
    at_pull = chunks_until(chunks, rep=1, phase="pulling")
    await harness.send_stream_chunks(chunks[: at_pull - 20])
    subscription = harness.runtime.hub.subscribe(session_id)
    assert only_view(subscription)["signal"] == "ok"

    # The same movement on another IMU of the session: the detector does not filter by stream_id.
    await harness.send_stream_chunks({**chunk, "stream_id": "imu_t12"} for chunk in chunks[at_pull - 20 : at_pull + 5])
    await harness.send_stream_chunks(chunks[at_pull - 25 : at_pull - 20])  # QoS 1 redelivery of stored chunks

    assert harness.runtime.ingest.counts["rejected"] == 0
    assert harness.runtime.ingest.counts["duplicate"] == 5
    assert drain(subscription) == []
    assert harness.runtime.hub.tracker_view(session_id)["rep"] == 0


async def test_a_failing_tracker_never_breaks_ingestion_nor_loses_the_chunk(harness, caplog) -> None:
    session_id = await running_session(harness)
    chunk = harness.imu_chunks(session_id)[0]

    class Broken:
        def on_chunk(self, *_: object) -> None:
            raise RuntimeError("boom")

    ingest = Ingest(harness.store, harness.runtime.control, live=Broken())
    ingest.handle(topics.stream(chunk["astronaut_id"], chunk["device_id"], "imu_l3"), json.dumps(chunk).encode())

    assert ingest.counts == {"accepted": 1, "duplicate": 0, "rejected": 0, "dropped": 0}
    assert harness.store.rejected_count() == 0
    assert len(harness.store.session_chunks(chunk["astronaut_id"], session_id)) == 1
    assert "live repetition" in caplog.text


async def test_a_session_without_live_reps_is_never_tracked(harness) -> None:
    plan = harness.plan(protocol_id=SPINE_CHECK)
    del plan["planned_reps"]
    await harness.setup_astronaut()
    response = await harness.http.post(f"/ui/crew/{harness.astronaut_id}/sessions", json=plan)
    assert response.status_code == 202, response.text
    session_id = response.json()["session"]["session_id"]
    assert not has_live_reps(harness.store.session(session_id).payload)

    await harness.send_stream_chunks(harness.imu_chunks(session_id)[:3])

    assert harness.runtime.ingest.counts["accepted"] >= 3
    assert harness.runtime.hub.tracked_sessions() == set()


async def test_a_slow_subscriber_never_blocks_and_keeps_the_latest_state(harness) -> None:
    session_id = await running_session(harness)
    hub = harness.runtime.hub
    subscription = hub.subscribe(session_id)
    chunks = harness.imu_chunks(session_id)
    for chunk in chunks[:40]:  # every pair is a change: the signal back, then lost; nobody reads the queue
        await harness.send_stream_chunks([chunk])
        harness.clock.advance(seconds=2)
        hub.refresh(session_id)
    await harness.send_stream_chunks(chunks[40:41])

    assert subscription.queue.full()
    last = drain(subscription)[-1]
    assert last.view is not None
    assert (last.view["signal"], last.view["as_of"]) == ("ok", harness.clock().isoformat())


async def test_the_end_of_a_session_reaches_a_full_queue_and_leaves_no_tracker(harness) -> None:
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    hub = harness.runtime.hub
    subscription = hub.subscribe(session_id)
    gone = hub.subscribe(session_id)
    chunks = harness.imu_chunks(session_id)
    for chunk in chunks[:30]:
        await harness.send_stream_chunks([chunk])
        harness.clock.advance(seconds=2)
        hub.refresh(session_id)
    hub.unsubscribe(gone)
    assert subscription.queue.full()

    await harness.stop_session(session_id, start)

    assert hub.tracked_sessions() == set()
    assert drain(subscription)[-1].event == "end"
    assert drain(gone)[-1].event != "end"
    # A chunk that arrives late is stored but does not bring the tracker back.
    await harness.send_stream_chunks(chunks[30:31])
    assert hub.tracked_sessions() == set()
    assert len(harness.store.session_chunks(chunks[0]["astronaut_id"], session_id)) == 31


# --- rebuild from the store ------------------------------------------------------------------------


async def test_rebuild_after_a_restart_has_the_same_counter_as_live_and_no_signal_until_the_next_chunk(harness) -> None:
    session_id = await running_session(harness)
    chunks = harness.imu_chunks(session_id)
    middle = chunks_until(chunks, rep=4, phase="letting_up") + 3
    await harness.send_stream_chunks(chunks[:middle])
    live = harness.runtime.hub.tracker_view(session_id)
    assert (live["signal"], live["rep"]) == ("ok", 4) and live["progress_pct"] is not None

    restarted = restarted_hub(harness)
    subscription = restarted.subscribe(session_id)
    rebuilt = only_view(subscription)
    assert validation.errors(STATE_SCHEMA, rebuilt) == []
    assert counter(rebuilt) == counter(live)
    assert (rebuilt["signal"], rebuilt["phase"], rebuilt["progress_pct"]) == ("no_signal", None, None)

    await harness.send_stream_chunks(chunks[middle : middle + 1])
    restarted.on_chunk(harness.store.session(session_id), chunks[middle])
    back = only_view(subscription)
    assert back == harness.runtime.hub.tracker_view(session_id)
    assert (back["signal"], back["phase"]) == ("ok", "letting_up")


async def test_the_first_chunk_after_a_restart_is_not_counted_twice(harness) -> None:
    """The chunk is already stored when the hub hears of it, so the rebuild it triggers has it: it is not fed again."""
    session_id = await running_session(harness)
    chunks = harness.imu_chunks(session_id)
    upto = chunks_until(chunks, rep=2, phase="pulling") + 3
    store_and_tell(harness, restarted_hub(harness), session_id, chunks[:upto])

    restarted = restarted_hub(harness)
    store_and_tell(harness, restarted, session_id, chunks[upto : upto + 1])

    straight = RepDetector(RepDetectorParams.default())
    for chunk in chunks[: upto + 1]:
        straight.feed(chunk)
    view = restarted.tracker_view(session_id)
    assert view["signal"] == "ok"
    assert (view["rep"], view["phase"], view["progress_pct"], view["last"]) == tuple(straight.snapshot().values())


async def test_rebuild_follows_the_real_order_of_the_boots_whatever_their_random_ids(harness) -> None:
    """boot_id is random: here the later boot has the lower id. In id order the current boot would be retired and the counter would freeze."""
    session_id = await running_session(harness)
    chunks = harness.imu_chunks(session_id)
    reboot_at = chunks_until(chunks, rep=5, phase="resting") + 5
    first = [{**chunk, "boot_id": 4_000_000_000} for chunk in chunks[:reboot_at]]
    second = [{**chunk, "boot_id": 7, "seq": seq} for seq, chunk in enumerate(chunks[reboot_at:])]
    resume = chunks_until(second, rep=2, phase="pulling") + 3
    live = restarted_hub(harness)
    store_and_tell(harness, live, session_id, [*first, *second[:resume]])
    key = chunks[0]["astronaut_id"], chunks[0]["device_id"], session_id, "imu_l3"
    assert [(c["boot_id"], c["seq"]) for c in harness.store.stream_chunks(*key)] == [(c["boot_id"], c["seq"]) for c in [*first, *second[:resume]]]

    restarted = restarted_hub(harness)
    assert counter(only_view(restarted.subscribe(session_id))) == counter(live.tracker_view(session_id))

    store_and_tell(harness, live, session_id, second[resume:])
    row = harness.store.session(session_id)
    for chunk in second[resume:]:
        restarted.on_chunk(row, chunk)
    assert restarted.tracker_view(session_id) == live.tracker_view(session_id)
    assert restarted.tracker_view(session_id)["rep"] == 10


async def test_stream_chunks_only_returns_that_stream_of_that_session_of_that_device(harness) -> None:
    session_id = await running_session(harness)
    chunk = harness.imu_chunks(session_id)[0]
    for stored in (chunk, {**chunk, "stream_id": "imu_t12"}, {**chunk, "session_id": OTHER_SESSION, "seq": 1}, {**chunk, "device_id": "wb-02", "seq": 2}):
        assert harness.store.add_chunk(stored)
    assert list(harness.store.stream_chunks(chunk["astronaut_id"], chunk["device_id"], session_id, "imu_l3")) == [chunk]


async def test_a_stored_chunk_the_detector_cannot_read_does_not_stop_the_rebuild(harness, caplog) -> None:
    session_id = await running_session(harness)
    chunks = harness.imu_chunks(session_id)
    for chunk in ({**chunks[0], "data": {**chunks[0]["data"], "gyro_x": []}}, chunks[1], chunks[2]):
        assert harness.store.add_chunk(chunk)
    assert only_view(restarted_hub(harness).subscribe(session_id))["rep"] == 0
    assert "rebuild" in caplog.text
