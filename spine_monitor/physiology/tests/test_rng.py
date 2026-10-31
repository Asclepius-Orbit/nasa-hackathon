import re

import numpy as np
import pytest

from spine_physiology.rng import RngTree, derive_boot_id, derive_uuid, generator, seed_sequence

# Patrón `uuid` de common.schema.json.
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def draw(seed: int, *keys) -> np.ndarray:
    return generator(seed, *keys).standard_normal(64)


def test_same_branch_is_bit_exact():
    a = draw(7, "AST-01", 3, "lat_pulldown", "emg_lat_left", "signal")
    b = draw(7, "AST-01", 3, "lat_pulldown", "emg_lat_left", "signal")
    assert np.array_equal(a, b)


def test_branch_does_not_depend_on_other_draws():
    # Pedir otras ramas antes (en cualquier orden y cantidad) no cambia una rama.
    alone = draw(7, "AST-02", 9, "device")
    for day in range(5):
        draw(7, "AST-02", day, "device")
        generator(7, "AST-01", day).integers(0, 10, size=1000)
    assert np.array_equal(alone, draw(7, "AST-02", 9, "device"))


@pytest.mark.parametrize(
    "other",
    [
        (8, "AST-01", 3, "signal"),  # otra semilla
        (7, "AST-02", 3, "signal"),  # otro astronauta
        (7, "AST-01", 4, "signal"),  # otro día
        (7, "AST-01", 3, "faults"),  # otro propósito
        (7, "AST-01", 3),  # prefijo
        (7, "AST-01", 3, "signal", "x"),  # extensión
        (7, "AST-01", "3", "signal"),  # "3" no es 3
        (7, 3, "AST-01", "signal"),  # el orden importa
    ],
)
def test_different_branches_differ(other):
    assert not np.array_equal(draw(7, "AST-01", 3, "signal"), draw(*other))


def test_negative_int_keys_are_allowed():
    assert not np.array_equal(draw(1, -3), draw(1, 3))


@pytest.mark.parametrize("seed", [-1, 1.5, True, "7"])
def test_bad_seed_is_rejected(seed):
    with pytest.raises(ValueError):
        seed_sequence(seed)


@pytest.mark.parametrize("key", [1.0, None, True, ("a",)])
def test_bad_key_is_rejected(key):
    with pytest.raises(TypeError):
        generator(1, key)


def test_uuid_format_and_determinism():
    value = derive_uuid(42, "AST-01", 0, "am_spine_check")
    assert UUID.fullmatch(value)
    assert value[14] == "4"  # versión 4
    assert value[19] in "89ab"  # variante RFC 4122
    assert value == derive_uuid(42, "AST-01", 0, "am_spine_check")
    assert value != derive_uuid(42, "AST-01", 1, "am_spine_check")
    assert value != derive_uuid(43, "AST-01", 0, "am_spine_check")


def test_uuids_are_unique_over_many_keys():
    values = {derive_uuid(1, "AST-01", day, slot) for day in range(200) for slot in ("a", "b", "c")}
    assert len(values) == 600
    assert all(UUID.fullmatch(v) for v in values)


def test_boot_id_range_and_determinism():
    values = [derive_boot_id(5, "AST-01", day, 0) for day in range(500)]
    assert all(isinstance(v, int) and 0 <= v <= 2**32 - 1 for v in values)
    assert len(set(values)) == len(values)
    assert values[10] == derive_boot_id(5, "AST-01", 10, 0)
    assert derive_boot_id(5, "AST-01", 10, 0) != derive_boot_id(5, "AST-01", 10, 1)


def test_ids_are_independent_of_generators():
    # Derivar ids no consume ni coincide con el generador de la misma rama.
    keys = ("AST-01", 2, "boot")
    first = draw(3, *keys)
    derive_uuid(3, *keys)
    derive_boot_id(3, *keys)
    assert np.array_equal(first, draw(3, *keys))


def test_tree_shortcuts_match_functions():
    tree = RngTree(11)
    assert np.array_equal(tree.rng("a", 1).random(8), generator(11, "a", 1).random(8))
    assert tree.uuid("a") == derive_uuid(11, "a")
    assert tree.boot_id("a") == derive_boot_id(11, "a")
    with pytest.raises(ValueError):
        RngTree(-1)
