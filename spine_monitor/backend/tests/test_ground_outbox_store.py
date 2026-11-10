"""Ground outbox in the Store: one queued revision per day is replaced in place, a sent one is never rewritten."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from monitor.db import GroundKey, Store

NOW = datetime(2027, 2, 9, 9, 0, tzinfo=UTC)
D1, D2 = date(2027, 2, 8), date(2027, 2, 9)


def at(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)


def key(day: date = D1, revision: int = 1, astronaut: str = "AST-01") -> GroundKey:
    return GroundKey(astronaut, day, revision)


@pytest.fixture
def store() -> Iterator[Store]:
    opened = Store(":memory:")
    yield opened
    opened.close()


def put(store: Store, day: date = D1, tag: str = "a", astronaut: str = "AST-01", now: datetime = NOW, mission_day: int | None = 5) -> int:
    return store.upsert_ground_summary(astronaut, day, mission_day, lambda revision: {"revision": revision, "tag": tag}, now)


def sent(store: Store, k: GroundKey, minutes: int, tag: str = "a") -> bool:
    return store.mark_ground_sent(k, at(minutes), {"revision": k.revision, "tag": tag})


def payload(store: Store, k: GroundKey) -> dict[str, Any]:
    (item,) = [i for i in store.ground_outbox_items(k.astronaut_id, 50) if i.key == k]
    return item.payload


def test_the_first_summary_of_a_day_is_queued_as_revision_1_with_the_revision_given_to_build(store: Store):
    assert put(store, tag="first") == 1

    (item,) = store.ground_outbox_items("AST-01", 10)
    assert (item.key, item.mission_day, item.state, item.payload) == (key(), 5, "queued", {"revision": 1, "tag": "first"})
    assert (item.enqueued_at, item.received_at, item.attempts) == ("2027-02-09T09:00:00.000000Z", None, 0)
    assert (item.last_error, item.next_attempt_at) == (None, None)


def test_a_queued_revision_is_replaced_in_place_keeping_revision_and_attempts(store: Store):
    put(store, tag="old")
    store.mark_ground_failed(key(), "NO_LINK", at(30))

    assert put(store, tag="new", now=at(10), mission_day=6) == 1

    (item,) = store.ground_outbox_items("AST-01", 10)
    assert (item.key.revision, item.payload, item.mission_day) == (1, {"revision": 1, "tag": "new"}, 6)
    assert (item.enqueued_at, item.attempts, item.last_error) == ("2027-02-09T09:10:00.000000Z", 1, "NO_LINK")


def test_a_sent_revision_is_kept_and_the_next_summary_is_the_next_revision(store: Store):
    put(store, tag="old")
    sent(store, key(), 5, "old")

    assert put(store, tag="new") == 2

    assert payload(store, key(revision=1)) == {"revision": 1, "tag": "old"}
    assert payload(store, key(revision=2)) == {"revision": 2, "tag": "new"}
    assert put(store, tag="newer") == 2  # revision 2 is queued: replaced, not a third one


def test_days_and_astronauts_have_their_own_revisions(store: Store):
    assert [put(store, D1), put(store, D2), put(store, D1, astronaut="AST-02")] == [1, 1, 1]


def test_a_build_that_fails_leaves_the_outbox_untouched_and_usable(store: Store):
    put(store, tag="old")

    def boom(revision: int) -> dict[str, Any]:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        store.upsert_ground_summary("AST-01", D1, 5, boom, at(10))

    assert payload(store, key()) == {"revision": 1, "tag": "old"}
    assert put(store, tag="after", now=at(20)) == 1


class FailingCommit:
    """The connection of the store, except that its next COMMIT fails (disk full, I/O error) without having committed."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def execute(self, sql: str, *params: Any) -> sqlite3.Cursor:
        if sql == "COMMIT":
            raise sqlite3.OperationalError("disk I/O error")
        return self._db.execute(sql, *params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)


def test_a_commit_that_fails_rolls_back_and_leaves_the_connection_usable(store: Store):
    put(store, tag="old")
    real = store._db
    store._db = FailingCommit(real)  # pyright: ignore[reportAttributeAccessIssue] -- test double standing in for the connection

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        put(store, tag="lost", now=at(10))

    store._db = real
    assert not real.in_transaction
    assert payload(store, key()) == {"revision": 1, "tag": "old"}
    assert put(store, tag="after", now=at(20)) == 1
    assert payload(store, key()) == {"revision": 1, "tag": "after"}


def test_next_item_is_the_oldest_queued_by_date_then_revision(store: Store):
    put(store, D2)
    put(store, D1)
    sent(store, key(D1), 1)
    put(store, D1, tag="b")  # D1 revision 2

    first = store.next_ground_item(at(10))
    assert first is not None
    assert first.key == key(D1, 2)

    sent(store, first.key, 11, "b")
    second = store.next_ground_item(at(12))
    assert second is not None
    assert second.key == key(D2)

    sent(store, second.key, 13)
    assert store.next_ground_item(at(14)) is None


def test_an_entry_in_backoff_blocks_the_newer_ones_of_the_same_astronaut_but_not_of_another(store: Store):
    put(store, D1)
    put(store, D2)
    put(store, D2, astronaut="AST-02")
    store.mark_ground_failed(key(D1), "NO_LINK", at(30))

    other = store.next_ground_item(at(10))
    assert other is not None
    assert other.key == key(D2, astronaut="AST-02")
    sent(store, other.key, 11)
    assert store.next_ground_item(at(29)) is None

    due = store.next_ground_item(at(30))  # exactly at next_attempt_at it is due
    assert due is not None
    assert due.key == key(D1)


def test_marking_sent_stores_the_receipt_and_is_idempotent(store: Store):
    put(store)

    assert sent(store, key(), 5) is True
    assert sent(store, key(), 99) is True

    (item,) = store.ground_outbox_items("AST-01", 10)
    assert (item.state, item.received_at) == ("sent", "2027-02-09T09:05:00.000000Z")


def test_an_entry_updated_while_it_was_being_sent_stays_queued_with_the_same_key(store: Store):
    put(store, tag="old")
    put(store, tag="new", now=at(1))

    assert sent(store, key(), 5, "old") is False

    (item,) = store.ground_outbox_items("AST-01", 10)
    assert (item.key, item.state, item.received_at, item.payload["tag"]) == (key(), "queued", None, "new")
    assert store.last_ground_contact() is None


def test_marking_sent_an_unknown_key_changes_nothing(store: Store):
    assert store.mark_ground_sent(key(), at(5), {}) is False
    assert store.ground_outbox_items("AST-01", 10) == []


def test_a_failure_counts_the_attempt_and_schedules_the_next_without_changing_the_state(store: Store):
    put(store)

    store.mark_ground_failed(key(), "NO_LINK", at(5))
    store.mark_ground_failed(key(), "TIMEOUT", at(15))

    (item,) = store.ground_outbox_items("AST-01", 10)
    assert (item.state, item.attempts, item.last_error, item.next_attempt_at) == ("queued", 2, "TIMEOUT", "2027-02-09T09:15:00.000000Z")


def test_a_failure_after_the_receipt_does_not_touch_a_sent_entry(store: Store):
    put(store)
    sent(store, key(), 5)

    store.mark_ground_failed(key(), "LATE", at(15))

    (item,) = store.ground_outbox_items("AST-01", 10)
    assert (item.state, item.attempts, item.last_error, item.next_attempt_at) == ("sent", 0, None, None)


def test_items_are_most_recent_first_limited_and_per_astronaut(store: Store):
    put(store, D1)
    sent(store, key(D1), 1)
    put(store, D1, tag="b")
    put(store, D2)
    put(store, D2, astronaut="AST-02")

    assert [i.key for i in store.ground_outbox_items("AST-01", 10)] == [key(D2), key(D1, 2), key(D1, 1)]
    assert [i.key for i in store.ground_outbox_items("AST-01", 2)] == [key(D2), key(D1, 2)]


def test_latest_summary_is_the_payload_of_the_most_recent_entry_queued_or_sent(store: Store):
    assert store.latest_ground_summary("AST-01") is None

    put(store, D2, tag="today")
    put(store, D1, tag="yesterday")
    assert store.latest_ground_summary("AST-01") == {"revision": 1, "tag": "today"}

    sent(store, key(D2), 1, "today")
    assert store.latest_ground_summary("AST-01") == {"revision": 1, "tag": "today"}
    assert store.latest_ground_summary("AST-02") is None


def test_last_contact_is_the_latest_receipt_per_astronaut_or_global(store: Store):
    assert (store.last_ground_contact("AST-01"), store.last_ground_contact()) == (None, None)
    put(store, D1)
    put(store, D2)
    put(store, D1, astronaut="AST-02")
    sent(store, key(D1), 5)
    sent(store, key(D2), 3)  # an older receipt does not win
    sent(store, key(D1, astronaut="AST-02"), 9)

    assert store.last_ground_contact("AST-01") == "2027-02-09T09:05:00.000000Z"
    assert store.last_ground_contact("AST-02") == "2027-02-09T09:09:00.000000Z"
    assert store.last_ground_contact() == "2027-02-09T09:09:00.000000Z"


def test_everything_survives_closing_and_reopening_the_store(tmp_path: Path):
    path = tmp_path / "app.db"
    first = Store(path)
    put(first, D1, tag="sent")
    sent(first, key(D1), 5, "sent")
    put(first, D2, tag="queued")
    first.mark_ground_failed(key(D2), "NO_LINK", at(30))
    first.close()

    reopened = Store(path)

    items = reopened.ground_outbox_items("AST-01", 10)
    assert [(i.key, i.state, i.attempts, i.last_error, i.next_attempt_at) for i in items] == [
        (key(D2), "queued", 1, "NO_LINK", "2027-02-09T09:30:00.000000Z"),
        (key(D1), "sent", 0, None, None),
    ]
    assert reopened.last_ground_contact() == "2027-02-09T09:05:00.000000Z"
    assert reopened.next_ground_item(at(29)) is None
    reopened.close()


def test_a_database_written_before_the_outbox_existed_acquires_the_table(tmp_path: Path):
    path = tmp_path / "app.db"
    Store(path).close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE ground_outbox")

    store = Store(path)

    assert put(store) == 1
    store.close()
