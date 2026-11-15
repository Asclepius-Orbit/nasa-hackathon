"""Demo seeder (`python -m monitor.demo_seed`): two astronauts with ~60 days of history, built with the real engine.

The engine is the real one (`create_default_app`, its default settings, no `risk_saturation` override) reached over ASGI,
and the clock is the injected `END`: nothing here reads the wall clock. The seeder tunes the synthetic trajectories, never the
engine, so the levels asserted below are what the engine itself answered.
"""

import asyncio
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from monitor import demo_seed
from monitor.db import Store
from monitor.engine import EngineClient
from monitor.ground.link import link_state
from monitor.main import create_app
from monitor.readonly import create_read_app
from monitor.settings import AppSettings
from spine_contracts import BaselineEntry, validation
from spine_engine.api import create_default_app
from spine_engine.deviation.scoring import RobustZScorer

pytestmark = pytest.mark.integration

END = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
TODAY = END.date()
AST_01, AST_02 = "AST-01", "AST-02"
UI_TOKEN = "seed-ui-token"
UI_HEADERS = {"Authorization": f"Bearer {UI_TOKEN}"}
LEVELS = ("green", "amber", "red")


@pytest.fixture
def short_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """20 days with the launch 8 days ago: the same code on a third of the data, for the tests about skipping and the command line."""
    monkeypatch.setattr(demo_seed, "HISTORY_DAYS", 20)
    monkeypatch.setattr(demo_seed, "LAUNCH_DAYS_AGO", 8)


def run_seed(db_path: Path, end: datetime = END) -> dict[str, str]:
    async def go() -> dict[str, str]:
        store = Store(db_path)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_default_app()), base_url="http://engine") as http:
                return await demo_seed.seed_demo(store, EngineClient(http), end)
        finally:
            store.close()

    return asyncio.run(go())


@dataclass(frozen=True)
class Seeded:
    path: Path
    result: dict[str, str]

    def store(self) -> Store:
        return Store(self.path, read_only=True)


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Seeded:
    path = tmp_path_factory.mktemp("demo-seed") / "demo.db"
    return Seeded(path, run_seed(path))


@pytest.fixture
def store(seeded: Seeded) -> Iterator[Store]:
    store = seeded.store()
    yield store
    store.close()


def levels_by_day(store: Store, astronaut_id: str) -> dict[date, str]:
    start, end = datetime(2026, 1, 1, tzinfo=UTC), END
    return {datetime.fromisoformat(a["as_of"]).date(): a["level"] for a in store.assessments_between(astronaut_id, start, end)}


def last_days(levels: dict[date, str], n: int) -> list[str]:
    """The levels of the n days up to YESTERDAY: at noon today's own assessment (21:00) is not made yet."""
    return [levels.get(TODAY - timedelta(days=k), "nodata") for k in range(n, 0, -1)]


# --- result and idempotency ------------------------------------------------------------------


def test_both_astronauts_are_seeded(seeded: Seeded) -> None:
    assert seeded.result == {AST_01: "seeded", AST_02: "seeded"}


def test_crew_is_registered(store: Store) -> None:
    assert [(c["astronaut_id"], c["registered"], c["has_profile"], c["has_baseline"]) for c in store.crew_overview()] == [
        (AST_01, True, True, True),
        (AST_02, True, True, True),
    ]


def dump_everything(path: Path) -> dict[str, list[Any]]:
    db = sqlite3.connect(path)
    try:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: sorted((tuple(row) for row in db.execute(f"SELECT * FROM {t}")), key=repr) for t in tables}
    finally:
        db.close()


def test_second_run_changes_nothing(tmp_path: Path, seeded: Seeded) -> None:
    path = tmp_path / "again.db"
    shutil.copy2(seeded.path, path)
    before = dump_everything(path)

    again = run_seed(path)

    assert set(again) == {AST_01, AST_02}
    assert all(v.startswith("skipped") for v in again.values())
    assert dump_everything(path) == before


def test_an_astronaut_with_sessions_is_left_alone(tmp_path: Path, seeded: Seeded, short_history: None, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "partial.db"
    store, source = Store(path), seeded.store()
    session = source.session_rows(AST_01)[0].payload
    store.insert_session(session, "processed")
    store.close()
    source.close()

    with caplog.at_level("INFO", logger="monitor.demo_seed"):
        result = run_seed(path)

    assert result[AST_01].startswith("skipped") and result[AST_02] == "seeded"
    assert any(AST_01 in r.getMessage() and "skipp" in r.getMessage().lower() for r in caplog.records)
    check = Store(path, read_only=True)
    assert check.profile(AST_01) is None and len(check.session_rows(AST_01)) == 1
    check.close()


def test_an_astronaut_with_a_complete_profile_is_left_alone(tmp_path: Path, seeded: Seeded, short_history: None) -> None:
    path = tmp_path / "profile.db"
    source = seeded.store()
    store = Store(path)
    store.put_profile(source.profile(AST_02) or {})
    store.close()
    source.close()

    result = run_seed(path)

    assert result[AST_02].startswith("skipped") and result[AST_01] == "seeded"


def test_an_incomplete_profile_is_replaced(tmp_path: Path, short_history: None) -> None:
    path = tmp_path / "thin.db"
    store = Store(path)
    store.put_profile(
        {
            "schema_version": "1.0.0", "astronaut_id": AST_01, "updated_at": "2026-11-01T00:00:00Z", "date_of_birth": "1988-04-12",
            "sex_at_birth": "female", "anthropometry": {"height_standing_cm": 172, "body_mass_kg": 68},
        }
    )  # fmt: skip
    store.close()

    assert run_seed(path)[AST_01] == "seeded"


def test_two_runs_on_two_files_give_the_same_bytes(tmp_path: Path, seeded: Seeded) -> None:
    other = tmp_path / "twin.db"
    run_seed(other)

    assert dump_everything(other) == dump_everything(seeded.path)


# --- everything validates --------------------------------------------------------------------


@pytest.mark.parametrize("astronaut_id", [AST_01, AST_02])
def test_every_stored_entity_validates(store: Store, astronaut_id: str) -> None:
    export = store.export_astronaut(astronaut_id)
    problems: list[str] = []

    def check(schema: str, payload: Any) -> None:
        problems.extend(f"{schema}: {e}" for e in validation.errors(schema, payload))

    check("domain/astronaut-profile", export["profile"])
    for item in export["daily_contexts"]:
        check("domain/daily-context", item)
    for item in export["sessions"]:
        check("domain/session", item["session"])
    for item in export["feature_sets"] + export["measured_windows"]:
        check("domain/feature-set", item)
    for item in export["baselines"]:
        check("domain/baseline", item)
    for item in export["assessments"]:
        check("domain/assessment", item)
    for item in export["self_reports"]:
        check("domain/self-report", item)
    for item in export["medical_contacts"]:
        check("domain/medical-contact", item)
    for item in store.ground_outbox_items(astronaut_id, 500):
        check("app/ground-summary", item.payload)

    assert problems == []
    assert export["assessments"] and export["baselines"] and export["feature_sets"]


# --- the history -----------------------------------------------------------------------------


@pytest.mark.parametrize("astronaut_id", [AST_01, AST_02])
def test_sixty_days_of_history_ending_now(store: Store, astronaut_id: str) -> None:
    export = store.export_astronaut(astronaut_id)
    first = TODAY - timedelta(days=59)
    contexts = [c["date"] for c in export["daily_contexts"]]
    assert contexts == [(first + timedelta(days=i)).isoformat() for i in range(60)]

    started = [datetime.fromisoformat(s["session"]["started_at"]) for s in export["sessions"]]
    assert max(started) <= END and min(started).date() == first
    per_day = {d: sum(1 for s in started if s.date() == d) for d in {s.date() for s in started}}
    assert set(per_day) == {first + timedelta(days=i) for i in range(60)}
    assert set(per_day.values()) <= {1, 2, 3} and 3 in per_day.values()
    assert {s["state"] for s in export["sessions"]} == {"processed"}

    reports = export["self_reports"]
    daily = [datetime.fromisoformat(r["timestamp"]).date() for r in reports if r["kind"] == "daily"]
    assert daily == [first + timedelta(days=i) for i in range(59)]  # every day but today
    assert TODAY not in daily
    odi = [r for r in reports if r["kind"] == "weekly_odi"]
    assert 7 <= len(odi) <= 9
    assert all(
        b - a == timedelta(days=7)
        for a, b in zip([datetime.fromisoformat(r["timestamp"]) for r in odi], [datetime.fromisoformat(r["timestamp"]) for r in odi][1:], strict=False)
    )
    assert all(datetime.fromisoformat(r["timestamp"]) <= END for r in reports)


@pytest.mark.parametrize("astronaut_id", [AST_01, AST_02])
def test_profile_is_complete_and_today_is_mid_flight(store: Store, astronaut_id: str) -> None:
    profile = store.profile(astronaut_id)
    assert profile is not None
    assert {"date_of_birth", "sex_at_birth", "anthropometry", "launch_date", "landing_date", "spine_history", "space_history", "fitness"} <= set(profile)
    assert {"height_standing_cm", "height_sitting_cm", "body_mass_kg"} <= set(profile["anthropometry"])
    launch, landing = date.fromisoformat(profile["launch_date"]), date.fromisoformat(profile["landing_date"])
    assert launch < TODAY < landing
    assert (TODAY - launch).days >= 30 and (landing - TODAY).days >= 60


def test_the_two_profiles_are_different_people(store: Store) -> None:
    one, two = store.profile(AST_01), store.profile(AST_02)
    assert one is not None and two is not None
    assert (one["sex_at_birth"], one["date_of_birth"]) == ("female", "1988-04-12")
    assert (two["sex_at_birth"], two["date_of_birth"]) == ("male", "1981-09-30")


@pytest.mark.parametrize("astronaut_id", [AST_01, AST_02])
def test_baseline_is_built_from_the_preflight_sessions(store: Store, astronaut_id: str) -> None:
    baselines = store.export_astronaut(astronaut_id)["baselines"]
    profile = store.profile(astronaut_id)
    assert profile is not None
    launch = date.fromisoformat(profile["launch_date"])
    versions = [b["version"] for b in baselines]
    assert versions == list(range(1, len(versions) + 1)) and len(versions) >= 3
    preflight = {r.payload["session_id"] for r in store.session_rows(astronaut_id, phase="preflight")}
    assert set(baselines[-1]["built_from_session_ids"]) == preflight
    assert all(datetime.fromisoformat(b["created_at"]).date() < launch for b in baselines)
    assert len({b["baseline_id"] for b in baselines}) == len(baselines)


@pytest.mark.parametrize("astronaut_id", [AST_01, AST_02])
def test_ids_are_deterministic_uuid5(store: Store, astronaut_id: str) -> None:
    export = store.export_astronaut(astronaut_id)
    ids = [a["assessment_id"] for a in export["assessments"]] + [b["baseline_id"] for b in export["baselines"]]
    assert all(uuid.UUID(i).version == 5 for i in ids)


# --- the arcs, as the real engine answered ---------------------------------------------------


def test_ast_01_is_green_and_stable_then_amber_with_drivers(store: Store) -> None:
    levels = levels_by_day(store, AST_01)
    assert set(levels.values()) <= {"green", "amber"}

    early = last_days(levels, 60)[:45]
    assert set(early) == {"green"} or set(early) <= {"green", "nodata"}
    assert "green" in early
    tail = last_days(levels, 7)
    assert tail[-1] == "amber" and tail.count("amber") >= 3
    assert "amber" not in last_days(levels, 14)[:5]

    latest = store.latest_assessment(AST_01)
    assert latest is not None and latest["level"] == "amber"
    assert 0.3 <= latest["risk_score"] < 0.8
    assert latest["drivers"] and all(d["deviation_z"] is not None for d in latest["drivers"])
    assert latest["escalation"]["required"] is False


def test_ast_02_reaches_red_with_an_urgent_flag(store: Store) -> None:
    levels = levels_by_day(store, AST_02)
    tail = last_days(levels, 20)
    assert "green" in last_days(levels, 60)[:40]
    assert "amber" in tail and tail[-1] == "red"
    order = [LEVELS.index(x) for x in tail if x in LEVELS]
    assert order == sorted(order), "once it worsens it does not get better again before today"

    latest = store.latest_assessment(AST_02)
    assert latest is not None and latest["level"] == "red"
    assert latest["escalation"]["required"] is True
    assert latest["drivers"]
    flags = [r for r in store.export_astronaut(AST_02)["self_reports"] if r["kind"] == "red_flag"]
    assert len(flags) == 1 and flags[0]["red_flags"]
    assert datetime.fromisoformat(flags[0]["timestamp"]).date() == TODAY - timedelta(days=1)
    assert any("RED_FLAG" in rule["rule_id"] for rule in latest["fired_rules"])


@pytest.mark.parametrize("astronaut_id", [AST_01, AST_02])
def test_latest_assessment_is_fresh_and_today_has_no_daily_report(store: Store, astronaut_id: str) -> None:
    latest = store.latest_assessment(astronaut_id)
    assert latest is not None
    age = END - datetime.fromisoformat(latest["as_of"])
    assert timedelta(0) <= age < timedelta(hours=24)
    daily = store.latest_self_report(astronaut_id, "daily")
    assert daily is not None and datetime.fromisoformat(daily["timestamp"]).date() == TODAY - timedelta(days=1)


def test_the_symptom_arc(store: Store) -> None:
    def pain(astronaut_id: str) -> list[int]:
        return [r["pain_nrs"] for r in store.export_astronaut(astronaut_id)["self_reports"] if r["kind"] == "daily"]

    one, two = pain(AST_01), pain(AST_02)
    assert max(one[:40]) <= 3 and 3 <= max(one[-7:]) <= 6
    assert max(two[:40]) <= 3 and max(two[-4:]) >= 7
    assert max(r["stiffness_nrs"] for r in store.export_astronaut(AST_02)["self_reports"] if r["kind"] == "daily") >= 6
    sleep = [r["sleep"]["hours"] for r in store.export_astronaut(AST_02)["self_reports"] if r["kind"] == "daily"]
    assert sum(sleep[:20]) / 20 > sum(sleep[-5:]) / 5 + 0.8


# --- Houston ---------------------------------------------------------------------------------


def test_the_red_flag_reached_houston_inside_a_ground_window(store: Store) -> None:
    items = store.ground_outbox_items(AST_02, 500)
    assert len(items) >= 30
    red = [i for i in items if i.payload["level"] == "red" and i.payload["red_flags"]]
    assert red and red[0].key.day == TODAY - timedelta(days=1)
    assert red[0].state == "sent" and red[0].received_at is not None
    assert red[0].payload["escalation"]["required"] is True
    assert red[0].payload["contacts"], "the call to the crew medical officer travels with it"

    for item in items:
        if item.state == "sent":
            assert item.received_at is not None
            received = datetime.fromisoformat(item.received_at)
            assert received >= datetime.fromisoformat(item.enqueued_at)
            assert received <= END
            assert link_state(demo_seed.GROUND_WINDOWS, received) == {"state": "up"}
    assert store.last_ground_contact(AST_02) is not None


def test_ground_windows_match_the_compose_file() -> None:
    windows = demo_seed.GROUND_WINDOWS
    assert (windows.anchor, windows.period_min, windows.up_min) == (datetime(2026, 1, 1, tzinfo=UTC), 15, 5)


@pytest.mark.parametrize("minute", range(0, 60, 7))
def test_ground_receipt_always_lands_in_an_open_window_after_the_enqueue(minute: int) -> None:
    enqueued = datetime(2026, 11, 14, 20, minute, 30, tzinfo=UTC)

    received = demo_seed.ground_receipt(enqueued, demo_seed.GROUND_WINDOWS)

    assert received >= enqueued
    assert received - enqueued < timedelta(minutes=15)
    assert link_state(demo_seed.GROUND_WINDOWS, received) == {"state": "up"}


# --- scale (mirrors the engine's z) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("mad", "std", "median"),
    [(0.4, 0.5, 10.0), (0.0, 0.5, 10.0), (0.0, 0.0, 10.0), (0.0, 0.0, -20.0), (0.0, 0.0, 0.0), (1e-9, 1e-9, 4.0)],
)
def test_the_scale_is_the_one_of_the_engine_z_score(mad: float, std: float, median: float) -> None:
    entry = {
        "feature_id": "stretch.full_spine.elongation_mm",
        "gravity_environment": "earth_1g",
        "n": 3,
        "mean": median,
        "std": std,
        "median": median,
        "mad": mad,
        "min": 0,
        "max": 1,
    }
    model = BaselineEntry.model_validate(entry)

    assert demo_seed.robust_scale(entry) == pytest.approx(1.0 / RobustZScorer().score(median + 1.0, model))


# --- the views and the APIs serve it -------------------------------------------------------------


@pytest.fixture
def ui(seeded: Seeded, tmp_path: Path) -> Iterator[TestClient]:
    settings = AppSettings(_env_file=None, db_path=seeded.path, ui_token=UI_TOKEN, backup_dir=tmp_path / "backups")
    app = create_app(settings, clock=lambda: END, engine_transport=httpx.ASGITransport(app=create_default_app()))
    yield TestClient(app, headers=UI_HEADERS)
    app.state.runtime.store.close()


def view(ui: TestClient, astronaut_id: str, name: str, schema: str, **params: Any) -> dict[str, Any]:
    response = ui.get(f"/ui/crew/{astronaut_id}/{name}", params=params, headers={**UI_HEADERS, "X-Astronaut-Id": astronaut_id})
    assert response.status_code == 200, response.text
    body = response.json()
    assert validation.errors(schema, body) == []
    return body


def test_today_view(ui: TestClient) -> None:
    one = view(ui, AST_01, "views/today", "app/ui-today")
    two = view(ui, AST_02, "views/today", "app/ui-today")

    assert (one["level"], two["level"]) == ("amber", "red")
    assert one["reasons"] and two["reasons"]
    assert "TASK_DAILY_REPORT" in one["also_today"] and "TASK_DAILY_REPORT" in two["also_today"]
    assert one["lumbar_delta_mm"] is not None
    assert one["trust"]["data_total"] == 4
    assert two["recommendations"]


def test_history_view_covers_the_whole_window(ui: TestClient) -> None:
    body = view(ui, AST_02, "views/history", "app/ui-history", **{"from": (TODAY - timedelta(days=59)).isoformat(), "to": TODAY.isoformat()})

    assert len(body["days"]) == 60
    assert body["days"][-1]["level"] == "red" or body["days"][-1]["level"] == "nodata"
    assert body["days"][-2]["level"] == "red" and body["days"][-2]["report"]["red_flags"]
    assert body["today_day"] is not None and body["preflight_range_mm"] is not None
    assert all(d["session"] is not None for d in body["days"])
    assert [d["pain_nrs"] for d in body["days"][:-1]].count(None) == 0


def test_baseline_view_shows_a_finished_baseline(ui: TestClient) -> None:
    for astronaut_id in (AST_01, AST_02):
        body = view(ui, astronaut_id, "views/baseline", "app/ui-baseline")
        assert body["recorded"] >= body["required"] and body["reference_version"] is not None
        assert {s["status"] for s in body["sessions"]} == {"complete"}


def test_houston_and_outbox_views(ui: TestClient) -> None:
    houston = view(ui, AST_02, "houston", "app/ui-houston")
    outbox = view(ui, AST_02, "outbox", "app/ui-outbox")

    assert houston["summary"] is not None and houston["last_contact_at"] is not None
    assert houston["summary"]["level"] == "red"
    assert len(outbox["items"]) >= 30 and outbox["items"][0]["state"] in ("sent", "queued")
    assert any(i["state"] == "sent" for i in outbox["items"])


def test_the_read_api_serves_it(seeded: Seeded) -> None:
    app = create_read_app(AppSettings(_env_file=None, db_path=seeded.path), clock=lambda: END)
    with TestClient(app) as client:
        status = client.get(f"/crew/{AST_02}/status").json()
        assert validation.errors("app/status", status) == []
        assert status["latest_assessment"]["level"] == "red" and status["active_baseline"]["version"] >= 3
        assessments = client.get(f"/crew/{AST_01}/assessments", params={"from": "2026-09-01T00:00:00Z", "to": END.isoformat()}).json()
        assert validation.errors("app/assessment-list", assessments) == []
        assert len(assessments["items"]) >= 30
        history = client.get(f"/crew/{AST_01}/history", params={"from": "2026-09-01T00:00:00Z", "to": END.isoformat()}).json()
        assert validation.errors("app/history", history) == []


# --- the command line ------------------------------------------------------------------------


def test_main_seeds_and_is_idempotent(tmp_path: Path, short_history: None, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "cli.db"
    transport = httpx.ASGITransport(app=create_default_app())
    argv = ["--end", END.isoformat(), "--engine-url", "http://engine", "--db-path", str(path)]

    assert demo_seed.main(argv, engine_transport=transport) == 0
    before = dump_everything(path)
    with caplog.at_level("INFO", logger="monitor.demo_seed"):
        assert demo_seed.main(argv, engine_transport=transport) == 0

    assert dump_everything(path) == before
    assert sum("skipp" in r.getMessage().lower() for r in caplog.records) >= 2


def test_main_fails_cleanly_when_the_engine_is_down(tmp_path: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    code = demo_seed.main(["--end", END.isoformat(), "--db-path", str(tmp_path / "down.db")], engine_transport=httpx.MockTransport(refuse))

    assert code == 1


def test_main_rejects_a_naive_end(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        demo_seed.main(["--end", "2026-11-15T12:00:00", "--db-path", str(tmp_path / "x.db")])


def test_defaults_point_at_the_compose_engine() -> None:
    args = demo_seed.parse_args([])

    assert args.engine_url == "http://engine:8001"
    assert args.end is None and args.db_path is None
