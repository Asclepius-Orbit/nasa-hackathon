"""Errors in problem details format (RFC 9457), per `schemas/v1/engine/error.schema.json`."""

from fastapi.responses import JSONResponse
from spine_contracts import ErrorResponse, dump
from spine_contracts.models.engine.error_schema import Error, Type

PROBLEM_JSON = "application/problem+json"


def problem(type_: Type, title: str, status: int, detail: str | None = None, errors: list[Error] | None = None) -> JSONResponse:
    # Pyright false positive: the generated model declares the default of `errors` as a positional Field.
    body = ErrorResponse(type=type_, title=title, status=status)  # pyright: ignore[reportCallIssue]
    if detail is not None:
        body.detail = detail
    if errors:
        body.errors = errors
    return JSONResponse(dump(body), status_code=status, media_type=PROBLEM_JSON)


def to_errors(messages: list[str]) -> list[Error]:
    """`validation.errors` returns 'pointer: message'; the root pointer is the empty string."""
    result = []
    for line in messages:
        pointer, _, message = line.partition(": ")
        result.append(Error(pointer="" if pointer == "/" else pointer, message=message))
    return result
