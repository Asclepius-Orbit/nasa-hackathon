"""Errores del servidor MCP."""

from spine_contracts import AppErrorResponse


class UpstreamError(Exception):
    """Fallo de un sistema aguas arriba (app o motor), con la forma de `app/error` (problem details)."""

    def __init__(self, status: int, title: str, detail: str | None = None) -> None:
        super().__init__(status, title, detail)
        self.status = status
        self.title = title
        self.detail = detail

    @classmethod
    def from_problem(cls, problem: AppErrorResponse) -> UpstreamError:
        return cls(problem.status, problem.title, problem.detail)

    def to_problem(self, type_: str) -> AppErrorResponse:
        """Problem details de `app/error`; `type_` es uno de sus tipos (`not_found`, `invalid_range`...)."""
        payload: dict[str, object] = {"type": type_, "title": self.title, "status": self.status}
        if self.detail is not None:
            payload["detail"] = self.detail
        return AppErrorResponse.model_validate(payload)

    def __str__(self) -> str:
        return self.title if self.detail is None else f"{self.title}: {self.detail}"
