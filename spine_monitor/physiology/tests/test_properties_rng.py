import re

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from spine_physiology import rng

pytestmark = pytest.mark.property

seeds = st.integers(min_value=0, max_value=2**63)
keys = st.lists(st.one_of(st.text(max_size=12), st.integers(min_value=-1000, max_value=1000)), max_size=5)
UUID_V4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


@given(seed=seeds, ks=keys)
def test_a_branch_is_reproducible(seed, ks):
    assert rng.generator(seed, *ks).random(5).tolist() == rng.generator(seed, *ks).random(5).tolist()


# Un entero y su texto no deben colisionar (el tag i:/s: los separa); Hypothesis casi nunca lo genera solo.
@example(seed=0, a=[1], b=["1"])
@given(seed=seeds, a=keys, b=keys)
def test_different_branches_give_different_streams(seed, a, b):
    if a == b:
        return
    assert rng.generator(seed, *a).random(8).tolist() != rng.generator(seed, *b).random(8).tolist()


@given(seed=seeds, ks=keys)
def test_derived_uuids_are_valid_v4_and_stable(seed, ks):
    value = rng.derive_uuid(seed, *ks)
    assert UUID_V4.fullmatch(value)
    assert value == rng.derive_uuid(seed, *ks)


@given(seed=seeds, ks=keys)
def test_boot_id_fits_32_bits(seed, ks):
    assert 0 <= rng.derive_boot_id(seed, *ks) < 2**32
