"""Funciones auxiliares del dataset de demo, con las expectativas calculadas a mano."""

from datetime import UTC, datetime, timedelta

import pytest
from spine_mcp_server import demo_data as demo

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _at(day: int, hour: int, minute: int = 0, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("score", "level"),
    [(0.0, "green"), (0.39, "green"), (0.399999, "green"), (0.4, "amber"), (0.41, "amber"), (0.799999, "amber"), (0.8, "red"), (0.81, "red"), (1.0, "red")],
)
def test_level_thresholds_are_0_4_and_0_8_with_the_upper_bound_exclusive(score: float, level: str):
    assert demo._level(score) == level  # pyright: ignore[reportPrivateUsage] -- se prueba el helper privado del módulo


@pytest.mark.parametrize(
    ("levels", "worst"),
    [(("green",), "green"), (("green", "amber"), "amber"), (("red", "green", "amber"), "red"), (("amber", "amber"), "amber"), (("green", "green"), "green")],
)
def test_worst_picks_the_most_severe_level(levels: tuple[str, ...], worst: str):
    assert demo._worst(*levels) == worst  # pyright: ignore[reportPrivateUsage] -- helper privado


@pytest.mark.parametrize(
    ("value", "low", "high", "expected"),
    [
        (0.5, 0.0, 1.0, 0.5),
        (-0.1, 0.0, 1.0, 0.0),
        (1.1, 0.0, 1.0, 1.0),
        (0.0, 0.0, 1.0, 0.0),
        (1.0, 0.0, 1.0, 1.0),
        (0.01, 0.02, 0.35, 0.02),
        (0.4, 0.02, 0.35, 0.35),
    ],
)
def test_clamp_limits_both_ends(value: float, low: float, high: float, expected: float):
    assert demo._clamp(value, low, high) == expected  # pyright: ignore[reportPrivateUsage] -- helper privado


def test_clamp_defaults_to_the_unit_interval():
    assert (demo._clamp(-5), demo._clamp(5), demo._clamp(0.3)) == (0.0, 1.0, 0.3)  # pyright: ignore[reportPrivateUsage] -- helper privado


def test_hour_rounds_down_and_keeps_the_zone():
    rounded = demo._hour(datetime(2026, 11, 13, 12, 59, 59, 999999, tzinfo=UTC))  # pyright: ignore[reportPrivateUsage] -- helper privado
    assert rounded == NOW
    assert rounded.tzinfo is UTC
    assert demo._hour(NOW) == NOW  # pyright: ignore[reportPrivateUsage] -- helper privado
    assert demo._hour(_at(13, 12, 1)) == NOW  # pyright: ignore[reportPrivateUsage] -- helper privado


def test_iso_has_no_fraction_and_a_z_suffix():
    assert demo._iso(datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)) == "2026-01-02T03:04:05Z"  # pyright: ignore[reportPrivateUsage] -- helper privado


def test_uuid_is_a_uuid5_over_astronaut_kind_and_key():
    import uuid

    expected = str(uuid.uuid5(uuid.UUID("a5c1e910-0000-4000-8000-0d3e0da7a000"), "AST-01/session/2026-11-13T10:00:00Z"))
    assert demo._uuid("session", "2026-11-13T10:00:00Z") == expected  # pyright: ignore[reportPrivateUsage] -- helper privado
    assert demo._uuid("assessment", "2026-11-13T10:00:00Z") != expected  # pyright: ignore[reportPrivateUsage] -- helper privado
    assert demo._uuid("session", "2026-11-13T11:00:00Z") != expected  # pyright: ignore[reportPrivateUsage] -- helper privado


# stretch(t): t < 8 -> 6,6 + 0,06 t; t >= 8 -> 7,0 + 0,35 (t - 6)
@pytest.mark.parametrize(
    ("t", "value"),
    [(0, 6.6), (1, 6.66), (2, 6.72), (3, 6.78), (4, 6.84), (5, 6.9), (6, 6.96), (7, 7.02), (8, 7.7), (9, 8.05), (10, 8.4), (11, 8.75), (12, 9.1), (13, 9.45)],
)
def test_stretch_per_day(t: int, value: float):
    assert demo._stretch(t) == value  # pyright: ignore[reportPrivateUsage] -- helper privado


# risk(t): t < 8 -> 0,18 + 0,02 t; t >= 8 -> 0,40 + 0,0444 (t - 8)
@pytest.mark.parametrize(
    ("t", "risk"),
    [(0, 0.18), (1, 0.2), (2, 0.22), (3, 0.24), (4, 0.26), (5, 0.28), (6, 0.3), (7, 0.32), (8, 0.4), (9, 0.44), (10, 0.49), (11, 0.53), (12, 0.58), (13, 0.62)],
)
def test_risk_per_day(t: int, risk: float):
    assert demo._risk(t) == risk  # pyright: ignore[reportPrivateUsage] -- helper privado


# media de ventana [t-6, t] sobre _PAIN = [2,2,2,2,2,2,3,3,3,4,4,5,5,5]
@pytest.mark.parametrize(
    ("t", "mean"),
    [(0, 2.0), (3, 2.0), (5, 2.0), (6, 2.1), (7, 2.3), (8, 2.4), (9, 2.7), (10, 3.0), (11, 3.4), (12, 3.9), (13, 4.1)],
)
def test_pain_mean_7d_uses_the_trailing_window_and_is_partial_at_the_start(t: int, mean: float):
    assert demo._pain_mean_7d(t) == mean  # pyright: ignore[reportPrivateUsage] -- helper privado


@pytest.mark.parametrize(("value", "z"), [(6.5, 0.0), (9.1, 2.4), (6.6, 0.1), (7.7, 1.1), (4.55, -1.8), (14.1, 7.0), (29.1, 20.9)])
def test_z_stretch_is_the_distance_to_6_5_in_1_083_units(value: float, z: float):
    assert demo._z_stretch(value) == z  # pyright: ignore[reportPrivateUsage] -- helper privado


@pytest.mark.parametrize(("mean", "z"), [(2.0, 0.0), (4.1, 1.7), (5.0, 2.5), (3.2, 1.0), (1.0, -0.8)])
def test_z_pain_is_the_distance_to_2_in_1_2_units(mean: float, z: float):
    assert demo._z_pain(mean) == z  # pyright: ignore[reportPrivateUsage] -- helper privado


def test_stretch_baseline_entry_is_the_stretch_feature_of_the_example_baseline():
    entry = demo._stretch_baseline_entry()  # pyright: ignore[reportPrivateUsage] -- helper privado
    assert entry["feature_id"] == "stretch.full_spine.peak_elongation_mm"


def test_freshness_age_is_in_hours_rounded_to_one_decimal_and_stale_is_strictly_greater():
    items = [
        ("exactly", NOW - timedelta(hours=6), 6, None),
        ("just_over", NOW - timedelta(hours=6, minutes=6), 6, None),  # 6,1 h
        ("fresh", NOW - timedelta(minutes=90), 72, "wb-01"),
        ("rounds_down", NOW - timedelta(hours=1, minutes=2), 1, None),  # 1,03 -> 1,0, no supera 1
        ("future", NOW + timedelta(hours=1), 6, None),
    ]
    result = demo._freshness(NOW, items)  # pyright: ignore[reportPrivateUsage] -- helper privado
    assert result == [
        {"what": "exactly", "last_at": "2026-11-13T06:00:00Z", "age_hours": 6.0, "stale": False},
        {"what": "just_over", "last_at": "2026-11-13T05:54:00Z", "age_hours": 6.1, "stale": True},
        {"what": "fresh", "last_at": "2026-11-13T10:30:00Z", "age_hours": 1.5, "stale": False, "device_id": "wb-01"},
        {"what": "rounds_down", "last_at": "2026-11-13T10:58:00Z", "age_hours": 1.0, "stale": False},
        {"what": "future", "last_at": "2026-11-13T13:00:00Z", "age_hours": -1.0, "stale": False},
    ]


def test_timeline_rounds_now_down_to_the_hour():
    assert demo._Timeline(datetime(2026, 11, 13, 12, 59, 59, tzinfo=UTC)).now == NOW  # pyright: ignore[reportPrivateUsage] -- helper privado


def test_timeline_marks_are_all_relative_to_now():
    tl = demo._Timeline(_at(13, 12, 34))  # pyright: ignore[reportPrivateUsage] -- helper privado
    assert [tl.t(k) for k in (13, 12, 1, 0)] == [0, 1, 12, 13]
    assert tl.assessment_at(0) == NOW
    assert tl.assessment_at(3) == _at(10, 12)
    assert [tl.mission_day(k) for k in (0, 1, 13)] == [87, 86, 74]
    assert tl.report_at(0) == _at(13, 11, 30)
    assert tl.report_at(2) == _at(11, 11, 30)
    assert tl.exercise_ks() == [13, 11, 9, 7, 5, 3, 1]
    assert tl.exercise_start(1) == _at(12, 10, 30)
    assert tl.exercise_end(1) == _at(12, 10, 36)
    assert tl.feature_set_at(1) == _at(12, 10, 39)
    assert tl.check_starts() == [_at(5, 10), _at(9, 10), _at(13, 10)]
    assert tl.odi_at() == _at(11, 11, 15)


@pytest.mark.parametrize(("k", "expected"), [(0, 1), (1, 1), (2, 3), (3, 3), (4, 5), (12, 13), (13, 13)])
def test_session_k_is_the_last_processed_exercise_day(k: int, expected: int):
    assert demo._Timeline(NOW).session_k(k) == expected  # pyright: ignore[reportPrivateUsage] -- helper privado
