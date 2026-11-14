"""Regenerate the Pydantic models from the JSON Schemas.

The JSON Schemas in schemas/v1 are the source of truth; src/spine_contracts/models is generated
from them and must never be edited by hand. Run from the workspace root:

    uv run python contracts/scripts/generate_models.py

A test fails when the committed models differ from what this script produces.
"""

import shutil
import subprocess
import sys
from pathlib import Path

CONTRACTS_DIR = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = CONTRACTS_DIR / "schemas" / "v1"
MODELS_DIR = CONTRACTS_DIR / "src" / "spine_contracts" / "models"

ARGS = [
    "--input-file-type", "jsonschema",
    "--output-model-type", "pydantic_v2.BaseModel",
    # con* types (constr, conint...) instead of Field constraints: the Annotated style wraps every
    # constrained list item (ids, reasons) in a RootModel, and str(item) then gives "root='...'".
    "--no-use-annotated",
    "--use-standard-collections",
    "--use-union-operator",
    "--use-title-as-name",
    "--use-subclass-enum",
    "--capitalise-enum-members",
    "--collapse-root-models",
    "--reuse-model",
    "--disable-timestamp",
    "--formatters", "black", "isort",
]


def generate(output: Path = MODELS_DIR) -> None:
    if output.exists():
        shutil.rmtree(output)
    subprocess.run(
        [sys.executable, "-W", "ignore", "-m", "datamodel_code_generator", "--input", str(SCHEMAS_DIR), "--output", str(output), *ARGS],
        check=True,
    )


if __name__ == "__main__":
    generate()
    print(f"generated {MODELS_DIR}")
