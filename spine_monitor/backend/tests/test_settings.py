"""AppSettings: the bounds of the UI view settings are validated at load time, not on every request."""

from datetime import UTC, datetime
from typing import Any

import pytest
from monitor.api import MAX_HISTORY_DAYS
from monitor.ground.link import GroundWindows
from monitor.settings import AppSettings
from pydantic import ValidationError


def settings(**kwargs: Any) -> AppSettings:
    return AppSettings(_env_file=None, **kwargs)


@pytest.mark.parametrize("days", [0, -1, 367])
def test_history_window_days_out_of_range_is_refused(days: int) -> None:
    with pytest.raises(ValidationError, match="history_window_days"):
        settings(history_window_days=days)


@pytest.mark.parametrize("days", [1, 30, 366])
def test_history_window_days_in_range_is_accepted(days: int) -> None:
    assert settings(history_window_days=days).history_window_days == days


@pytest.mark.parametrize("required", [0, -1])
def test_baseline_required_below_one_is_refused(required: int) -> None:
    with pytest.raises(ValidationError, match="baseline_required"):
        settings(baseline_required=required)


def test_baseline_required_one_is_accepted() -> None:
    assert settings(baseline_required=1).baseline_required == 1


def test_the_history_window_upper_bound_is_the_api_limit() -> None:
    assert settings(history_window_days=MAX_HISTORY_DAYS).history_window_days == MAX_HISTORY_DAYS
    with pytest.raises(ValidationError):
        settings(history_window_days=MAX_HISTORY_DAYS + 1)


def test_no_ground_link_is_configured_by_default() -> None:
    loaded = settings()

    assert loaded.ground_window_anchor is None
    assert loaded.ground_windows is None
    assert (loaded.ground_retry_min_s, loaded.ground_retry_max_s) == (1, 300)


def test_the_ground_windows_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_APP_GROUND_WINDOW_ANCHOR", "2026-11-13T12:00:00+02:00")
    monkeypatch.setenv("SPINE_APP_GROUND_WINDOW_PERIOD_MIN", "15")
    monkeypatch.setenv("SPINE_APP_GROUND_WINDOW_UP_MIN", "5")

    assert settings().ground_windows == GroundWindows(datetime(2026, 11, 13, 10, 0, tzinfo=UTC), 15, 5)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"ground_window_anchor": "2026-11-13T10:00:00"}, "ground_window_anchor"),
        ({"ground_window_period_min": 0}, "ground_window_period_min"),
        ({"ground_window_up_min": 0}, "ground_window_up_min"),
        ({"ground_window_period_min": 10, "ground_window_up_min": 10}, "ground_window_up_min"),
        ({"ground_retry_min_s": 0}, "ground_retry_min_s"),
        ({"ground_retry_min_s": 10, "ground_retry_max_s": 9}, "ground_retry_max_s"),
        ({"ground_tick_s": 0}, "ground_tick_s"),
        ({"ground_send_timeout_s": 0}, "ground_send_timeout_s"),
    ],
    ids=["naive_anchor", "period_zero", "up_zero", "up_not_below_period", "retry_min_zero", "retry_max_below_min", "tick_zero", "send_timeout_zero"],
)
def test_bad_ground_settings_are_refused_at_startup(overrides: dict[str, Any], field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        settings(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [{"ground_window_period_min": 11, "ground_window_up_min": 10}, {"ground_retry_min_s": 9, "ground_retry_max_s": 9}],
    ids=["up_just_below_period", "retry_max_equal_to_min"],
)
def test_the_limits_of_the_ground_settings_are_accepted(overrides: dict[str, Any]) -> None:
    assert settings(**overrides)


def test_processing_retry_defaults() -> None:
    loaded = settings()
    assert (loaded.processing_retry_min_s, loaded.processing_retry_max_s, loaded.processing_max_attempts) == (30.0, 900.0, 5)


def test_processing_retry_max_below_min_is_refused() -> None:
    with pytest.raises(ValidationError, match="processing_retry_max_s"):
        settings(processing_retry_min_s=60, processing_retry_max_s=30)


def test_reassess_debounce_defaults_to_five_seconds() -> None:
    assert settings().reassess_debounce_s == 5.0


def test_reassess_debounce_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_APP_REASSESS_DEBOUNCE_S", "0")
    assert settings().reassess_debounce_s == 0  # zero is allowed: the harness turns the delay off


def test_negative_reassess_debounce_is_refused() -> None:
    with pytest.raises(ValidationError, match="reassess_debounce_s"):
        settings(reassess_debounce_s=-0.1)


def test_reassess_tick_defaults_to_one_hour() -> None:
    assert settings().reassess_tick_s == 3600.0


def test_reassess_tick_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_APP_REASSESS_TICK_S", "60")
    assert settings().reassess_tick_s == 60


@pytest.mark.parametrize("tick", [0, -1])
def test_a_reassess_tick_that_is_not_positive_is_refused(tick: float) -> None:
    with pytest.raises(ValidationError, match="reassess_tick_s"):
        settings(reassess_tick_s=tick)
