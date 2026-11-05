"""Engine client (contracts/openapi.yaml): /features, /baseline and /assess.

What is sent is validated first against the JSON Schema of the request, and what comes back against
the schema of the response: a contract break shows up here, not later in the database.
"""

from typing import Any

import httpx
from spine_contracts import validation


class EngineError(Exception):
    """The engine answered with an error (problem details) or with something that breaks the contract."""

    def __init__(self, message: str, *, status: int | None = None, problem: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.problem = problem


class EngineClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _post(self, path: str, request_schema: str, response_schema: str, body: dict[str, Any]) -> dict[str, Any]:
        found = validation.errors(request_schema, body)
        if found:
            raise EngineError(f"{path}: request violates {request_schema}: {'; '.join(found[:5])}")
        try:
            response = await self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise EngineError(f"{path}: engine unreachable: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            raise EngineError(f"{path}: engine returned non-JSON (HTTP {response.status_code})", status=response.status_code) from None
        if response.is_error:
            problem = payload if isinstance(payload, dict) else None
            detail = (problem or {}).get("detail") or (problem or {}).get("title") or "error"
            raise EngineError(f"{path}: HTTP {response.status_code}: {detail}", status=response.status_code, problem=problem)
        found = validation.errors(response_schema, payload)
        if found:
            raise EngineError(f"{path}: response violates {response_schema}: {'; '.join(found[:5])}")
        return payload

    async def features(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/features", "engine/features-request", "domain/feature-set", request)

    async def baseline(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/baseline", "engine/baseline-request", "domain/baseline", request)

    async def assess(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/assess", "engine/assess-request", "domain/assessment", request)
