"""The /assess request builder shared by the assessment and the read-only API."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from monitor.assess_request import build_assess_request
from monitor.db import Store
from spine_contracts import validation

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
AS_OF = datetime(2027, 2, 9, 10, 0, tzinfo=UTC)
AST = "AST-01"
WINDOW = timedelta(days=14)


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "app.db")
    yield store
    store.close()


def seed_all(store: Store) -> None:
    store.put_profile(example("domain/astronaut-profile"))
    store.put_daily_context(example("domain/daily-context"))
    store.put_baseline(example("domain/baseline"))
    store.put_feature_set(example("domain/feature-set.inflight"), phase="inflight", anchor_at="2027-02-09T08:30:00Z")
    store.put_self_report(example("domain/self-report.daily"))


def test_skips_without_a_profile(store):
    built = build_assess_request(store, AST, AS_OF, WINDOW)

    assert (built.request, built.skipped) == (None, "no_profile")


def test_skips_without_a_baseline(store):
    store.put_profile(example("domain/astronaut-profile"))

    assert build_assess_request(store, AST, AS_OF, WINDOW).skipped == "no_baseline"


def test_skips_without_feature_sets_in_the_window(store):
    seed_all(store)

    assert build_assess_request(store, AST, AS_OF + timedelta(days=15), WINDOW).skipped == "no_feature_sets"


def test_builds_a_request_valid_for_the_engine(store):
    seed_all(store)

    built = build_assess_request(store, AST, AS_OF, WINDOW)

    assert built.skipped is None
    request = built.request
    assert request is not None
    assert validation.errors("engine/assess-request", request) == []
    assert request["as_of"] == AS_OF.isoformat()
    assert request["baseline"] == example("domain/baseline")
    assert [fs["session_id"] for fs in request["feature_sets"]] == [example("domain/feature-set.inflight")["session_id"]]
    assert request["mission_day"] == 87
