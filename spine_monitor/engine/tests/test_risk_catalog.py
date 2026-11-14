"""Riesgo con sentido y rangos del catálogo. Nada de ids ni umbrales clínicos: salen de un catálogo de
ejemplo (fixture) o del catálogo real, que el motor solo lee."""

import math
from collections.abc import Mapping
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import AssessmentDomain
from spine_contracts import catalog as real_catalog
from spine_engine import risk as risk_module
from spine_engine.interfaces import Deviation, Direction
from spine_engine.risk import DeviationRiskModel

WEIGHTS = dict.fromkeys(AssessmentDomain, 1.0)
LOW, HIGH, EITHER, NONE_DIR, UNSPEC, UNKNOWN = "x.low", "x.high", "x.either", "x.none", "x.unspec", "x.unknown"
RANGED_LOW, RANGED_HIGH = "x.ranged_low", "x.ranged_high"
# stretch.* ids give a domain (domain_of); the fixture ids reuse a real domain prefix so they are scored.
PREFIX = "stretch.full_spine."


class DictCatalog:
    """CatalogView de ejemplo, respaldado por diccionarios. Lanza KeyError con ids desconocidos, como el real."""

    def __init__(self, directions: Mapping[str, str], ranges: Mapping[str, dict[str, Any] | None]) -> None:
        self._directions = directions
        self._ranges = ranges

    def risk_direction(self, feature_id: str) -> str:
        return self._directions[feature_id.removeprefix(PREFIX)]

    def ranges(self, feature_id: str) -> dict[str, Any] | None:
        return self._ranges[feature_id.removeprefix(PREFIX)]


def band(lo: float | None, hi: float | None) -> dict[str, float | None]:
    return {"min": lo, "max": hi}


# Bounds 0 / 10 / 20 / 30, shared between adjacent bands as in the real catalog.
LOW_RANGES = {"normal": band(20.0, None), "alert": band(10.0, 20.0), "critical": band(None, 10.0), "source": "fixture"}
HIGH_RANGES = {"normal": band(None, 10.0), "alert": band(10.0, 20.0), "critical": band(20.0, None), "source": "fixture"}
DIRECTIONS = {
    LOW: "lower_is_worse",
    HIGH: "higher_is_worse",
    EITHER: "either_is_worse",
    NONE_DIR: "none",
    UNSPEC: "unspecified",
    RANGED_LOW: "lower_is_worse",
    RANGED_HIGH: "higher_is_worse",
}
CATALOG = DictCatalog(DIRECTIONS, {**dict.fromkeys(DIRECTIONS), RANGED_LOW: LOW_RANGES, RANGED_HIGH: HIGH_RANGES})


def fid(name: str) -> str:
    return PREFIX + name


def make(catalog: Any = CATALOG) -> DeviationRiskModel:
    return DeviationRiskModel(WEIGHTS, persistence_points=1, saturation=2.0, catalog=catalog)


def dev(name: str, z: float, direction: Direction | None, value: float = 15.0) -> Deviation:
    return Deviation(fid(name), value, abs(z), z, sustained=True, direction=direction, n_points=4)


def prefixed_model() -> DeviationRiskModel:
    return make()


def raw(deviation: Deviation, catalog: Any = None) -> float:
    """Suma cruda de una única desviación: score = 1 - exp(-raw / 2)."""
    result = make(CATALOG if catalog is None else catalog).score([deviation], 1.0)
    return -2.0 * math.log1p(-result.score) if result.score < 1.0 else math.inf


def no_catalog_score(deviation: Deviation) -> float:
    return DeviationRiskModel(WEIGHTS, persistence_points=1, saturation=2.0).score([deviation], 1.0).score


# --- direction -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "direction", "counts"),
    [
        (LOW, "down", True),
        (LOW, "up", False),
        (HIGH, "up", True),
        (HIGH, "down", False),
        (EITHER, "up", True),
        (EITHER, "down", True),
        (UNSPEC, "up", True),
        (UNSPEC, "down", True),
        (NONE_DIR, "up", False),
        (NONE_DIR, "down", False),
    ],
)
def test_direction_decides_whether_a_deviation_counts(name, direction, counts):
    z = -3.0 if direction == "down" else 3.0
    deviation = dev(name, z, direction)

    result = prefixed_model().score([deviation], 1.0)

    assert (result.score > 0) is counts
    assert bool(result.drivers) is counts
    if counts:
        assert result.score == pytest.approx(no_catalog_score(deviation))


def test_the_sign_of_z_stands_in_when_the_direction_is_missing():
    m = prefixed_model()

    assert m.score([dev(LOW, -3.0, None)], 1.0).score > 0
    assert m.score([dev(LOW, 3.0, None)], 1.0).score == 0.0
    assert m.score([dev(HIGH, 3.0, None)], 1.0).score > 0
    assert m.score([dev(HIGH, -3.0, None)], 1.0).score == 0.0


def test_an_explicit_direction_wins_over_the_sign_of_z():
    # A sustained rise whose z is negative is still a rise: for higher_is_worse it counts.
    assert prefixed_model().score([dev(HIGH, -3.0, "up")], 1.0).score > 0


def test_a_good_direction_driver_is_dropped_but_the_bad_one_keeps_the_whole_share():
    result = prefixed_model().score([dev(LOW, 3.0, "up"), dev(HIGH, 2.0, "up")], 1.0)

    assert [d.feature_id for d in result.drivers] == [fid(HIGH)]
    assert result.drivers[0].contribution == pytest.approx(result.score)


# --- unknown ids / no catalog ---------------------------------------------------------------------------


def test_an_id_unknown_to_the_catalog_falls_back_to_the_generic_z():
    deviation = dev("unknown", 3.0, "up")

    assert prefixed_model().score([deviation], 1.0).score == pytest.approx(no_catalog_score(deviation))
    assert prefixed_model().score([dev("unknown", -3.0, "down")], 1.0).score > 0


def test_without_a_catalog_the_model_behaves_as_before():
    assert no_catalog_score(dev(LOW, 3.0, "up")) > 0


def test_a_feature_without_ranges_keeps_the_generic_model_whatever_its_value():
    base = raw(dev(EITHER, 3.0, "up", value=0.0))

    assert raw(dev(EITHER, 3.0, "up", value=1e9)) == pytest.approx(base)


# --- ranges ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value", "level"),
    [
        (RANGED_LOW, 25.0, "normal"),
        (RANGED_LOW, 15.0, "alert"),
        (RANGED_LOW, 5.0, "critical"),
        (RANGED_HIGH, 5.0, "normal"),
        (RANGED_HIGH, 15.0, "alert"),
        (RANGED_HIGH, 25.0, "critical"),
    ],
)
def test_the_range_a_value_falls_in_scales_the_contribution(name, value, level):
    direction: Direction = "down" if name == RANGED_LOW else "up"
    z = -3.0 if direction == "down" else 3.0
    generic = raw(dev(EITHER, 3.0, "up"))  # |z| = 3, same weights, no ranges

    assert raw(dev(name, z, direction, value)) == pytest.approx(generic * risk_module.RANGE_LEVEL_WEIGHTS[level])


def test_the_levels_are_ordered_by_severity():
    weights = risk_module.RANGE_LEVEL_WEIGHTS

    assert weights["normal"] < weights["alert"] < weights["critical"]
    assert set(weights) == {"normal", "alert", "critical"}


@pytest.mark.parametrize(
    ("value", "level"),
    [
        (10.0, "critical"),  # shared edge critical/alert: the worse level wins
        (math.nextafter(10.0, 20.0), "alert"),
        (math.nextafter(10.0, 0.0), "critical"),
        (20.0, "alert"),  # shared edge alert/normal
        (math.nextafter(20.0, 30.0), "normal"),
        (math.nextafter(20.0, 0.0), "alert"),
    ],
)
def test_bounds_are_inclusive_and_the_worse_level_wins_on_a_shared_edge(value, level):
    generic = raw(dev(EITHER, 3.0, "up"))

    assert raw(dev(RANGED_LOW, -3.0, "down", value)) == pytest.approx(generic * risk_module.RANGE_LEVEL_WEIGHTS[level])


def test_a_value_inside_no_range_keeps_the_generic_weight():
    gaps = DictCatalog(
        {"g": "higher_is_worse"},
        {"g": {"normal": band(0.0, 1.0), "alert": band(5.0, 6.0), "critical": band(9.0, 10.0), "source": "fixture"}},
    )
    generic = raw(dev(EITHER, 3.0, "up"))

    assert raw(dev("g", 3.0, "up", value=3.0), catalog=gaps) == pytest.approx(generic)


def test_a_range_with_a_missing_level_only_uses_the_levels_it_has():
    partial = DictCatalog({"p": "higher_is_worse"}, {"p": {"critical": band(20.0, None), "source": "fixture"}})
    generic = raw(dev(EITHER, 3.0, "up"))

    assert raw(dev("p", 3.0, "up", value=25.0), catalog=partial) == pytest.approx(generic * risk_module.RANGE_LEVEL_WEIGHTS["critical"])
    assert raw(dev("p", 3.0, "up", value=5.0), catalog=partial) == pytest.approx(generic)


def test_ranges_do_not_revive_a_deviation_in_the_good_direction():
    assert prefixed_model().score([dev(RANGED_LOW, 3.0, "up", value=5.0)], 1.0).score == 0.0


# --- real catalog ----------------------------------------------------------------------------------------

REAL_IDS = real_catalog.all_feature_ids()
REAL_DOMAIN_IDS = [i for i in REAL_IDS if risk_module.domain_of(i) is not None]


def real_model() -> DeviationRiskModel:
    return DeviationRiskModel(WEIGHTS, persistence_points=1, saturation=2.0, catalog=real_catalog)


def with_direction(*directions: str) -> list[str]:
    return [i for i in REAL_DOMAIN_IDS if real_catalog.risk_direction(i) in directions]


def test_the_real_catalog_gives_the_model_something_to_check():
    assert with_direction("lower_is_worse")
    assert with_direction("higher_is_worse", "none")


@pytest.mark.parametrize("feature_id", with_direction("lower_is_worse", "higher_is_worse", "none"))
def test_in_the_real_catalog_a_good_direction_deviation_never_raises_the_risk(feature_id):
    direction = real_catalog.risk_direction(feature_id)
    model = real_model()
    baseline = model.score([], 1.0).score
    # "none" has no bad direction: both ways are good.
    moves: list[Direction] = ["up", "down"] if direction == "none" else ["up" if direction == "lower_is_worse" else "down"]
    for move in moves:
        for value in (0.0, 1.0, 1e6, -1e6):
            z = 4.0 if move == "up" else -4.0
            deviation = Deviation(feature_id, value, 4.0, z, sustained=True, direction=move, n_points=10)

            assert model.score([deviation], 1.0).score == baseline


@pytest.mark.parametrize("feature_id", with_direction("lower_is_worse", "higher_is_worse"))
def test_in_the_real_catalog_a_bad_direction_deviation_still_scores(feature_id):
    bad: Direction = "down" if real_catalog.risk_direction(feature_id) == "lower_is_worse" else "up"
    z = -4.0 if bad == "down" else 4.0
    deviation = Deviation(feature_id, 0.0, 4.0, z, sustained=True, direction=bad, n_points=10)

    assert real_model().score([deviation], 1.0).score > 0.0


@pytest.mark.parametrize("feature_id", with_direction("either_is_worse", "unspecified"))
def test_in_the_real_catalog_an_undirected_feature_counts_both_ways(feature_id):
    up = Deviation(feature_id, 0.0, 4.0, 4.0, sustained=True, direction="up", n_points=10)
    down = Deviation(feature_id, 0.0, 4.0, -4.0, sustained=True, direction="down", n_points=10)

    assert real_model().score([up], 1.0).score > 0.0
    assert real_model().score([down], 1.0).score > 0.0


# --- properties ------------------------------------------------------------------------------------------

z_mag = st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False)
values = st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False)
names = st.sampled_from([LOW, HIGH, RANGED_LOW, RANGED_HIGH, EITHER, UNSPEC, NONE_DIR])
bad_dir: dict[str, Direction] = {LOW: "down", RANGED_LOW: "down", HIGH: "up", RANGED_HIGH: "up"}


@pytest.mark.property
@given(name=st.sampled_from([LOW, HIGH, RANGED_LOW, RANGED_HIGH]), mag=z_mag, value=values)
def test_a_good_direction_deviation_contributes_nothing(name, mag, value):
    good: Direction = "up" if bad_dir[name] == "down" else "down"
    z = mag if good == "up" else -mag
    other = dev(EITHER, 1.0, "up")
    m = prefixed_model()

    assert m.score([dev(name, z, good, value), other], 1.0).score == m.score([other], 1.0).score


@pytest.mark.property
@given(name=st.sampled_from([LOW, HIGH, RANGED_LOW, RANGED_HIGH]), a=z_mag, b=z_mag, value=values)
def test_risk_is_monotone_in_the_magnitude_in_the_bad_direction(name, a, b, value):
    lo, hi = sorted((a, b))
    bad = bad_dir[name]
    sign = -1.0 if bad == "down" else 1.0
    m = prefixed_model()

    assert m.score([dev(name, sign * lo, bad, value)], 1.0).score <= m.score([dev(name, sign * hi, bad, value)], 1.0).score


@pytest.mark.property
@given(name=names, z=st.floats(min_value=-50, max_value=50, allow_nan=False), value=values, direction=st.sampled_from(["up", "down", None]))
def test_the_catalog_never_raises_the_score_above_a_critical_weighted_generic(name, z, value, direction):
    deviation = dev(name, z, direction, value)
    ceiling = no_catalog_score(deviation)

    result = prefixed_model().score([deviation], 1.0)

    assert 0.0 <= result.score <= 1.0
    assert result.score <= 1 - (1 - ceiling) ** max(risk_module.RANGE_LEVEL_WEIGHTS.values()) + 1e-12
