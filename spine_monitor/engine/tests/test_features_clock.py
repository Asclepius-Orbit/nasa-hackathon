"""device_ms -> UTC por boot_id con anclas ClockSync (#40)."""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import ClockSync
from spine_engine.features.clock import DeviceClock

T0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)


def anchor(device_ms: int, utc: datetime, *, boot: int = 7, uncertainty_ms: float = 10.0) -> ClockSync:
    return ClockSync.model_validate(
        {
            "schema_version": "1.0.0",
            "astronaut_id": "AST-01",
            "device_id": "wb-01",
            "boot_id": boot,
            "device_ms": device_ms,
            "utc": utc,
            "uncertainty_ms": uncertainty_ms,
            "method": "round_trip",
        }
    )


def ms(n: float) -> timedelta:
    return timedelta(milliseconds=n)


def test_boot_without_anchors_has_no_utc():
    clock = DeviceClock.from_syncs([anchor(0, T0, boot=1)])

    assert clock.fit(7) is None
    assert clock.to_utc(7, 1000) is None
    assert clock.uncertainty_ms(7) is None


def test_single_anchor_is_a_fixed_offset():
    clock = DeviceClock.from_syncs([anchor(1000, T0, uncertainty_ms=18)])

    assert clock.to_utc(7, 1000) == T0
    assert clock.to_utc(7, 3500) == T0 + ms(2500)
    assert clock.to_utc(7, 0) == T0 - ms(1000)
    fit = clock.fit(7)
    assert fit is not None
    assert (fit.slope, fit.n_anchors) == (1.0, 1)
    assert clock.uncertainty_ms(7) == 18


def test_two_anchors_fit_the_drift_exactly():
    # El dispositivo atrasa: 10 000 ms de dispositivo son 10 010 ms UTC.
    clock = DeviceClock.from_syncs([anchor(10_000, T0 + ms(10_010)), anchor(0, T0)])

    fit = clock.fit(7)
    assert fit is not None
    assert fit.slope == pytest.approx(1.001)
    assert clock.to_utc(7, 0) == T0
    assert clock.to_utc(7, 10_000) == T0 + ms(10_010)
    assert clock.to_utc(7, 5_000) == T0 + ms(5_005)


def test_least_squares_over_noisy_anchors_and_residual():
    syncs = [anchor(0, T0 + ms(1)), anchor(1000, T0 + ms(999)), anchor(2000, T0 + ms(2001)), anchor(3000, T0 + ms(2999))]

    fit = DeviceClock.from_syncs(syncs).fit(7)

    assert fit is not None
    assert fit.slope == pytest.approx(0.9996)
    # offset_ms se mide desde `reference` (la ancla UTC más temprana, T0 + 1 ms): 0,6 - 1 = -0,4.
    assert fit.reference == T0 + ms(1)
    assert fit.offset_ms == pytest.approx(-0.4)
    assert fit.max_residual_ms == pytest.approx(1.2)
    assert fit.n_anchors == 4


def test_uncertainty_is_the_worst_anchor_uncertainty():
    clock = DeviceClock.from_syncs([anchor(0, T0, uncertainty_ms=5), anchor(1000, T0 + ms(1000), uncertainty_ms=40)])

    assert clock.uncertainty_ms(7) == 40


def test_anchors_with_the_same_device_ms_fall_back_to_a_mean_offset():
    clock = DeviceClock.from_syncs([anchor(500, T0), anchor(500, T0 + ms(10))])

    fit = clock.fit(7)
    assert fit is not None
    assert fit.slope == 1.0
    assert clock.to_utc(7, 500) == T0 + ms(5)
    assert clock.to_utc(7, 1500) == T0 + ms(1005)


def test_non_positive_slope_falls_back_to_a_mean_offset():
    # Anclas incoherentes (más device_ms, UTC anterior): no se acepta un reloj que va hacia atrás.
    clock = DeviceClock.from_syncs([anchor(0, T0 + ms(1000)), anchor(1000, T0)])

    fit = clock.fit(7)
    assert fit is not None
    assert fit.slope == 1.0
    assert clock.to_utc(7, 500) == T0 + ms(500)


def test_boots_are_fitted_independently():
    clock = DeviceClock.from_syncs([anchor(0, T0, boot=1), anchor(0, T0 + ms(60_000), boot=2)])

    assert clock.to_utc(1, 0) == T0
    assert clock.to_utc(2, 0) == T0 + ms(60_000)


def test_huge_device_ms_is_unmapped_instead_of_raising():
    clock = DeviceClock.from_syncs([anchor(0, T0)])

    assert clock.to_utc(7, 10**400) is None
    assert clock.to_utc(7, 10**18) is None


def test_huge_anchor_values_do_not_raise():
    clock = DeviceClock.from_syncs([anchor(10**18, T0), anchor(10**18 + 1000, T0 + ms(1000))])

    assert clock.to_utc(7, 10**18 + 500) == T0 + ms(500)


def test_empty_syncs():
    assert DeviceClock.from_syncs([]).to_utc(0, 0) is None
    assert DeviceClock.from_syncs(None).to_utc(0, 0) is None


# --- Propiedades ---------------------------------------------------------------------------------

_device_ms = st.integers(0, 10**10)
# UTC = device_ms + jitter: relojes realistas (deriva y jitter de enlace), no rectas arbitrarias.
_anchor_specs = st.lists(st.tuples(_device_ms, st.integers(-1000, 1000), st.floats(0, 100)), min_size=1, max_size=8)


def build(specs) -> list[ClockSync]:
    return [anchor(d, T0 + ms(d + j), uncertainty_ms=e) for d, j, e in specs]


@pytest.mark.property
@given(_anchor_specs, _device_ms, _device_ms)
def test_mapping_is_monotonic(specs, x, y):
    clock = DeviceClock.from_syncs(build(specs))
    lo, hi = sorted((x, y))

    a, b = clock.to_utc(7, lo), clock.to_utc(7, hi)
    assert a is not None and b is not None
    assert a <= b


@pytest.mark.property
@given(_anchor_specs, st.randoms(use_true_random=False))
def test_fit_does_not_depend_on_anchor_order(specs, rnd):
    shuffled = list(specs)
    rnd.shuffle(shuffled)

    a, b = DeviceClock.from_syncs(build(specs)).fit(7), DeviceClock.from_syncs(build(shuffled)).fit(7)
    assert a is not None and b is not None
    assert a.slope == pytest.approx(b.slope, rel=1e-9, abs=1e-12)
    assert a.offset_ms == pytest.approx(b.offset_ms, rel=1e-9, abs=1e-3)


@pytest.mark.property
@given(_device_ms, st.integers(-(10**9), 10**9), st.integers(1, 10**7), st.integers(0, 10**4))
def test_exact_at_both_anchors_when_there_are_two(d, u, span, extra_ms):
    # Deriva positiva acotada: la recta pasa por las dos anclas (a resolución de microsegundo).
    first, second = anchor(d, T0 + ms(u)), anchor(d + span, T0 + ms(u + span + extra_ms))
    clock = DeviceClock.from_syncs([second, first])

    for a in (first, second):
        mapped = clock.to_utc(7, a.device_ms)
        assert mapped is not None
        assert abs(mapped - a.utc) <= timedelta(microseconds=1)


@pytest.mark.property
@given(_anchor_specs)
def test_uncertainty_comes_only_from_the_anchors(specs):
    assert DeviceClock.from_syncs(build(specs)).uncertainty_ms(7) == max(e for _, _, e in specs)
