"""protocol_catalog: lookup of the fixed facts of a protocol by protocol_id."""

from monitor.views.protocol_catalog import protocol_entry, total_reps_for


def test_exercise_protocol_has_reps() -> None:
    assert total_reps_for("lat_pulldown_standing_v1") == 10


def test_passive_protocol_has_no_reps() -> None:
    assert total_reps_for("spine_length_rest_v1") is None


def test_unknown_protocol_has_no_reps_instead_of_a_default() -> None:
    assert total_reps_for("no_such_protocol_v9") is None
    assert protocol_entry("no_such_protocol_v9") is None


def test_entry_is_the_catalog_entry() -> None:
    entry = protocol_entry("lat_pulldown_standing_v1")
    assert entry is not None
    assert entry["band"] == {"color": "blue", "nominal_kg": 15}
