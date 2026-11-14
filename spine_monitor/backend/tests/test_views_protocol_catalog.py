"""protocol_catalog: lookup of the fixed facts of a protocol by protocol_id."""

from monitor.views.protocol_catalog import planned_sets_for, protocol_entry, reps_per_set_for, total_reps_for


def test_exercise_protocol_has_reps() -> None:
    assert total_reps_for("lat_pulldown_standing_v1") == 60


def test_passive_protocol_has_no_reps() -> None:
    assert total_reps_for("spine_length_rest_v1") is None


def test_unknown_protocol_has_no_reps_instead_of_a_default() -> None:
    assert total_reps_for("no_such_protocol_v9") is None
    assert protocol_entry("no_such_protocol_v9") is None


def test_entry_is_the_catalog_entry() -> None:
    entry = protocol_entry("lat_pulldown_standing_v1")
    assert entry is not None
    assert entry["band"] == {"color": "green", "nominal_kg": 25}


def test_exercise_protocol_is_made_of_sets() -> None:
    assert (planned_sets_for("lat_pulldown_standing_v1"), reps_per_set_for("lat_pulldown_standing_v1")) == (4, 15)


def test_passive_and_unknown_protocols_have_no_sets() -> None:
    for protocol_id in ("spine_length_rest_v1", "no_such_protocol_v9"):
        assert (planned_sets_for(protocol_id), reps_per_set_for(protocol_id)) == (None, None)


def test_entry_has_the_rest_between_sets() -> None:
    entry = protocol_entry("lat_pulldown_standing_v1")
    assert entry is not None
    assert entry["rest_between_sets_s"] == 30
