"""mission_for / mission_day_of: mission day and phase from the launch and landing dates (pure, `today` is a parameter)."""

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from monitor.views.mission import mission_day_of, mission_for

LAUNCH = date(2026, 11, 15)
LANDING = date(2027, 5, 15)
PROFILE = {"launch_date": LAUNCH.isoformat(), "landing_date": LANDING.isoformat()}
NULLS = {"day": None, "start": None, "phase": None}


def test_without_profile_everything_is_null() -> None:
    assert mission_for(None, LAUNCH) == NULLS


@pytest.mark.parametrize("profile", [{}, {"launch_date": None}, {"launch_date": None, "landing_date": "2027-05-15"}])
def test_without_launch_date_everything_is_null(profile: dict[str, str | None]) -> None:
    assert mission_for(profile, LAUNCH) == NULLS


@pytest.mark.parametrize(
    ("today", "day", "phase"),
    [
        (LAUNCH - timedelta(days=30), -30, "preflight"),
        (LAUNCH - timedelta(days=1), -1, "preflight"),
        (LAUNCH, 1, "inflight"),
        (LAUNCH + timedelta(days=86), 87, "inflight"),
        (LANDING, 182, "inflight"),
        (LANDING + timedelta(days=1), 183, "postflight"),
    ],
)
def test_day_and_phase(today: date, day: int, phase: str) -> None:
    assert mission_for(PROFILE, today) == {"day": day, "start": "2026-11-15", "phase": phase}


@pytest.mark.parametrize("profile", [{"launch_date": "2026-11-15"}, {"launch_date": "2026-11-15", "landing_date": None}])
def test_without_landing_date_it_stays_inflight(profile: dict[str, str | None]) -> None:
    assert mission_for(profile, date(2030, 1, 1))["phase"] == "inflight"


def test_mission_day_of_is_reusable() -> None:
    assert mission_day_of(LAUNCH, LAUNCH) == 1
    assert mission_day_of(LAUNCH, LAUNCH - timedelta(days=30)) == -30


DATES = st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 1, 1))


@pytest.mark.property
@given(launch=DATES, offset=st.integers(-2000, 2000))
def test_day_is_never_zero_and_sign_follows_phase(launch: date, offset: int) -> None:
    result = mission_for({"launch_date": launch.isoformat()}, launch + timedelta(days=offset))
    assert result["day"] != 0
    assert (result["day"] < 0) == (result["phase"] == "preflight")
    assert result["phase"] == ("preflight" if offset < 0 else "inflight")


@pytest.mark.property
@given(launch=DATES, offset=st.integers(-2000, 2000))
def test_day_is_strictly_increasing_by_date(launch: date, offset: int) -> None:
    today = launch + timedelta(days=offset)
    after = mission_day_of(launch, today + timedelta(days=1))
    before = mission_day_of(launch, today)
    # Consecutive dates step by one, except across the launch day where the numbering skips 0.
    assert after > before
    assert after - before == (2 if before == -1 else 1)


@pytest.mark.property
@given(launch=DATES, landing_gap=st.integers(0, 400), offset=st.integers(-500, 900))
def test_phase_boundaries_with_landing(launch: date, landing_gap: int, offset: int) -> None:
    landing = launch + timedelta(days=landing_gap)
    today = launch + timedelta(days=offset)
    result = mission_for({"launch_date": launch.isoformat(), "landing_date": landing.isoformat()}, today)
    expected = "preflight" if today < launch else "postflight" if today > landing else "inflight"
    assert result["phase"] == expected
    assert result["day"] == mission_day_of(launch, today)
