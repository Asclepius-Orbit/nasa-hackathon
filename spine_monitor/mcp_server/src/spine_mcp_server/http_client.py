"""Real HTTP client of the `AppReader` port, over the app read API (`contracts/app-openapi.yaml`).

Every failure is signalled with `UpstreamError`, like the mocks: the app problem details (4xx) pass through as they are
and transport or shape failures are translated to 502, 503 or 504. No network call is made on construction.
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
"""Cap on the detail coming from the app and reaching the LLM (upstream content, untrusted)."""


class _AppApi:
    """Common base of the app read API clients: httpx client, closing and error translation in `_get`."""

    def __init__(self, base_url: str, *, client: httpx.Client | None = None, timeout: float = 10.0) -> None:
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(base_url=base_url, timeout=timeout)
        self._base = base_url.rstrip("/")

    def close(self) -> None:
        """Close the own httpx client; an injected one is closed by whoever created it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()

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


class HttpAppReader(_AppApi):
    """`AppReader` that queries the app read API over HTTP."""

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


class HttpAssessBaseSource(_AppApi):
    """`AssessBaseSource` that asks the app read API for the assessment request it would send now."""

    def get_base(self, astronaut_id: str) -> AssessRequest:
        return self._get(f"/crew/{_segment(astronaut_id)}/assess-base", AssessRequest)


def _segment(identifier: str) -> str:
    """An id as a single path segment: `/`, `?`, `#` or `..` cannot change the endpoint or open the query."""
    return quote(identifier, safe="")


def _window(from_: datetime, to: datetime, limit: int) -> dict[str, str | int]:
    return {"from": from_.isoformat(), "to": to.isoformat(), "limit": limit}


def _problem(response: httpx.Response) -> AppErrorResponse | None:
    try:
        return AppErrorResponse.model_validate_json(response.content)
    except ValidationError:
        return None


def _detail(problem: AppErrorResponse | ErrorResponse) -> str | None:
    """The problem `detail` or, if absent, its field errors (`pointer: message`); truncated to `MAX_DETAIL_CHARS`."""
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
    """Simulator that queries the engine directly through its HTTP contract."""

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
