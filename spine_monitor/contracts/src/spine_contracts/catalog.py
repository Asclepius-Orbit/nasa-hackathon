"""Catálogo de features como módulo de runtime.

Carga `feature-catalog.json`, expande los ids con plantilla (`emg.{muscle}.{side}.rms_mv`) a ids concretos
con los enums de `common.schema.json` y resuelve la unidad, la dirección de riesgo y los rangos de un id
concreto. No contiene valores clínicos: viven en el JSON (provisionales hasta que Aguado los valide) y este
módulo solo los lee.

El catálogo se lee del repositorio (`contracts/catalog`) al ejecutar desde el workspace de uv, y de la copia
incluida en el wheel (`spine_contracts/catalog`) al instalarlo por separado, igual que los esquemas.
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
    """El feature_id no corresponde a ninguna entrada (expandida) del catálogo."""

    def __init__(self, feature_id: str) -> None:
        super().__init__(feature_id)
        self.feature_id = feature_id

    def __str__(self) -> str:
        return f"unknown feature_id: {self.feature_id!r}"


@cache
def load_catalog() -> Catalog:
    """Catálogo de features tal como está en `feature-catalog.json` (compartido: no lo mutes)."""
    path = CATALOG_DIR / "feature-catalog.json"
    if not path.is_file():
        raise FileNotFoundError(f"feature catalog not found at {path}; run from the repository workspace or install the wheel")
    return cast(Catalog, json.loads(path.read_text(encoding="utf-8")))


@cache
def _common_defs() -> dict[str, Any]:
    common = json.loads((SCHEMAS_DIR / "common.schema.json").read_text(encoding="utf-8"))
    return cast(dict[str, Any], common["$defs"])


def placeholders(template: str) -> list[str]:
    """Nombres de los marcadores `{nombre}` de un id con plantilla, en orden."""
    return [part[1:-1] for part in template.split(".") if part.startswith("{")]


def expand_template(template: str) -> list[str]:
    """Ids concretos de una plantilla: producto cartesiano de los enums de `common.schema.json`."""
    names = placeholders(template)
    defs = _common_defs()
    values = [cast(list[str], defs[name]["enum"]) for name in names]
    return [template.format(**dict(zip(names, combo, strict=True))) for combo in product(*values)]


def expand_ids(entry: CatalogEntry) -> list[str]:
    """Ids concretos de una entrada del catálogo."""
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
    """Todos los ids concretos del catálogo, en orden de entrada."""
    return list(_entries_by_id())


def is_known(feature_id: str) -> bool:
    """True si `feature_id` es un id concreto del catálogo (una plantilla sin expandir no lo es)."""
    return feature_id in _entries_by_id()


def unit_for(feature_id: str) -> str:
    """Unidad de la entrada cuya plantilla produce `feature_id`; lanza `UnknownFeatureIdError` si no existe."""
    return str(_entry_for(feature_id)["unit"])


def risk_direction(feature_id: str) -> str:
    """`risk_direction` de la entrada de `feature_id` (puede ser `unspecified`); `UnknownFeatureIdError` si no existe."""
    return str(_entry_for(feature_id)["risk_direction"])


def ranges(feature_id: str) -> dict[str, Any] | None:
    """Rangos normal/alert/critical/source de la entrada de `feature_id`, o None si no hay; `UnknownFeatureIdError` si no existe."""
    value = _entry_for(feature_id)["ranges"]
    return None if value is None else cast(dict[str, Any], value)


@cache
def load_calibration_defaults() -> dict[str, Any]:
    """Defaults provisionales de calibración (`calibration-defaults.json`): curva del cordón, reposo y ventana MVC.

    Los lee el gateway y los comprueban los tests contra la misión de ejemplo (compartido: no lo mutes).
    """
    path = CATALOG_DIR / "calibration-defaults.json"
    if not path.is_file():
        raise FileNotFoundError(f"calibration defaults not found at {path}; run from the repository workspace or install the wheel")
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
