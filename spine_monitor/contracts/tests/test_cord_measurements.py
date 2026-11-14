"""Raw cord measurements are observations, not a calibration curve."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

CATALOG = Path(__file__).resolve().parents[1] / "catalog"


def test_experiment_preserves_measurements_and_distinct_setups():
    data = json.loads((CATALOG / "cord-measurements.json").read_text(encoding="utf-8"))
    schema = json.loads((CATALOG / "cord-measurements.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate(data)
    assert data["bench"]["contact_spacing_mm"] == 100
    assert data["back"]["contact_spacing_mm"] == 500
    assert data["back"]["straight_ohm"] == 4500
    assert data["back"]["max_flexion_ohm"] == 5650
    assert [(x["whole_cord_length_mm"], x["resistance_ohm"]) for x in data["bench"]["measurements"]] == [
        (1000, [630, 780, 795]),
        (1100, [1020, 1165, 1125]),
        (1200, [1110, 1255, 1260]),
        (1300, [1270, 1320, 1300]),
        (1400, [1350, 1355, 1350]),
    ]
    assert not validator.is_valid({**data, "bench": {**data["bench"], "contact_spacing_mm": 0}})
    invalid = {**data, "bench": {**data["bench"], "measurements": [{"whole_cord_length_mm": 1000, "resistance_ohm": [630, 780]}]}}
    assert not validator.is_valid(invalid)
