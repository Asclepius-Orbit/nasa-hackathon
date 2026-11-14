"""Fuzz de la API real del engine contra el contrato OpenAPI (contracts/openapi.yaml).

El oráculo es el YAML del contrato, no el OpenAPI que genera FastAPI: los handlers leen el cuerpo a mano y
el generado no lleva schemas de petición. Determinista y acotado: Hypothesis derandomizado (misma secuencia
de casos en cada ejecución), 40 ejemplos por operación, sin deadline y
sin la fase de cobertura de Schemathesis (determinista pero añade ~40 s).
"""

import json
from pathlib import Path

import pytest
import schemathesis
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings
from schemathesis.specs.openapi.checks import positive_data_acceptance
from spine_engine.api import create_default_app

# El warning de cookies de Starlette sale de cada petición que genera Schemathesis; no aporta nada aquí.
pytestmark = [pytest.mark.contract, pytest.mark.filterwarnings("ignore::DeprecationWarning")]

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
BASE_URL = "http://testserver"

CONFIG = schemathesis.Config.from_dict({"phases": {"coverage": {"enabled": False}}})
schema = schemathesis.openapi.from_path(CONTRACT, config=CONFIG)

# Un 500 del engine también debe salir como problem details del contrato (no hay excepciones que escapen).
client = TestClient(create_default_app(), raise_server_exceptions=False)


@schema.parametrize()
@settings(derandomize=True, max_examples=40, deadline=None, suppress_health_check=list(HealthCheck))
def test_engine_answers_what_the_contract_promises(case: schemathesis.Case) -> None:
    kwargs = case.as_transport_kwargs(base_url=BASE_URL)
    response = client.request(**kwargs)
    case.validate_response(response, excluded_checks=_domain_rejections(case, response))


# Rechazos de dominio por operación: el único 422 que el schema de la petición no puede expresar.
_DOMAIN_REJECTIONS = {
    "/baseline": {"insufficient_data"},
    "/simulate": {"unknown_feature", "invalid_request"},
    "/stretch-estimate": {"invalid_request"},
}


def rechazo_de_dominio(path: str, status: int, problem_type: str | None) -> bool:
    """¿Es un 422 de dominio documentado para esa ruta? Cualquier otra combinación no lo es."""
    return status == 422 and problem_type in _DOMAIN_REJECTIONS.get(path, set())


def _domain_rejections(case: schemathesis.Case, response) -> list:
    """`positive_data_acceptance` toma por fallo un 422 a una petición válida según el schema.

    El contrato documenta ese 422 en POST /baseline (`insufficient_data`: p. ej. sin sesiones en gravedad de
    referencia), pero Schemathesis 4.33 no lee las respuestas documentadas para este check: solo compara con
    `expected_statuses` de su configuración. Se excluye la comprobación únicamente para ese 422 y el cuerpo
    sigue validándose contra el schema de error. Lo mismo vale para POST /simulate: un override sobre una
    feature fuera del catálogo o sin valor en la base (`unknown_feature`) o con resultado no finito
    (`invalid_request`) es un rechazo de dominio que el schema de la petición no puede expresar. El acotado es
    igual en /stretch-estimate: las resistencias deben estar dentro del dominio medido, que depende de la referencia.
    por operación: un `invalid_request` en /features, /baseline o /assess sería una divergencia entre el schema
    y Pydantic y debe fallar. Cualquier otro rechazo de datos válidos falla.
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
        ("/simulate", 422, "insufficient_data", False),
        ("/stretch-estimate", 422, "invalid_request", True),
        ("/stretch-estimate", 422, "unknown_feature", False),
        ("/assess", 422, "invalid_request", False),
        ("/assess", 422, "insufficient_data", False),
        ("/features", 422, "invalid_request", False),
        ("/simulate", 400, "invalid_request", False),
        ("/simulate", 422, None, False),
    ],
)
def test_domain_rejections_are_scoped_per_operation(path, status, problem_type, expected):
    assert rechazo_de_dominio(path, status, problem_type) is expected
