"""Cliente HTTP real del puerto `AppReader`, sobre la API de lectura de la app (`contracts/app-openapi.yaml`).

Todo fallo se señala con `UpstreamError`, igual que los mocks: los problem details de la app (4xx) pasan tal cual
y los fallos de transporte o de forma se traducen a 502, 503 o 504. No se hace ninguna llamada de red al construir.
"""

from datetime import datetime
from types import TracebackType
from typing import Self
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError
from spine_contracts import (
    AppErrorResponse,
    AssessRequest,
    ErrorResponse,
    Explanation,
    History,
    SelfReportList,
    SessionList,
    SimulateRequest,
    SimulateResponse,
    Status,
    dump,
)

from spine_mcp_server.errors import UpstreamError

MAX_DETAIL_CHARS = 500
"""Tope del detalle que viene de la app y llega al LLM (contenido aguas arriba, no confiable)."""


class HttpAppReader:
    """`AppReader` que consulta la API de lectura de la app por HTTP."""

    def __init__(self, base_url: str, *, client: httpx.Client | None = None, timeout: float = 10.0) -> None:
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(base_url=base_url, timeout=timeout)
        self._base = base_url.rstrip("/")

    def close(self) -> None:
        """Cierra el cliente httpx propio; uno inyectado lo cierra quien lo creó."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()

    def get_status(self, astronaut_id: str) -> Status:
        return self._get(f"/crew/{_segment(astronaut_id)}/status", Status)

    def get_history(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> History:
        return self._get(f"/crew/{_segment(astronaut_id)}/history", History, _window(from_, to, limit))

    def get_sessions(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> SessionList:
        return self._get(f"/crew/{_segment(astronaut_id)}/sessions", SessionList, _window(from_, to, limit))

    def get_self_reports(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> SelfReportList:
        return self._get(f"/crew/{_segment(astronaut_id)}/self-reports", SelfReportList, _window(from_, to, limit))

    def get_explanation(self, assessment_id: str) -> Explanation:
        return self._get(f"/assessments/{_segment(assessment_id)}/explanation", Explanation)

    def _get[M: BaseModel](self, path: str, model: type[M], params: dict[str, str | int] | None = None) -> M:
        try:
            response = self._client.get(self._base + path, params=params)
        except httpx.TimeoutException as exc:
            raise UpstreamError(504, "App API timeout", f"No response from {self._base} ({type(exc).__name__}).") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(503, "App API unavailable", f"Cannot reach {self._base} ({type(exc).__name__}).") from exc
        if response.is_success:
            try:
                return model.model_validate_json(response.content)
            except ValidationError as exc:
                raise UpstreamError(502, "App API returned an invalid response", f"Body is not a valid {model.__name__}.") from exc
        raise _failure(response)


def _segment(identifier: str) -> str:
    """Un id como un único segmento de ruta: `/`, `?`, `#` o `..` no pueden cambiar de endpoint ni abrir la query."""
    return quote(identifier, safe="")


def _window(from_: datetime, to: datetime, limit: int) -> dict[str, str | int]:
    return {"from": from_.isoformat(), "to": to.isoformat(), "limit": limit}


def _problem(response: httpx.Response) -> AppErrorResponse | None:
    try:
        return AppErrorResponse.model_validate_json(response.content)
    except ValidationError:
        return None


def _detail(problem: AppErrorResponse | ErrorResponse) -> str | None:
    """`detail` del problema o, si no lo trae, sus errores de campo (`pointer: message`); recortado a `MAX_DETAIL_CHARS`."""
    detail = problem.detail or "; ".join(f"{error.pointer}: {error.message}" for error in problem.errors or []) or None
    return None if detail is None else detail[:MAX_DETAIL_CHARS]


def _failure(response: httpx.Response) -> UpstreamError:
    status = response.status_code
    problem = _problem(response)
    if status >= 500:
        return UpstreamError(status, "App API error", f"HTTP {status}")
    if problem is not None:
        return UpstreamError(problem.status, problem.title, _detail(problem))
    return UpstreamError(status, "App API request rejected", f"HTTP {status}")


class HttpSimulator:
    """Simulator que consulta directamente el motor mediante su contrato HTTP."""

    def __init__(self, base_url: str, *, client: httpx.Client | None = None, timeout: float = 10.0) -> None:
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(base_url=base_url, timeout=timeout)
        self._base = base_url.rstrip("/")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()

    def simulate(self, request: SimulateRequest) -> SimulateResponse:
        try:
            response = self._client.post(self._base + "/simulate", json=dump(request))
        except httpx.TimeoutException as exc:
            raise UpstreamError(504, "Engine API timeout", f"No response from {self._base} ({type(exc).__name__}).") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(503, "Engine API unavailable", f"Cannot reach {self._base} ({type(exc).__name__}).") from exc
        if response.is_success:
            try:
                return SimulateResponse.model_validate_json(response.content)
            except ValidationError as exc:
                raise UpstreamError(502, "Engine API returned an invalid response", "Body is not a valid SimulateResponse.") from exc
        status = response.status_code
        if status >= 500:
            raise UpstreamError(status, "Engine API error", f"HTTP {status}")
        try:
            problem = ErrorResponse.model_validate_json(response.content)
        except ValidationError:
            raise UpstreamError(status, "Engine API request rejected", f"HTTP {status}") from None
        raise UpstreamError(problem.status, problem.title, _detail(problem))


_NOT_IMPLEMENTED_DETAIL = "The app API has no endpoint for this yet, so {what} is not available in http mode."


class UnavailableSimulator:
    """`Simulator` del modo http: la API de la app aún no expone la simulación."""

    def simulate(self, request: SimulateRequest) -> SimulateResponse:
        raise UpstreamError(501, "Simulation not available", _NOT_IMPLEMENTED_DETAIL.format(what="the simulator"))


class UnavailableAssessBase:
    """`AssessBaseSource` del modo http: la API de la app aún no expone la petición de valoración base."""

    def get_base(self, astronaut_id: str) -> AssessRequest:
        raise UpstreamError(501, "Assessment base not available", _NOT_IMPLEMENTED_DETAIL.format(what="the assessment base"))
