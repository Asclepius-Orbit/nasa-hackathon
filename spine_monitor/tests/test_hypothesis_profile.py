"""El perfil de Hypothesis del conftest raíz desactiva el deadline (runners lentos de CI)."""

from hypothesis import settings


def test_hypothesis_deadline_is_disabled():
    assert settings().deadline is None
