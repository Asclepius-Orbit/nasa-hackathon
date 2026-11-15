"""The Hypothesis profile of the root conftest disables the deadline (slow CI runners)."""

from hypothesis import settings


def test_hypothesis_deadline_is_disabled():
    assert settings().deadline is None
