"""Ports of the MCP server towards the rest of the system. They are Protocols: implementations need not import anything from here.

There are only read and simulation operations: the MCP server never writes to the system.
"""

from datetime import datetime
from typing import Protocol

from spine_contracts import (
    AssessRequest,
    Explanation,
    History,
    SelfReportList,
    SessionList,
    SimulateRequest,
    SimulateResponse,
    Status,
)


class AppReader(Protocol):
    """Read access to the app API (`app/*`). Failures are signalled with `UpstreamError`."""

    def get_status(self, astronaut_id: str) -> Status:
        """Current state of the astronaut: latest assessment, session, self-report, devices and freshness."""
        ...

    def get_history(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> History:
        """Feature sets and assessments in [from_, to], from oldest to most recent, with at most `limit` items."""
        ...

    def get_sessions(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> SessionList:
        """Sessions started in [from_, to], from oldest to most recent, with at most `limit` items."""
        ...

    def get_self_reports(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> SelfReportList:
        """Self-reports timestamped in [from_, to], from oldest to most recent, with at most `limit` items."""
        ...

    def get_explanation(self, assessment_id: str) -> Explanation:
        """Full explanation of an assessment: baseline, features, self-reports and unresolved data."""
        ...


class Simulator(Protocol):
    """Hypothetical ("what if") simulation of the engine. It never modifies real data."""

    def simulate(self, request: SimulateRequest) -> SimulateResponse:
        """Reference assessment versus that of the scenario with the overrides applied."""
        ...


class AssessBaseSource(Protocol):
    """Source of the assessment request the overrides of a simulation are applied to."""

    def get_base(self, astronaut_id: str) -> AssessRequest:
        """Current base assessment request of the astronaut."""
        ...
