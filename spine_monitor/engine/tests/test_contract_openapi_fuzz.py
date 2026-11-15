"""Fuzz of the real engine API against the OpenAPI contract (contracts/openapi.yaml).

The oracle is the contract YAML, not the OpenAPI that FastAPI generates: the handlers read the body by hand and
the generated one carries no request schemas. Deterministic and bounded: derandomized Hypothesis (same sequence
of cases on every run), 40 examples per operation, no deadline and
without Schemathesis's coverage phase (deterministic but adds ~40 s).
"""

import json
from pathlib import Path

import pytest
import schemathesis
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings
from schemathesis.specs.openapi.checks import positive_data_acceptance
from spine_engine.api import create_default_app

# Starlette's cookies warning comes from every request Schemathesis generates; it adds nothing here.
pytestmark = [pytest.mark.contract, pytest.mark.filterwarnings("ignore::DeprecationWarning")]

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
BASE_URL = "http://testserver"

CONFIG = schemathesis.Config.from_dict({"phases": {"coverage": {"enabled": False}}})
schema = schemathesis.openapi.from_path(CONTRACT, config=CONFIG)

# An engine 500 must also come out as the contract's problem details (no exceptions escape).
client = TestClient(create_default_app(), raise_server_exceptions=False)


@schema.parametrize()
@settings(derandomize=True, max_examples=40, deadline=None, suppress_health_check=list(HealthCheck))
def test_engine_answers_what_the_contract_promises(case: schemathesis.Case) -> None:
    kwargs = case.as_transport_kwargs(base_url=BASE_URL)
    response = client.request(**kwargs)
    case.validate_response(response, excluded_checks=_domain_rejections(case, response))


# Domain rejections per operation: the only 422 that the request schema cannot express.
_DOMAIN_REJECTIONS = {
    "/baseline": {"insufficient_data"},
    "/assess": {"insufficient_data"},
    "/simulate": {"unknown_feature", "invalid_request", "insufficient_data"},
    "/stretch-estimate": {"invalid_request"},
}


def rechazo_de_dominio(path: str, status: int, problem_type: str | None) -> bool:
    """Is it a documented domain 422 for that route? Any other combination is not."""
    return status == 422 and problem_type in _DOMAIN_REJECTIONS.get(path, set())


def _domain_rejections(case: schemathesis.Case, response) -> list:
    """`positive_data_acceptance` counts as a failure a 422 to a request valid per the schema.

    The contract documents that 422 on POST /baseline (`insufficient_data`: e.g. no sessions at
    reference gravity), but Schemathesis 4.33 does not read the documented responses for this check: it only compares against
    `expected_statuses` of its configuration. The check is skipped only for that 422 and the body
    is still validated against the error schema. The same goes for POST /simulate: an override on a
    feature outside the catalogue or with no value in the baseline (`unknown_feature`) or with a non-finite result
    (`invalid_request`) is a domain rejection that the request schema cannot express. The bound is
    the same in /stretch-estimate: resistances must be within the measured domain, which depends on the reference.
    Inconsistent identities in the baseline, assessment or simulation produce `insufficient_data`.
    Per operation: an `invalid_request` on /features, /baseline or /assess would be a divergence between the schema
    and Pydantic and must fail. Any other rejection of valid data fails.
    """
    problem_type = json.loads(response.content).get("type") if response.status_code == 422 else None
    if rechazo_de_dominio(case.operation.path, response.status_code, problem_type):
        return [positive_data_acceptance]
    return []


@pytest.mark.parametrize(
    ("path", "status", "problem_type", "expected"),
    [
        ("/baseline", 422, "insufficient_data", True),
        ("/baseline", 422, "invalid_request", False),
        ("/baseline", 422, "unknown_feature", False),
        ("/simulate", 422, "unknown_feature", True),
        ("/simulate", 422, "invalid_request", True),
        ("/simulate", 422, "insufficient_data", True),
        ("/stretch-estimate", 422, "invalid_request", True),
        ("/stretch-estimate", 422, "unknown_feature", False),
        ("/assess", 422, "invalid_request", False),
        ("/assess", 422, "insufficient_data", True),
        ("/features", 422, "invalid_request", False),
        ("/simulate", 400, "invalid_request", False),
        ("/simulate", 422, None, False),
    ],
)
def test_domain_rejections_are_scoped_per_operation(path, status, problem_type, expected):
    assert rechazo_de_dominio(path, status, problem_type) is expected
