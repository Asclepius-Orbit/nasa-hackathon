"""Ports of the agent towards the rest of the system. They are Protocols: implementations need not import anything from here.

Read-only: the agent never writes to the system. `None` means no data / not found.
"""

from typing import Any, Protocol


class StatusSource(Protocol):
    """Where the agent reads the astronaut status and the explanation of an assessment from."""

    async def current_status(self, astronaut_id: str) -> dict[str, Any] | None:
        """Current status of the astronaut, or None when there is no data for them."""
        ...

    async def explanation(self, assessment_id: str) -> dict[str, Any] | None:
        """Full explanation of an assessment, or None when the assessment does not exist."""
        ...
