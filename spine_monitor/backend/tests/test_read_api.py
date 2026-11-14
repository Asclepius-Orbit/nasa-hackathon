"""Read-only API of the app (contracts/app-openapi.yaml), the one the MCP server talks to."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from monitor.db import Store
from monitor.readonly import create_read_app
from monitor.settings import AppSettings
from spine_contracts import openapi_drift, validation

pytestmark = pytest.mark.integration

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
EXAMPLES = CONTRACTS / "examples" / "valid"
NOW = datetime(2027, 2, 9, 10, 0, tzinfo=UTC)
AST = "AST-01"
INFLIGHT_SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a03"
ASSESSMENT = "a55e55e0-0000-4000-8000-0000000000a1"
BASELINE = "b45e11e0-0000-4000-8000-0000000000b1"
DAILY = "5e1f0e90-0000-4000-8000-000000000001"
WINDOW = {"from": "2027-02-01T00:00:00Z", "to": "2027-02-10T00:00:00Z"}


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def inflight_session() -> dict:
    session = example("domain/session.exercise")
    session.update(
        session_id=INFLIGHT_SESSION,
        started_at="2027-02-09T08:30:00Z",
        ended_at="2027-02-09T08:50:00Z",
        mission_context={"mission_day": 87, "phase": "inflight", "gravity_environment": "microgravity"},
    )
    return session


def populate(store: Store) -> None:
    """The examples of the contract, wired together: the amber assessment cites all of them."""
    store.put_profile(example("domain/astronaut-profile"))
    store.put_daily_context(example("domain/daily-context"))
    preflight = example("domain/session.exercise")
    store.insert_session(preflight, "processed")
    store.put_feature_set(example("domain/feature-set.preflight"), phase="preflight", anchor_at=preflight["started_at"])
    store.put_baseline(example("domain/baseline"))
    store.insert_session(inflight_session(), "processed")
    store.put_feature_set(example("domain/feature-set.inflight"), phase="inflight", anchor_at="2027-02-09T08:30:00Z")
    for name in ("self-report.weekly-odi", "self-report.daily"):
        store.put_self_report(example(f"domain/{name}"))
    store.put_assessment(example("domain/assessment.amber"))
    store.put_device_status(example("telemetry/device-status.degraded") | {"timestamp": "2027-02-09T09:57:00Z"})


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "app.db")
    yield store
    store.close()


@pytest.fixture
def client(store):
    app = create_read_app(AppSettings(_env_file=None), store=store, clock=lambda: NOW)
    return TestClient(app)


@pytest.fixture
def full(store, client):
    populate(store)
    return client


def assert_problem(response, status: int, type_: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert validation.errors("app/error", body) == []
    assert body["type"] == type_
    return body


# --- surface -------------------------------------------------------------------------------------


def test_routes_match_the_contract(client):
    contract = yaml.safe_load((CONTRACTS / "app-openapi.yaml").read_text(encoding="utf-8"))

    assert openapi_drift.diff(contract, client.app.openapi()) == []


def test_every_route_is_read_only(client):
    methods = {m for item in client.app.openapi()["paths"].values() for m in item}

    assert methods == {"get"}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_the_read_app_cannot_write(tmp_path):
    Store(tmp_path / "app.db").close()
    app = create_read_app(AppSettings(_env_file=None, db_path=tmp_path / "app.db"))

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        app.state.store.put_profile(example("domain/astronaut-profile"))
    app.state.store.close()


# --- get_current_status --------------------------------------------------------------------------


def test_status_of_an_unknown_astronaut_is_404(client):
    assert_problem(client.get(f"/crew/{AST}/status"), 404, "not_found")


def test_status_with_everything_recorded(full):
    body = full.get(f"/crew/{AST}/status").json()

    assert validation.errors("app/status", body) == []
    assert body["generated_at"] == NOW.isoformat()
    assert body["today"] == {"date": "2027-02-09", "mission_day": 87, "phase": "inflight", "gravity_environment": "microgravity"}
    assert body["latest_assessment"]["assessment_id"] == ASSESSMENT
    assert body["latest_session"]["session_id"] == INFLIGHT_SESSION
    assert body["latest_self_report"]["report_id"] == DAILY
    assert [d["device_id"] for d in body["devices"]] == ["wb-01"]
    assert body["active_baseline"] == {"baseline_id": BASELINE, "version": 1, "created_at": example("domain/baseline")["created_at"]}
    freshness = {(f["what"], f.get("device_id")): f for f in body["freshness"]}
    assert freshness[("assessment", None)] == {"what": "assessment", "last_at": "2027-02-09T09:00:00Z", "age_hours": 1.0, "stale": False}
    assert freshness[("session", None)]["age_hours"] == 1.5
    assert freshness[("self_report", None)]["stale"] is False
    assert freshness[("device", "wb-01")] == {"what": "device", "device_id": "wb-01", "last_at": "2027-02-09T09:57:00Z", "age_hours": 0.05, "stale": False}


def test_missing_data_is_null_and_stale(store, client):
    store.put_profile(example("domain/astronaut-profile"))

    body = client.get(f"/crew/{AST}/status").json()

    assert validation.errors("app/status", body) == []
    assert [body[k] for k in ("today", "latest_assessment", "latest_session", "latest_self_report", "active_baseline")] == [None] * 5
    assert body["devices"] == []
    assert body["freshness"] == [{"what": what, "last_at": None, "age_hours": None, "stale": True} for what in ("assessment", "session", "self_report")]


def test_old_data_is_stale(store, client):
    populate(store)
    later = create_read_app(AppSettings(_env_file=None), store=store, clock=lambda: NOW + timedelta(days=3))

    freshness = TestClient(later).get(f"/crew/{AST}/status").json()["freshness"]

    assert all(f["stale"] for f in freshness)
    assert TestClient(later).get(f"/crew/{AST}/status").json()["today"] is None


def test_stale_thresholds_come_from_the_settings(store):
    populate(store)
    strict = AppSettings(_env_file=None, stale_assessment_hours=0.5, stale_device_hours=0.01)
    app = create_read_app(strict, store=store, clock=lambda: NOW)

    freshness = {(f["what"], f.get("device_id")): f["stale"] for f in TestClient(app).get(f"/crew/{AST}/status").json()["freshness"]}

    assert freshness[("assessment", None)] is True
    assert freshness[("device", "wb-01")] is True
    assert freshness[("self_report", None)] is False


def test_active_baseline_falls_back_to_the_latest(store, client):
    store.put_baseline(example("domain/baseline"))

    body = client.get(f"/crew/{AST}/status").json()

    assert body["active_baseline"]["baseline_id"] == BASELINE


def test_sessions_that_never_ran_are_not_served(store, client):
    session = inflight_session()
    store.insert_session(session, "rejected")

    assert client.get(f"/crew/{AST}/status").json()["latest_session"] is None
    assert_problem(client.get(f"/sessions/{INFLIGHT_SESSION}"), 404, "not_found")
    assert client.get(f"/crew/{AST}/sessions", params=WINDOW).json()["items"] == []


# --- get_history ---------------------------------------------------------------------------------


def test_history_of_a_window(full):
    body = full.get(f"/crew/{AST}/history", params=WINDOW).json()

    assert validation.errors("app/history", body) == []
    assert (body["from"], body["to"], body["limit"], body["truncated"]) == (WINDOW["from"], WINDOW["to"], 100, False)
    # Feature sets are filtered by the start of their session: the pre-flight one (2026-11-13) is out.
    assert [f["session_id"] for f in body["feature_sets"]] == [INFLIGHT_SESSION]
    assert [a["assessment_id"] for a in body["assessments"]] == [ASSESSMENT]


def test_history_keeps_the_most_recent_and_flags_truncation(store, client):
    populate(store)
    for hour in (1, 2, 3):
        later = example("domain/assessment.amber") | {"assessment_id": f"a55e55e0-0000-4000-8000-00000000000{hour}", "as_of": f"2027-02-09T0{hour}:00:00Z"}
        store.put_assessment(later)

    body = client.get(f"/crew/{AST}/history", params=WINDOW | {"limit": 2}).json()

    assert body["truncated"] is True
    assert [a["as_of"] for a in body["assessments"]] == ["2027-02-09T03:00:00Z", "2027-02-09T09:00:00Z"]
    assert len(body["feature_sets"]) == 1


def test_window_bounds_are_inclusive(full):
    body = full.get(f"/crew/{AST}/history", params={"from": "2027-02-09T09:00:00Z", "to": "2027-02-09T09:00:00Z"}).json()

    assert [a["assessment_id"] for a in body["assessments"]] == [ASSESSMENT]
    assert body["feature_sets"] == []


def test_window_with_another_offset(full):
    body = full.get(f"/crew/{AST}/history", params={"from": "2027-02-09T10:00:00+01:00", "to": "2027-02-09T10:00:00+01:00"}).json()

    assert [a["assessment_id"] for a in body["assessments"]] == [ASSESSMENT]
    assert body["from"] == "2027-02-09T10:00:00+01:00"


@pytest.mark.parametrize(
    ("params", "type_"),
    [
        ({"from": "2027-02-10T00:00:00Z", "to": "2027-02-01T00:00:00Z"}, "invalid_range"),
        ({"to": "2027-02-10T00:00:00Z"}, "invalid_request"),
        ({"from": "2027-02-01T00:00:00Z"}, "invalid_request"),
        ({"from": "2027-02-01T00:00:00", "to": "2027-02-10T00:00:00Z"}, "invalid_request"),
        ({"from": "yesterday", "to": "2027-02-10T00:00:00Z"}, "invalid_request"),
        (WINDOW | {"limit": 0}, "invalid_request"),
        (WINDOW | {"limit": 501}, "invalid_request"),
        (WINDOW | {"limit": "many"}, "invalid_request"),
    ],
)
def test_bad_windows_are_refused(full, params, type_):
    body = assert_problem(full.get(f"/crew/{AST}/history", params=params), 422, type_)

    assert body["errors"]


def test_history_of_an_unknown_astronaut_is_404(client):
    assert_problem(client.get("/crew/AST-99/history", params=WINDOW), 404, "not_found")


def test_malformed_ids_are_invalid_requests(client):
    assert_problem(client.get("/crew/Jane/status"), 422, "invalid_request")
    assert_problem(client.get("/assessments/not-a-uuid"), 422, "invalid_request")


# --- resource lists ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "schema", "key", "expected"),
    [
        ("assessments", "app/assessment-list", "assessment_id", [ASSESSMENT]),
        ("sessions", "app/session-list", "session_id", [INFLIGHT_SESSION]),
        ("self-reports", "app/self-report-list", "report_id", ["5e1f0e90-0000-4000-8000-000000000002", DAILY]),
    ],
)
def test_lists_of_a_window(full, path, schema, key, expected):
    body = full.get(f"/crew/{AST}/{path}", params=WINDOW).json()

    assert validation.errors(schema, body) == []
    assert [item[key] for item in body["items"]] == expected
    assert (body["astronaut_id"], body["truncated"], body["limit"]) == (AST, False, 100)


def test_list_limit_keeps_the_most_recent(full):
    body = full.get(f"/crew/{AST}/self-reports", params=WINDOW | {"limit": 1}).json()

    assert [r["report_id"] for r in body["items"]] == [DAILY]
    assert body["truncated"] is True


@pytest.mark.parametrize("path", ["assessments", "sessions", "self-reports"])
def test_lists_validate_their_window(full, path):
    assert_problem(full.get(f"/crew/{AST}/{path}", params={"from": WINDOW["to"], "to": WINDOW["from"]}), 422, "invalid_range")
    assert_problem(full.get(f"/crew/AST-99/{path}", params=WINDOW), 404, "not_found")


# --- single resources ----------------------------------------------------------------------------


def test_single_resources(full):
    assert full.get(f"/assessments/{ASSESSMENT}").json() == example("domain/assessment.amber")
    assert full.get(f"/sessions/{INFLIGHT_SESSION}").json() == inflight_session()
    assert full.get(f"/sessions/{INFLIGHT_SESSION}/feature-set").json() == example("domain/feature-set.inflight")
    assert full.get(f"/self-reports/{DAILY}").json() == example("domain/self-report.daily")
    assert full.get(f"/crew/{AST}/baselines/{BASELINE}").json() == example("domain/baseline")


@pytest.mark.parametrize(
    "path",
    [
        "/assessments/00000000-0000-4000-8000-000000000000",
        "/sessions/00000000-0000-4000-8000-000000000000",
        "/sessions/00000000-0000-4000-8000-000000000000/feature-set",
        "/self-reports/00000000-0000-4000-8000-000000000000",
        f"/crew/{AST}/baselines/00000000-0000-4000-8000-000000000000",
        f"/crew/AST-02/baselines/{BASELINE}",
        "/assessments/00000000-0000-4000-8000-000000000000/explanation",
    ],
)
def test_missing_resources_are_404(full, path):
    assert_problem(full.get(path), 404, "not_found")


def test_session_without_features_is_404(store, client):
    store.insert_session(inflight_session(), "running")

    assert client.get(f"/sessions/{INFLIGHT_SESSION}").status_code == 200
    assert_problem(client.get(f"/sessions/{INFLIGHT_SESSION}/feature-set"), 404, "not_found")


# --- explain_assessment --------------------------------------------------------------------------


def test_explanation_resolves_what_the_assessment_cites(full):
    body = full.get(f"/assessments/{ASSESSMENT}/explanation").json()

    assert validation.errors("app/explanation", body) == []
    assert body["assessment"] == example("domain/assessment.amber")
    assert body["baseline"] == {"baseline_id": BASELINE, "version": 1}
    # Only the entries of the drivers; symptoms.pain_nrs_7d_mean has no baseline entry.
    assert [e["feature_id"] for e in body["baseline_entries"]] == ["stretch.full_spine.peak_elongation_mm"]
    assert [f["session_id"] for f in body["feature_sets"]] == [INFLIGHT_SESSION]
    assert [r["report_id"] for r in body["self_reports"]] == [DAILY]
    assert body["unresolved"] == []


def test_explanation_lists_what_is_gone(store, client):
    store.put_assessment(example("domain/assessment.amber"))

    body = client.get(f"/assessments/{ASSESSMENT}/explanation").json()

    assert validation.errors("app/explanation", body) == []
    assert (body["baseline"], body["baseline_entries"], body["feature_sets"], body["self_reports"]) == (None, [], [], [])
    assert body["unresolved"] == [
        {"kind": "baseline", "id": BASELINE},
        {"kind": "session", "id": INFLIGHT_SESSION},
        {"kind": "self_report", "id": DAILY},
    ]


def test_explanation_without_based_on(store, client):
    assessment = example("domain/assessment.amber")
    del assessment["based_on"]
    store.put_assessment(assessment)

    body = client.get(f"/assessments/{ASSESSMENT}/explanation").json()

    assert validation.errors("app/explanation", body) == []
    assert (body["baseline"], body["unresolved"]) == (None, [])


def test_explanation_ignores_a_baseline_of_another_astronaut(store, client):
    store.put_assessment(example("domain/assessment.amber"))
    store.put_baseline(example("domain/baseline") | {"astronaut_id": "AST-02"})

    body = client.get(f"/assessments/{ASSESSMENT}/explanation").json()

    assert body["baseline"] is None
    assert {"kind": "baseline", "id": BASELINE} in body["unresolved"]


# --- assess-base ---------------------------------------------------------------------------------


def test_assess_base_is_the_request_the_assessment_would_post(store, client):
    populate(store)

    response = client.get(f"/crew/{AST}/assess-base")

    assert response.status_code == 200, response.text
    body = response.json()
    assert validation.errors("engine/assess-request", body) == []
    assert body["as_of"] == NOW.isoformat()
    assert body["baseline"] == example("domain/baseline")
    assert body["profile"] == example("domain/astronaut-profile")
    assert body["mission_day"] == 87
    assert INFLIGHT_SESSION in {fs["session_id"] for fs in body["feature_sets"]}


def test_assess_base_of_an_unknown_astronaut_is_404(client):
    assert_problem(client.get(f"/crew/{AST}/assess-base"), 404, "not_found")


def test_assess_base_without_a_profile_is_404_naming_the_reason(store, client):
    store.put_baseline(example("domain/baseline"))

    assert assert_problem(client.get(f"/crew/{AST}/assess-base"), 404, "not_found")["detail"] == "no_profile"


def test_assess_base_without_a_baseline_is_404_naming_the_reason(store, client):
    store.put_profile(example("domain/astronaut-profile"))

    assert assert_problem(client.get(f"/crew/{AST}/assess-base"), 404, "not_found")["detail"] == "no_baseline"


def test_assess_base_without_feature_sets_in_the_window_is_404_naming_the_reason(store, client):
    populate(store)
    later = create_read_app(AppSettings(_env_file=None), store=store, clock=lambda: NOW + timedelta(days=30))

    assert assert_problem(TestClient(later).get(f"/crew/{AST}/assess-base"), 404, "not_found")["detail"] == "no_feature_sets"


def test_assess_base_window_comes_from_the_settings(store):
    populate(store)
    narrow = AppSettings(_env_file=None, assess_window_days=1)
    app = create_read_app(narrow, store=store, clock=lambda: NOW + timedelta(days=3))

    assert assert_problem(TestClient(app).get(f"/crew/{AST}/assess-base"), 404, "not_found")["detail"] == "no_feature_sets"
