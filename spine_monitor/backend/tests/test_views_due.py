"""reports_due keeps the semantics of GET /ui/crew/{id}/self-reports/due: daily unless done today (UTC), ODI if never or past the interval."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from monitor.views.due import reports_due

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


def at(delta: timedelta) -> dict[str, str]:
    return {"timestamp": (NOW + delta).isoformat()}


def test_nothing_ever_reported_means_both_due() -> None:
    assert reports_due(None, None, NOW, 7) == (True, True)


def test_daily_done_today_is_not_due() -> None:
    assert reports_due(at(timedelta(hours=-5)), None, NOW, 7)[0] is False


def test_daily_from_yesterday_is_due() -> None:
    assert reports_due(at(timedelta(hours=-11)), None, NOW, 7)[0] is True


def test_daily_date_is_taken_in_utc() -> None:
    # 23:30 on the 12th in UTC-5 is 04:30 on the 13th in UTC: it counts as today.
    local = datetime(2026, 11, 12, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert reports_due({"timestamp": local.isoformat()}, None, NOW, 7)[0] is False


def test_odi_exactly_at_the_interval_is_due() -> None:
    assert reports_due(None, at(timedelta(days=-7)), NOW, 7)[1] is True


def test_odi_just_inside_the_interval_is_not_due() -> None:
    assert reports_due(None, at(timedelta(days=-7, seconds=1)), NOW, 7)[1] is False


@pytest.mark.parametrize("interval", [1, 7, 14])
def test_odi_follows_the_configured_interval(interval: int) -> None:
    assert reports_due(None, at(timedelta(days=-interval)), NOW, interval)[1] is True
    assert reports_due(None, at(timedelta(days=-interval + 1)), NOW, interval)[1] is False


def test_both_done_recently() -> None:
    assert reports_due(at(timedelta(minutes=-1)), at(timedelta(days=-1)), NOW, 7) == (False, False)
