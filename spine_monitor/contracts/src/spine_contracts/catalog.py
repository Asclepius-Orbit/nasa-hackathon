"""Feature catalog as a runtime module.

Loads `feature-catalog.json`, expands templated ids (`emg.{muscle}.{side}.rms_mv`) to concrete ids
with the enums of `common.schema.json` and resolves the unit, risk direction and ranges of a concrete
id. It holds no clinical values: they live in the JSON (provisional until clinically validated) and this
module only reads them.

The catalog is read from the repository (`contracts/catalog`) when running from the uv workspace, and from the
copy bundled in the wheel (`spine_contracts/catalog`) when installed separately, like the schemas.
"""

import json
from collections.abc import Mapping
from functools import cache
from itertools import product
from pathlib import Path
from typing import Any, cast

from spine_contracts.validation import SCHEMAS_DIR

_BUNDLED_DIR = Path(__file__).resolve().parent / "catalog"
_REPO_DIR = Path(__file__).resolve().parents[2] / "catalog"
CATALOG_DIR = _BUNDLED_DIR if _BUNDLED_DIR.is_dir() else _REPO_DIR

type Catalog = dict[str, Any]
type CatalogEntry = Mapping[str, Any]


class UnknownFeatureIdError(KeyError):
    """The feature_id matches no (expanded) catalog entry."""

    def __init__(self, feature_id: str) -> None:
        super().__init__(feature_id)
        self.feature_id = feature_id

    def __str__(self) -> str:
        return f"unknown feature_id: {self.feature_id!r}"


@cache
def load_catalog() -> Catalog:
    """Feature catalog as in `feature-catalog.json` (shared: do not mutate it)."""
    path = CATALOG_DIR / "feature-catalog.json"
    if not path.is_file():
        raise FileNotFoundError(f"feature catalog not found at {path}; run from the repository workspace or install the wheel")
    return cast(Catalog, json.loads(path.read_text(encoding="utf-8")))


@cache
def _common_defs() -> dict[str, Any]:
    common = json.loads((SCHEMAS_DIR / "common.schema.json").read_text(encoding="utf-8"))
    return cast(dict[str, Any], common["$defs"])


def placeholders(template: str) -> list[str]:
    """Names of the `{name}` placeholders of a templated id, in order."""
    return [part[1:-1] for part in template.split(".") if part.startswith("{")]


def expand_template(template: str) -> list[str]:
    """Concrete ids of a template: Cartesian product of the enums of `common.schema.json`."""
    names = placeholders(template)
    defs = _common_defs()
    values = [cast(list[str], defs[name]["enum"]) for name in names]
    return [template.format(**dict(zip(names, combo, strict=True))) for combo in product(*values)]


def expand_ids(entry: CatalogEntry) -> list[str]:
    """Concrete ids of a catalog entry."""
    return expand_template(str(entry["feature_id"]))


@cache
def _entries_by_id() -> dict[str, CatalogEntry]:
    return {feature_id: entry for entry in load_catalog()["entries"] for feature_id in expand_ids(entry)}


def _entry_for(feature_id: str) -> CatalogEntry:
    try:
        return _entries_by_id()[feature_id]
    except KeyError:
        raise UnknownFeatureIdError(feature_id) from None


def all_feature_ids() -> list[str]:
    """All concrete ids of the catalog, in entry order."""
    return list(_entries_by_id())


def is_known(feature_id: str) -> bool:
    """True if `feature_id` is a concrete catalog id (an unexpanded template is not)."""
    return feature_id in _entries_by_id()


def unit_for(feature_id: str) -> str:
    """Unit of the entry whose template produces `feature_id`; raises `UnknownFeatureIdError` if it does not exist."""
    return str(_entry_for(feature_id)["unit"])


def risk_direction(feature_id: str) -> str:
    """`risk_direction` of the entry of `feature_id` (may be `unspecified`); `UnknownFeatureIdError` if it does not exist."""
    return str(_entry_for(feature_id)["risk_direction"])


def ranges(feature_id: str) -> dict[str, Any] | None:
    """normal/alert/critical/source ranges of the entry of `feature_id`, or None if there are none; `UnknownFeatureIdError` if it does not exist."""
    value = _entry_for(feature_id)["ranges"]
    return None if value is None else cast(dict[str, Any], value)


@cache
def load_calibration_defaults() -> dict[str, Any]:
    """Provisional calibration defaults (`calibration-defaults.json`): cord curve, rest point and MVC window.

    The gateway reads them and the tests check them against the example mission (shared: do not mutate it).
    """
    path = CATALOG_DIR / "calibration-defaults.json"
    if not path.is_file():
        raise FileNotFoundError(f"calibration defaults not found at {path}; run from the repository workspace or install the wheel")
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
