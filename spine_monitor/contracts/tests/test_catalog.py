"""Catálogo de features como módulo de runtime: carga, expansión de plantillas, unidades e ids conocidos."""

import json
from pathlib import Path
from re import fullmatch

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from spine_contracts.catalog import (
    UnknownFeatureIdError,
    all_feature_ids,
    expand_ids,
    expand_template,
    is_known,
    load_catalog,
    placeholders,
    ranges,
    risk_direction,
    unit_for,
)


def test_load_catalog_returns_entries():
    catalog = load_catalog()
    assert catalog["entries"]
    assert all("feature_id" in entry and "unit" in entry for entry in catalog["entries"])


def test_placeholders_lists_names_in_order():
    assert placeholders("emg.{muscle}.{side}.rms_mv") == ["muscle", "side"]
    assert placeholders("a.b.c") == []


def test_expand_template_without_placeholders_is_identity():
    assert expand_template("a.b.c") == ["a.b.c"]


def test_expand_template_covers_cartesian_product():
    ids = expand_template("emg.{muscle}.{side}.rms_mv")
    assert len(ids) == len(set(ids)) > 2
    assert all(i.startswith("emg.") and i.endswith(".rms_mv") for i in ids)
    assert any(".left." in i for i in ids) and any(".right." in i for i in ids)


def test_expand_ids_takes_an_entry():
    entry = load_catalog()["entries"][0]
    assert expand_ids(entry) == expand_template(entry["feature_id"])


def test_all_feature_ids_is_unique_and_is_the_union_of_entries():
    ids = all_feature_ids()
    assert len(ids) == len(set(ids))
    assert set(ids) == {i for entry in load_catalog()["entries"] for i in expand_ids(entry)}


def test_unit_for_concrete_id():
    concrete = expand_template("emg.{muscle}.{side}.rms_mv")[0]
    assert unit_for(concrete) == "mV"


def test_unknown_id_raises_clear_key_error():
    with pytest.raises(UnknownFeatureIdError, match="nope.nothing") as info:
        unit_for("nope.nothing")
    assert isinstance(info.value, KeyError)


def test_is_known():
    assert is_known(expand_template("emg.{muscle}.{side}.rms_mv")[0])
    assert not is_known("emg.{muscle}.{side}.rms_mv")  # la plantilla sin expandir no es un id concreto
    assert not is_known("nope.nothing")


def test_features_removed_by_clinical_decision_stay_out_of_the_catalog():
    # Aguado retiró estas features; no deben volver al catálogo (ni como plantilla ni como id concreto)
    removed = {
        "imu.l3.sagittal.smoothness_sparc",
        "imu.t9.sagittal.smoothness_sparc",
        "symptoms.pain_nrs_7d_max",
        "symptoms.odi_pct",
    }
    assert removed.isdisjoint(all_feature_ids())
    assert not any(is_known(feature_id) for feature_id in removed)


def test_risk_direction_and_ranges_of_a_concrete_id_come_from_its_entry():
    entry = next(e for e in load_catalog()["entries"] if e["ranges"] is not None)
    concrete = expand_ids(entry)[0]
    assert risk_direction(concrete) == entry["risk_direction"]
    assert ranges(concrete) == entry["ranges"]


def test_ranges_is_none_when_the_entry_has_no_ranges():
    entry = next(e for e in load_catalog()["entries"] if e["ranges"] is None)
    assert ranges(expand_ids(entry)[0]) is None


@pytest.mark.parametrize("accessor", [risk_direction, ranges])
def test_risk_accessors_raise_for_unknown_and_unexpanded_ids(accessor):
    with pytest.raises(UnknownFeatureIdError, match="nope.nothing"):
        accessor("nope.nothing")
    with pytest.raises(UnknownFeatureIdError):
        accessor("emg.{muscle}.{side}.rms_mv")


@pytest.mark.property
@given(data=st.data())
def test_every_expanded_id_resolves_risk_direction_and_ranges_of_its_entry(data):
    entry = data.draw(st.sampled_from(load_catalog()["entries"]))
    feature_id = data.draw(st.sampled_from(expand_ids(entry)))
    assert risk_direction(feature_id) == entry["risk_direction"]
    assert ranges(feature_id) == entry["ranges"]


@pytest.mark.property
@given(data=st.data())
def test_every_expanded_id_is_known_and_round_trips_to_its_entry_unit(data):
    entry = data.draw(st.sampled_from(load_catalog()["entries"]))
    feature_id = data.draw(st.sampled_from(expand_ids(entry)))
    assert is_known(feature_id)
    assert unit_for(feature_id) == entry["unit"]


# ---- protocol catalog -----------------------------------------------------------
# Fixed facts of each protocol (band, reps, rest, fit items) that the UI views quote. Only codes and
# numbers: the UI writes the words.

CONTRACTS_DIR = Path(__file__).resolve().parents[1]
PROTOCOL_CATALOG = CONTRACTS_DIR / "catalog" / "protocol-catalog.json"
PROTOCOL_CATALOG_SCHEMA = CONTRACTS_DIR / "catalog" / "protocol-catalog.schema.json"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _protocols() -> dict[str, dict]:
    return {entry["protocol_id"]: entry for entry in _load(PROTOCOL_CATALOG)["entries"]}


def _walk(node, key: str):
    if isinstance(node, dict):
        for k, value in node.items():
            if k == key and isinstance(value, str):
                yield value
            else:
                yield from _walk(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, key)


def test_protocol_catalog_matches_its_schema():
    schema = _load(PROTOCOL_CATALOG_SCHEMA)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_load(PROTOCOL_CATALOG))


def test_protocol_catalog_ids_are_unique_and_valid_protocol_ids():
    entries = _load(PROTOCOL_CATALOG)["entries"]
    ids = [entry["protocol_id"] for entry in entries]
    pattern = _load(CONTRACTS_DIR / "schemas" / "v1" / "common.schema.json")["$defs"]["protocol_id"]["pattern"]
    assert len(ids) == len(set(ids))
    assert all(fullmatch(pattern, i) for i in ids)


def test_protocol_catalog_fit_item_ids_are_unique_per_protocol():
    for protocol_id, entry in _protocols().items():
        ids = [item["id"] for item in entry["fit_items"]]
        assert len(ids) == len(set(ids)), protocol_id


def test_protocol_catalog_rejects_prose_and_missing_facts():
    validator = Draft202012Validator(_load(PROTOCOL_CATALOG_SCHEMA))
    catalog = _load(PROTOCOL_CATALOG)
    entry = catalog["entries"][0]
    for broken in (
        {**entry, "fit_items": [{"id": "skin", "label": "Clean the skin"}]},
        {**entry, "band": {"color": "blue"}},
        {key: value for key, value in entry.items() if key != "rest_seconds"},
        {key: value for key, value in entry.items() if key != "planned_sets"},
        {key: value for key, value in entry.items() if key != "posture"},
        {**entry, "total_reps": 0},
        {**entry, "planned_sets": 0},
        {**entry, "reps_per_set": 0},
        {**entry, "rest_between_sets_s": -1},
        {**entry, "grip": "fancy"},
        {**entry, "tempo": "2-0-2"},
        {**entry, "posture": "hanging"},
    ):
        assert not validator.is_valid({**catalog, "entries": [broken]}), broken


def test_every_protocol_id_used_in_the_examples_is_in_the_protocol_catalog():
    known = set(_protocols())
    for path in sorted((CONTRACTS_DIR / "examples" / "valid").rglob("*.json")):
        for protocol_id in _walk(_load(path), "protocol_id"):
            assert protocol_id in known, f"{path.name}: {protocol_id} not in protocol catalog"


def test_ui_session_example_quotes_its_protocol_entry():
    session = _load(CONTRACTS_DIR / "examples" / "valid" / "app" / "ui-session.json")
    entry = _protocols()[session["protocol_id"]]
    assert session["band"] == entry["band"]
    assert session["total_reps"] == entry["total_reps"]
    assert session["rest_seconds"] == entry["rest_seconds"]
    assert session["planned_sets"] == entry["planned_sets"]
    assert session["reps_per_set"] == entry["reps_per_set"]
    assert session["rest_between_sets_s"] == entry["rest_between_sets_s"]
    assert session["fit_items"] == [item["id"] for item in entry["fit_items"]]


# ---- the standardised exercise protocol (issue #12, Aguado) ------------------------------------
# Standing lat pulldown, green elastic band (nominal 25 kg), 4 sets of 15 repetitions, pronated grip,
# moderate tempo. The spine_check that brackets it is also done standing.


def test_lat_pulldown_protocol_states_the_standardised_facts():
    entry = _protocols()["lat_pulldown_standing_v1"]
    assert entry["band"] == {"color": "green", "nominal_kg": 25}
    assert (entry["planned_sets"], entry["reps_per_set"], entry["total_reps"]) == (4, 15, 60)
    assert entry["grip"] == "pronated"
    assert entry["tempo"] == "moderate"
    assert entry["posture"] == "standing"
    assert isinstance(entry["rest_between_sets_s"], int) and entry["rest_between_sets_s"] > 0
    assert "Aguado" in entry["notes"], "the nominal rest between sets is a placeholder until Aguado fixes it"


def test_protocol_total_reps_is_sets_times_reps_per_set_or_all_null():
    for protocol_id, entry in _protocols().items():
        sets, reps, total = entry["planned_sets"], entry["reps_per_set"], entry["total_reps"]
        if None in (sets, reps, total):
            assert sets is reps is total is None, f"{protocol_id}: sets, reps per set and total are null together"
        else:
            assert total == sets * reps, protocol_id


def test_spine_check_protocol_is_standing_and_has_no_repetitions():
    entry = _protocols()["spine_length_rest_v1"]
    assert entry["posture"] == "standing"
    assert entry["band"] is None and entry["planned_sets"] is None and entry["reps_per_set"] is None
    assert entry["grip"] is None and entry["tempo"] is None and entry["rest_between_sets_s"] is None


def test_protocol_total_reps_is_the_session_planned_reps_in_the_examples():
    entry = _protocols()["lat_pulldown_standing_v1"]
    session = _load(CONTRACTS_DIR / "examples" / "valid" / "domain" / "session.exercise.json")
    assert (session["planned_sets"], session["reps_per_set"], session["planned_reps"]) == (
        entry["planned_sets"],
        entry["reps_per_set"],
        entry["total_reps"],
    )
    assert session["load_declared"]["nominal_kg"] == entry["band"]["nominal_kg"]
    assert session["load_declared"]["band_id"] == entry["band"]["color"]


def test_whole_spine_cord_and_t9_imu_features_are_in_the_feature_catalog():
    # the cord spans C3-C4 to L5-S1 (full_spine) and the IMU sits at T9
    for feature_id in ("stretch.full_spine.peak_elongation_mm", "stretch.full_spine.elongation_mm", "imu.t9.sagittal.rom_deg"):
        assert is_known(feature_id), feature_id
    assert is_known("imu.c3.sagittal.rom_deg") and is_known("imu.c4.sagittal.rom_deg")


def _feature_notes(feature_id: str) -> str:
    return next(entry for entry in load_catalog()["entries"] if entry["feature_id"] == feature_id).get("notes", "")


def test_mvc_features_say_the_mvc_procedure_is_pending():
    for feature_id in ("emg.{muscle}.{side}.rms_pct_mvc", "emg.{muscle}.{side}.peak_pct_mvc"):
        assert "PENDIENTE Aguado" in _feature_notes(feature_id), feature_id


def test_stretch_notes_state_what_350_ohm_means():
    notes = _feature_notes("stretch.{spine_region}.elongation_mm")
    assert "350 ohm" in notes and "per inch" in notes and "not a 2.54 cm deformation" in notes


def test_symmetry_and_imu_notes_reflect_the_standard_protocol():
    assert "#13" in _feature_notes("emg.{muscle}.symmetry_index_pct")
    assert "T9" in _feature_notes("imu.{spine_level}.{anatomical_plane}.rom_deg")


def test_example_mission_protocol_matches_the_protocol_catalog():
    # The simulator mission repeats the catalog's protocol facts; changing one without the other must fail here
    # (docs/protocolo-ejercicio.md lists every place to edit when Aguado validates a value).
    import spine_physiology
    import yaml

    # Via the installed package: mutmut stages the tests in a tree without `physiology/` next to `contracts/`.
    mission_file = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
    mission = yaml.safe_load(mission_file.read_text(encoding="utf-8"))
    mission_protocol = mission["protocols"]["lat_pulldown_standing_v1"]
    entry = _protocols()["lat_pulldown_standing_v1"]
    assert mission_protocol["planned_sets"] == entry["planned_sets"]
    assert mission_protocol["reps_per_set"] == entry["reps_per_set"]
    assert mission_protocol["rest_between_sets_s"] == entry["rest_between_sets_s"]
    assert mission_protocol["load"]["nominal_kg"] == entry["band"]["nominal_kg"]
    assert mission_protocol["load"]["band_id"] == entry["band"]["color"]
