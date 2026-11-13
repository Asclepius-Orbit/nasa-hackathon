"""Defaults de calibración: contrato propio y coincidencia con la misión de ejemplo del simulador."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from spine_contracts.catalog import CATALOG_DIR, load_calibration_defaults

SCHEMA = CATALOG_DIR / "calibration-defaults.schema.json"


def test_calibration_defaults_match_their_schema():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(load_calibration_defaults())


def test_calibration_defaults_curve_rises_strictly_and_contains_the_rest_point():
    stretch = load_calibration_defaults()["stretch"]
    points = [(p["resistance_ohm"], p["length_mm"]) for p in stretch["curve"]]
    assert all(a[0] < b[0] and a[1] < b[1] for a, b in zip(points, points[1:], strict=False))
    assert (stretch["rest_resistance_ohm"], stretch["rest_length_mm"]) in points


def test_calibration_defaults_keep_the_pending_measurement_marks():
    defaults = load_calibration_defaults()
    assert "PENDIENTE medición Aguado" in defaults["stretch"]["notes"]
    assert "PENDIENTE medición Aguado" in defaults["emg_mvc"]["notes"]


def test_example_mission_calibration_matches_the_calibration_defaults():
    # One source: the gateway reads these defaults and the simulator mission repeats them; changing one without
    # the other must fail here.
    import spine_physiology
    import yaml

    # Via the installed package: mutmut stages the tests in a tree without `physiology/` next to `contracts/`.
    mission_file = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
    mission = yaml.safe_load(mission_file.read_text(encoding="utf-8"))
    defaults = load_calibration_defaults()
    assert mission["signals"]["stretch"]["curve"] == defaults["stretch"]["curve"]
    assert mission["calibration"]["emg_mvc"]["window_ms"] == defaults["emg_mvc"]["window_ms"]
    assert {c["astronaut_id"]: c for c in mission["crew"]}["AST-01"]["baseline"]["cord_rest_length_mm"] == defaults["stretch"]["rest_length_mm"]
