"""Full-fidelity validation against the JSON Schemas.

The generated Pydantic models check structure, types and ranges, but not the conditional rules of the
schemas (an exercise session needs a declared load, a derived cord length needs its calibration, a
required escalation needs an urgency...). Use `validate` at the boundaries where data enters the
system (gateway ingestion, engine API) when those rules matter.

The schemas are read from the repository (`contracts/schemas/v1`) when running from the uv workspace,
and from the copy bundled in the wheel (`spine_contracts/schemas/v1`) when installed on its own.
"""

import json
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

_BUNDLED_DIR = Path(__file__).resolve().parent / "schemas" / "v1"
_REPO_DIR = Path(__file__).resolve().parents[2] / "schemas" / "v1"
SCHEMAS_DIR = _BUNDLED_DIR if _BUNDLED_DIR.is_dir() else _REPO_DIR


@cache
def _registry() -> Registry:
    if not SCHEMAS_DIR.is_dir():
        raise FileNotFoundError(f"JSON Schemas not found at {SCHEMAS_DIR}; run from the repository workspace or install the wheel")
    registry = Registry()
    for path in sorted(SCHEMAS_DIR.rglob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema, DRAFT202012))
    return registry


@cache
def validator(name: str) -> Draft202012Validator:
    """Validator for a schema name such as 'telemetry/stream-chunk' or 'domain/assessment'."""
    path = SCHEMAS_DIR / f"{name}.schema.json"
    if not path.is_file():
        raise KeyError(f"unknown schema: {name!r}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())


def errors(name: str, payload: object) -> list[str]:
    """Human-readable problems, one per violation ('pointer: message'); empty when valid."""
    found = sorted(validator(name).iter_errors(payload), key=lambda e: list(map(str, e.absolute_path)))
    return [f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in found]


def validate(name: str, payload: object) -> None:
    """Raise jsonschema.ValidationError if the payload violates the schema."""
    error = next(iter(validator(name).iter_errors(payload)), None)
    if error is not None:
        raise ValidationError(f"{name}: {error.message}", path=error.absolute_path, schema_path=error.absolute_schema_path)
