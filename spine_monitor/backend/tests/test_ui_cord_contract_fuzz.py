"""Fuzz of the cord views of the /ui API (cord-protocols and cord-history) against contracts/ui-openapi.yaml.

The oracle is the contract YAML. The app runs for real (real store, real engine answering /stretch-estimate over ASGI) with
cord sessions of both postures, valid and invalid, so the generated requests meet data. Deterministic and bounded as the other
fuzz tests: Hypothesis derandomized, 40 examples per operation, no deadline and no coverage phase. Only these two reads are
fuzzed (the rest of the UI API needs a bus and sessions).
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import schemathesis
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings
from monitor.main import create_app
from monitor.settings import AppSettings
from spine_contracts import validation
from spine_engine.api import create_default_app

pytestmark = [pytest.mark.contract, pytest.mark.filterwarnings("ignore::DeprecationWarning")]

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
BASE_URL = "http://testserver"
TOKEN, AST = "fuzz-token", "AST-01"
NOW = datetime(2027, 2, 9, 10, 0, tzinfo=UTC)
STRAIGHT, FLEXION = "spine_resistance_straight_v1", "spine_resistance_max_flexion_v1"
FEATURE = {STRAIGHT: "stretch.full_spine.straight_resistance_ohm", FLEXION: "stretch.full_spine.max_flexion_resistance_ohm"}
OPERATIONS = ["getCordProtocolsView", "getCordHistoryView"]

CONFIG = schemathesis.Config.from_dict({"phases": {"coverage": {"enabled": False}}})
schema = schemathesis.openapi.from_path(CONTRACTS / "ui-openapi.yaml", config=CONFIG).include(operation_id=OPERATIONS)


def _example(name: str) -> dict:
    return json.loads((CONTRACTS / "examples" / "valid" / f"{name}.json").read_text(encoding="utf-8"))


def _populate(client: TestClient, store) -> None:
    store.register_crew(AST, NOW)
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Astronaut-Id": AST}
    profile = _example("domain/astronaut-profile") | {"launch_date": "2026-11-13", "landing_date": "2027-06-01"}
    assert client.put(f"/ui/crew/{AST}/profile", json=profile, headers=headers).status_code in (200, 201)
    # (protocol, day, phase, gravity, end_reason, score): valid and invalid measurements of both postures.
    rows = [
        (STRAIGHT, 1, "preflight", "earth_1g", "completed", 0.9),
        (STRAIGHT, 20, "inflight", "microgravity", "completed", 0.9),
        (STRAIGHT, 22, "inflight", "microgravity", "completed", 0.2),
        (STRAIGHT, 23, "inflight", "microgravity", "aborted_pain", 0.9),
        (FLEXION, 2, "preflight", "earth_1g", "completed", 0.9),
        (FLEXION, 21, "inflight", "microgravity", "completed", 0.9),
    ]
    for n, (protocol, day, phase, gravity, end_reason, score) in enumerate(rows):
        session_id = f"3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a{n:02d}"
        started = f"2026-12-{day:02d}T09:00:00Z" if day > 9 else f"2026-11-{day + 14:02d}T09:00:00Z"
        session = _example("domain/session.exercise")
        session.update(session_id=session_id, astronaut_id=AST, started_at=started, protocol_id=protocol, end_reason=end_reason)
        session["mission_context"].update(phase=phase, gravity_environment=gravity)
        session.pop("ended_at", None)
        store.insert_session(session, "processed")
        feature_set = _example("domain/feature-set.preflight")
        feature_set.update(session_id=session_id, astronaut_id=AST, protocol_id=protocol, gravity_environment=gravity, computed_at=started)
        feature_set.pop("window", None)
        feature_set["features"] = [
            {"feature_id": FEATURE[protocol], "value": 4500.0 + 100 * n, "unit": "ohm", "n": 40, "quality": {"score": score, "assessed_by": "engine"}}
        ]
        feature_set["skipped"] = []
        assert validation.errors("domain/feature-set", feature_set) == []
        store.put_feature_set(feature_set, phase=phase, anchor_at=started)


def _client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    settings_ = AppSettings(_env_file=None, db_path=tmp_path_factory.mktemp("cord-fuzz") / "app.db", ui_token=TOKEN)
    app = create_app(settings_, engine_transport=httpx.ASGITransport(app=create_default_app()), clock=lambda: NOW)
    client = TestClient(app, raise_server_exceptions=False)
    _populate(client, app.state.runtime.store)
    return client


@pytest.fixture(scope="module")
def cord_client(tmp_path_factory: pytest.TempPathFactory):
    client = _client(tmp_path_factory)
    yield client
    client.app.state.runtime.store.close()


@schema.parametrize()
@settings(derandomize=True, max_examples=40, deadline=None, suppress_health_check=list(HealthCheck))
def test_cord_views_answer_what_the_contract_promises(case: schemathesis.Case, cord_client: TestClient) -> None:
    # A well-formed id is the populated astronaut, so the requests reach the data; malformed ids still exercise the rejection.
    if re.fullmatch(r"AST-[0-9]{2,3}", str(case.path_parameters.get("astronaut_id", ""))):
        case.path_parameters["astronaut_id"] = AST
    kwargs = case.as_transport_kwargs(base_url=BASE_URL, headers={"Authorization": f"Bearer {TOKEN}", "X-Astronaut-Id": str(case.path_parameters["astronaut_id"])})
    response = cord_client.request(**kwargs)
    case.validate_response(response)
    assert response.status_code != 500, response.text


def test_the_fuzzed_app_holds_the_cord_data_the_requests_are_meant_to_reach(cord_client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Astronaut-Id": AST}
    history = cord_client.get(f"/ui/crew/{AST}/views/cord-history", headers=headers).json()
    assert [len(p["measurements"]) for p in history["postures"]] == [4, 2]
    assert [m["valid"] for m in history["postures"][0]["measurements"]].count(False) == 2
    cord = cord_client.get(f"/ui/crew/{AST}/views/cord-protocols", headers=headers).json()
    assert cord["postures"][0]["estimate"] is not None
