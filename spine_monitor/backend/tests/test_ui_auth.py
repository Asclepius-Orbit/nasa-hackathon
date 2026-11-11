"""The /ui API needs the shared token and only writes or reads for the unlocked astronaut; /health needs nothing (#67).
The astronaut export (#66) goes through the same rules."""

import sys

import pytest
from monitor.settings import AppSettings
from monitor.ui_auth import ui_token_of

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


def as_(harness, **headers: str) -> dict[str, str]:
    """Headers that override the harness ones; an empty value stands for a missing header (the client always sends its own)."""
    return headers


async def test_without_the_token_every_ui_route_is_refused(harness):
    response = await harness.http.get(f"/ui/crew/{harness.astronaut_id}/views/today", headers=as_(harness, authorization=""))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"] == "application/problem+json"


async def test_a_wrong_token_is_refused(harness):
    response = await harness.http.get("/ui/crew", headers=as_(harness, authorization="Bearer nope"))

    assert response.status_code == 401


async def test_writing_for_another_astronaut_than_the_unlocked_one_is_forbidden(harness):
    response = await harness.http.post("/ui/crew/AST-02/self-reports", json={"kind": "red_flag", "red_flags": ["leg_weakness"]})

    assert response.status_code == 403
    assert harness.store.latest_self_report_of_any_kind("AST-02") is None


async def test_a_session_of_another_astronaut_is_forbidden(harness):
    await harness.setup_astronaut()
    session_id, _ = await harness.start_session()

    response = await harness.http.post(f"/ui/sessions/{session_id}/stop", json={}, headers=as_(harness, **{"x-astronaut-id": "AST-02"}))

    assert response.status_code == 403


async def test_without_an_unlocked_astronaut_only_the_crew_routes_answer(harness):
    no_astronaut = as_(harness, **{"x-astronaut-id": ""})

    assert (await harness.http.put("/ui/crew/AST-05", headers=no_astronaut)).status_code == 201
    assert (await harness.http.get("/ui/crew", headers=no_astronaut)).status_code == 200
    assert (await harness.http.get(f"/ui/crew/{harness.astronaut_id}/profile", headers=no_astronaut)).status_code == 403


async def test_health_needs_no_token(harness):
    assert (await harness.http.get("/health", headers={"authorization": ""})).status_code == 200


async def test_the_export_carries_what_is_stored_but_the_raw_chunks(harness):
    await harness.setup_astronaut()
    session_id, start = await harness.start_session()
    await harness.send_chunks(session_id)
    await harness.stop_session(session_id, start)

    export = (await harness.http.get(f"/ui/crew/{harness.astronaut_id}/export")).json()

    assert export["profile"]["astronaut_id"] == harness.astronaut_id
    assert [s["session"]["session_id"] for s in export["sessions"]] == [session_id]
    assert export["feature_sets"] and export["assessments"]
    assert "chunks" not in export


def test_without_a_configured_token_one_is_generated_once_in_the_token_file(tmp_path):
    settings = AppSettings(_env_file=None, db_path=tmp_path / "app.db")

    token = ui_token_of(settings)

    assert len(token) >= 32
    assert (tmp_path / "ui-token").read_text(encoding="utf-8") == token
    assert ui_token_of(settings) == token


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes do not apply on Windows")
def test_an_empty_token_file_left_world_readable_is_regenerated_owner_only(tmp_path):
    path = tmp_path / "ui-token"
    path.write_text("", encoding="utf-8")
    path.chmod(0o644)

    token = ui_token_of(AppSettings(_env_file=None, db_path=tmp_path / "app.db"))

    assert len(token) >= 32
    assert path.read_text(encoding="utf-8") == token
    assert path.stat().st_mode & 0o777 == 0o600


def test_an_empty_token_file_is_regenerated(tmp_path):
    (tmp_path / "ui-token").write_text("\n", encoding="utf-8")

    token = ui_token_of(AppSettings(_env_file=None, db_path=tmp_path / "app.db"))

    assert len(token) >= 32
    assert (tmp_path / "ui-token").read_text(encoding="utf-8") == token


def test_the_configured_token_wins(tmp_path):
    assert ui_token_of(AppSettings(_env_file=None, db_path=tmp_path / "app.db", ui_token="configured")) == "configured"
    assert not (tmp_path / "ui-token").exists()
