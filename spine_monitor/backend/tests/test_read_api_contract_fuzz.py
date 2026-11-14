"""Fuzz of the read-only API against its OpenAPI contract (contracts/app-openapi.yaml).

The oracle is the contract YAML, not the document FastAPI generates. The database holds the contract
examples wired together, so generated ids sometimes hit real data. Deterministic and bounded:
Hypothesis derandomized, 40 examples per operation, no deadline and no coverage phase, as the engine's
fuzz test.
"""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import schemathesis
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings
from monitor.db import Store
from monitor.readonly import create_read_app
from monitor.settings import AppSettings
from schemathesis.specs.openapi.checks import positive_data_acceptance

pytestmark = [pytest.mark.contract, pytest.mark.filterwarnings("ignore::DeprecationWarning")]

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "app-openapi.yaml"
BASE_URL = "http://testserver"

CONFIG = schemathesis.Config.from_dict({"phases": {"coverage": {"enabled": False}}})
schema = schemathesis.openapi.from_path(CONTRACT, config=CONFIG)


def _populated_store() -> Store:
    spec = importlib.util.spec_from_file_location("read_api_data", Path(__file__).with_name("test_read_api.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store = Store(":memory:")
    module.populate(store)
    return store


_store = _populated_store()
# A 500 must also come out as contract problem details: no exception escapes.
client = TestClient(
    create_read_app(AppSettings(_env_file=None), store=_store, clock=lambda: datetime(2027, 2, 9, 10, 0, tzinfo=UTC)),
    raise_server_exceptions=False,
)


@schema.parametrize()
@settings(derandomize=True, max_examples=40, deadline=None, suppress_health_check=list(HealthCheck))
def test_read_api_answers_what_the_contract_promises(case: schemathesis.Case) -> None:
    response = client.request(**case.as_transport_kwargs(base_url=BASE_URL))
    case.validate_response(response, excluded_checks=_domain_rejections(response))
    assert response.status_code != 500, json.loads(response.content)


def _domain_rejections(response) -> list:
    """`positive_data_acceptance` takes a 422 to a schema-valid request as a failure.

    The contract documents that 422 for windows (`invalid_range`: `to` before `from`, a rule a JSON
    Schema cannot express), but Schemathesis does not read the documented responses for this check. It is
    excluded only for that 422; the body is still validated against the error schema. Any other rejection
    of valid data fails.
    """
    if response.status_code == 422 and json.loads(response.content).get("type") == "invalid_range":
        return [positive_data_acceptance]
    return []
