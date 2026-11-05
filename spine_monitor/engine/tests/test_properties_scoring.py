import math
from types import SimpleNamespace
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import BaselineEntry
from spine_engine.deviation.scoring import RobustZScorer

pytestmark = pytest.mark.property

finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
spread = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)


def entry(median: float, mad: float, std: float) -> BaselineEntry:
    # RobustZScorer solo lee median, mad y std; un SimpleNamespace basta y evita rellenar el resto del modelo.
    return cast(BaselineEntry, SimpleNamespace(median=median, mad=mad, std=std))


@given(value=finite, median=finite, mad=spread, std=spread)
def test_score_is_always_finite(value, median, mad, std):
    assert math.isfinite(RobustZScorer().score(value, entry(median, mad, std)))


@given(median=finite, mad=spread, std=spread)
def test_the_median_scores_zero(median, mad, std):
    assert RobustZScorer().score(median, entry(median, mad, std)) == 0.0


@given(a=finite, b=finite, median=finite, mad=spread, std=spread)
def test_score_is_monotonic_in_the_value(a, b, median, mad, std):
    scorer, e = RobustZScorer(), entry(median, mad, std)
    low, high = sorted((a, b))
    assert scorer.score(low, e) <= scorer.score(high, e)


@given(value=finite, median=finite, mad=st.floats(min_value=1e-3, max_value=1e6), std=spread)
def test_score_uses_the_mad_when_it_is_not_degenerate(value, median, mad, std):
    expected = (value - median) / (1.4826 * mad)
    assert RobustZScorer().score(value, entry(median, mad, std)) == pytest.approx(expected)
