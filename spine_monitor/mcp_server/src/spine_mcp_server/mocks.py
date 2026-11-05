"""Mocks de solo lectura de los puertos, servidos desde los ejemplos válidos de los contratos.

Los ejemplos se localizan junto al paquete `spine_contracts` (`contracts/examples`), que en el workspace
está instalado en modo editable. Nunca se escribe ningún fichero.
"""

import json
from datetime import datetime
from pathlib import Path

import spine_contracts
from spine_contracts import AssessRequest, Explanation, History, SimulateRequest, SimulateResponse, Status

from spine_mcp_server.errors import UpstreamError

EXAMPLES_DIR = Path(spine_contracts.__file__).resolve().parents[2] / "examples" / "valid"


def _load(relative: str) -> object:
    return json.loads((EXAMPLES_DIR / relative).read_text(encoding="utf-8"))


def _not_found(what: str, key: str) -> UpstreamError:
    return UpstreamError(404, f"{what} not found", f"No {what.lower()} with id {key}.")


class ExampleAppReader:
    """`AppReader` sobre `app/status.json`, `app/history.json` y `app/explanation.json`.

    Acepta datos alternativos (p. ej. un history más largo) para ejercitar el filtrado y el límite.
    """

    def __init__(self, status: Status | None = None, history: History | None = None, explanation: Explanation | None = None) -> None:
        self._status = status or Status.model_validate(_load("app/status.json"))
        self._history = history or History.model_validate(_load("app/history.json"))
        self._explanation = explanation or Explanation.model_validate(_load("app/explanation.json"))

    def get_status(self, astronaut_id: str) -> Status:
        if astronaut_id != self._status.astronaut_id:
            raise _not_found("Astronaut", astronaut_id)
        return self._status

    def get_history(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> History:
        if astronaut_id != self._history.astronaut_id:
            raise _not_found("Astronaut", astronaut_id)
        if to < from_:
            raise UpstreamError(422, "invalid_range", f"'to' ({to.isoformat()}) is before 'from' ({from_.isoformat()}).")
        feature_sets = sorted((f for f in self._history.feature_sets if from_ <= f.computed_at <= to), key=lambda f: f.computed_at)
        assessments = sorted((a for a in self._history.assessments if from_ <= a.as_of <= to), key=lambda a: a.as_of)
        truncated = len(feature_sets) > limit or len(assessments) > limit
        return self._history.model_copy(
            update={
                "from_": from_,
                "to": to,
                "limit": limit,
                "truncated": truncated,
                "feature_sets": feature_sets[-limit:],
                "assessments": assessments[-limit:],
            }
        )

    def get_explanation(self, assessment_id: str) -> Explanation:
        if assessment_id != self._explanation.assessment.assessment_id:
            raise _not_found("Assessment", assessment_id)
        return self._explanation


class ExampleSimulator:
    """`Simulator` que devuelve siempre `engine/simulate-response.json`, sea cual sea la petición."""

    def __init__(self) -> None:
        self._response = SimulateResponse.model_validate(_load("engine/simulate-response.json"))

    def simulate(self, request: SimulateRequest) -> SimulateResponse:
        return self._response


class ExampleAssessBaseSource:
    """`AssessBaseSource` sobre el `base` de `engine/simulate-request.json`."""

    def __init__(self) -> None:
        self._base = SimulateRequest.model_validate(_load("engine/simulate-request.json")).base

    def get_base(self, astronaut_id: str) -> AssessRequest:
        if astronaut_id != self._base.astronaut_id:
            raise _not_found("Astronaut", astronaut_id)
        return self._base
