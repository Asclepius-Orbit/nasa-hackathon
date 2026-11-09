"""`POST /assess` con el ejemplo del contrato y la configuración por defecto reproduce un snapshot versionado."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from spine_engine.api import create_default_app
from spine_engine.settings import EngineSettings

pytestmark = pytest.mark.golden

GOLDEN = Path(__file__).parent / "golden" / "assess-example.json"
EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "assess-request.json"
# Lo único que no es determinista: el id generado en cada evaluación. Los tiempos se fijan igualmente por si
# el motor añade alguno de reloj de pared.
NORMALIZED = {"assessment_id": "<assessment_id>", "as_of": "<time>", "created_at": "<time>", "computed_at": "<time>"}


def _normalized(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {k: NORMALIZED[k] if k in NORMALIZED else _normalized(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalized(v) for v in value]
    return value


def snapshot() -> dict[str, Any]:
    # Sin `.env`: el snapshot es el de la configuración por defecto, no el de la máquina que lo ejecuta.
    client = TestClient(create_default_app(EngineSettings(_env_file=None)))
    response = client.post("/assess", json=json.loads(EXAMPLE.read_text(encoding="utf-8")))
    assert response.status_code == 200
    return _normalized(response.json())


def test_assess_example_matches_the_golden_snapshot():
    current = snapshot()
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    assert GOLDEN.exists(), "golden snapshot missing: run with UPDATE_GOLDEN=1 and review the diff"
    assert current == json.loads(GOLDEN.read_text(encoding="utf-8"))
