"""Detect drift between an OpenAPI document and the committed contract.

The app builds its HTTP API with FastAPI, which generates its own OpenAPI document. The contract
(`app-openapi.yaml`) is written first and is the reference; `diff` compares the two so a route that
is added, removed or renamed on either side fails a test instead of surprising the MCP server.
Only the surface is compared (path, method, operationId); bodies are checked against the JSON Schemas.
"""

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def operations(document: dict) -> dict[tuple[str, str], str | None]:
    """(path, METHOD) -> operationId for every operation of an OpenAPI document."""
    return {
        (path, method.upper()): item[method].get("operationId")
        for path, item in document.get("paths", {}).items()
        for method in HTTP_METHODS
        if method in item
    }


def diff(contract: dict, generated: dict) -> list[str]:
    """Differences of `generated` against `contract`, one line each; empty when they agree."""
    expected, actual = operations(contract), operations(generated)
    problems = [f"missing: {method} {path}" for (path, method) in sorted(expected.keys() - actual.keys())]
    problems += [f"unexpected: {method} {path}" for (path, method) in sorted(actual.keys() - expected.keys())]
    for key in sorted(expected.keys() & actual.keys()):
        if expected[key] != actual[key]:
            problems.append(f"operationId of {key[1]} {key[0]}: expected {expected[key]!r}, got {actual[key]!r}")
    return problems
