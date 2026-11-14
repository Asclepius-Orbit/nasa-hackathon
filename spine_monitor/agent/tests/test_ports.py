from typing import Any

import pytest
from spine_agent.ports import StatusSource


class FakeStatusSource:
    """Hand-written in-memory fake: None means no data / not found."""

    def __init__(self, statuses: dict[str, dict[str, Any]], explanations: dict[str, dict[str, Any]]) -> None:
        self._statuses = statuses
        self._explanations = explanations

    async def current_status(self, astronaut_id: str) -> dict[str, Any] | None:
        return self._statuses.get(astronaut_id)

    async def explanation(self, assessment_id: str) -> dict[str, Any] | None:
        return self._explanations.get(assessment_id)


@pytest.mark.unit
@pytest.mark.anyio
async def test_fake_satisfies_status_source_and_reports_missing_as_none(anyio_backend: str):
    source: StatusSource = FakeStatusSource({"a1": {"level": "ok"}}, {"x1": {"why": "baseline"}})
    assert await source.current_status("a1") == {"level": "ok"}
    assert await source.current_status("nope") is None
    assert await source.explanation("x1") == {"why": "baseline"}
    assert await source.explanation("nope") is None
