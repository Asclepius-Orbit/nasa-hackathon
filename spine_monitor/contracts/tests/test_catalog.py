"""Catálogo de features como módulo de runtime: carga, expansión de plantillas, unidades e ids conocidos."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts.catalog import (
    UnknownFeatureIdError,
    all_feature_ids,
    expand_ids,
    expand_template,
    is_known,
    load_catalog,
    placeholders,
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


@pytest.mark.property
@given(data=st.data())
def test_every_expanded_id_is_known_and_round_trips_to_its_entry_unit(data):
    entry = data.draw(st.sampled_from(load_catalog()["entries"]))
    feature_id = data.draw(st.sampled_from(expand_ids(entry)))
    assert is_known(feature_id)
    assert unit_for(feature_id) == entry["unit"]
