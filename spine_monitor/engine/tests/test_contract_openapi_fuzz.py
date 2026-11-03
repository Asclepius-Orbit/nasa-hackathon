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
    case.validate_response(response, excluded_checks=_domain_rejections(response))


def _domain_rejections(response) -> list:
    """`positive_data_acceptance` toma por fallo un 422 a una petición válida según el schema.

    El contrato documenta ese 422 como `insufficient_data` (p. ej. un baseline sin sesiones en gravedad
    de referencia), que no es una violación; se excluye la comprobación solo para esa respuesta, y el
    cuerpo sigue validándose contra el schema de error. Cualquier otro rechazo de datos válidos falla.
    """
    if response.status_code == 422 and json.loads(response.content).get("type") == "insufficient_data":
        return [positive_data_acceptance]
    return []
