"""AppSettings: the bounds of the UI view settings are validated at load time, not on every request."""

import pytest
from monitor.api import MAX_HISTORY_DAYS
from monitor.settings import AppSettings
from pydantic import ValidationError


def settings(**kwargs: int) -> AppSettings:
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
