"""Puertos del servidor MCP hacia el resto del sistema. Son Protocols: las implementaciones no necesitan importar nada de aquí.

Solo hay operaciones de lectura y de simulación: el servidor MCP nunca escribe en el sistema.
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
    """Lectura de la API de la app (`app/*`). Los fallos se señalan con `UpstreamError`."""

    def get_status(self, astronaut_id: str) -> Status:
        """Estado actual del astronauta: última valoración, sesión, autoinforme, dispositivos y frescura."""
        ...

    def get_history(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> History:
        """Feature sets y valoraciones en [from_, to], de más antiguo a más reciente, con como mucho `limit` elementos."""
        ...

    def get_sessions(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> SessionList:
        """Sesiones iniciadas en [from_, to], de más antigua a más reciente, con como mucho `limit` elementos."""
        ...

    def get_self_reports(self, astronaut_id: str, from_: datetime, to: datetime, limit: int = 100) -> SelfReportList:
        """Autoinformes con marca de tiempo en [from_, to], de más antiguo a más reciente, con como mucho `limit` elementos."""
        ...

    def get_explanation(self, assessment_id: str) -> Explanation:
        """Explicación completa de una valoración: baseline, features, autoinformes y datos sin resolver."""
        ...


class Simulator(Protocol):
    """Simulación hipotética («qué pasaría si») del motor. Nunca modifica datos reales."""

    def simulate(self, request: SimulateRequest) -> SimulateResponse:
        """Valoración de referencia frente a la del escenario con los overrides aplicados."""
        ...


class AssessBaseSource(Protocol):
    """Origen de la petición de valoración sobre la que se aplican los overrides de una simulación."""

    def get_base(self, astronaut_id: str) -> AssessRequest:
        """Petición de valoración base vigente del astronauta."""
        ...
