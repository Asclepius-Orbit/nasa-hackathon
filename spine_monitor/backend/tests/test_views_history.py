"""History view: one row per UTC day of a window, with explicit absence (nodata / null), never invented values."""

from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from monitor.db import SessionRow
from monitor.views.history import build_history
from monitor.views.lumbar import ELONGATION_FEATURE
from spine_contracts import SCHEMA_VERSION, validation

PROTOCOL, GRAVITY = "lat_pulldown_standing_v1", "earth_1g"
LAUNCH = date(2030, 1, 1)
TODAY = date(2030, 1, 10)


def mission_day_of(day: date) -> int | None:
    return (day - LAUNCH).days + 1


def assessment(as_of: str, level: str = "green") -> dict[str, Any]:
    return {"as_of": as_of, "level": level}


def feature_set(start: str, value: float | None, *, protocol: str = PROTOCOL, gravity: str = GRAVITY) -> dict[str, Any]:
    quality = {"score": 1.0, "flags": []}
    features = [] if value is None else [{"feature_id": ELONGATION_FEATURE, "value": value, "unit": "mm", "quality": quality}]
    return {
        "protocol_id": protocol,
        "gravity_environment": gravity,
        "computed_at": "2031-01-01T00:00:00Z",
        "window": {"start": start, "end": start},
        "features": features,
    }


def report(timestamp: str, kind: str = "daily", pain: int | None = 3, flags: list[str] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"timestamp": timestamp, "kind": kind, "red_flags": [] if flags is None else flags}
    if pain is not None:
        body["pain_nrs"] = pain
    return body


def session(started_at: str, state: str = "processed", protocol: str = PROTOCOL) -> SessionRow:
    payload = {"session_id": str(uuid4()), "started_at": started_at, "protocol_id": protocol}
    return SessionRow(payload, state, None)


def baseline(mean: float = 6.0, lo: float = 4.0, hi: float = 9.0, protocol: str = PROTOCOL, gravity: str = GRAVITY) -> dict[str, Any]:
    entry = {"feature_id": ELONGATION_FEATURE, "protocol_id": protocol, "gravity_environment": gravity, "n": 3, "mean": mean, "min": lo, "max": hi}
    return {"entries": [entry]}


def view(first: date = date(2030, 1, 8), last: date = date(2030, 1, 10), **kwargs: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"assessments": [], "feature_sets": [], "self_reports": [], "sessions": [], "baseline": None}
    return build_history(first, last, kwargs.pop("today", TODAY), mission_day_of, **{**args, **kwargs})


def day_of(result: dict[str, Any], iso: str) -> dict[str, Any]:
    return next(d for d in result["days"] if d["date"] == iso)


# --- shape ---------------------------------------------------------------------------------------


def test_an_empty_window_is_all_nodata_and_valid():
    result = view()

    assert validation.errors("app/ui-history", result) == []
    assert result["schema_version"] == SCHEMA_VERSION
    assert (result["from"], result["to"], result["today_day"], result["preflight_range_mm"]) == ("2030-01-08", "2030-01-10", 10, None)
    assert result["days"] == [
        {"date": f"2030-01-{n:02d}", "mission_day": n, "level": "nodata", "length_delta_mm": None, "pain_nrs": None, "session": None, "report": None}
        for n in (8, 9, 10)
    ]


def test_days_are_inclusive_ascending_with_their_mission_day():
    result = view(date(2030, 1, 3), date(2030, 1, 5))

    assert [(d["date"], d["mission_day"]) for d in result["days"]] == [("2030-01-03", 3), ("2030-01-04", 4), ("2030-01-05", 5)]


def test_a_single_day_window_has_one_row():
    assert len(view(date(2030, 1, 8), date(2030, 1, 8))["days"]) == 1


def test_from_after_to_is_refused():
    with pytest.raises(ValueError, match="from"):
        view(date(2030, 1, 9), date(2030, 1, 8))


def test_mission_day_and_today_day_are_null_without_launch():
    result = build_history(date(2030, 1, 8), date(2030, 1, 8), TODAY, lambda _: None, [], [], [], [], None)

    assert result["today_day"] is None
    assert result["days"][0]["mission_day"] is None
    assert validation.errors("app/ui-history", result) == []


def test_today_day_is_computed_for_today_not_for_the_window_end():
    assert view(date(2030, 1, 1), date(2030, 1, 2), today=date(2030, 1, 7))["today_day"] == 7


# --- level ---------------------------------------------------------------------------------------


def test_level_is_the_latest_assessment_of_the_day():
    result = view(
        assessments=[assessment("2030-01-09T18:00:00Z", "red"), assessment("2030-01-09T08:00:00Z", "green"), assessment("2030-01-08T08:00:00Z", "amber")]
    )

    assert [d["level"] for d in result["days"]] == ["amber", "red", "nodata"]


def test_the_day_is_the_utc_day_whatever_the_offset():
    # 23:30 at -05:00 on the 8th is 04:30 UTC on the 9th.
    result = view(assessments=[assessment("2030-01-08T23:30:00-05:00", "red")])

    assert [d["level"] for d in result["days"]] == ["nodata", "red", "nodata"]


def test_anything_but_an_assessment_leaves_the_level_nodata():
    result = view(
        feature_sets=[feature_set("2030-01-09T10:00:00Z", 7.0)],
        self_reports=[report("2030-01-09T10:00:00Z")],
        sessions=[session("2030-01-09T10:00:00Z")],
        baseline=baseline(),
    )

    assert day_of(result, "2030-01-09")["level"] == "nodata"


def test_data_outside_the_window_is_ignored():
    result = view(
        assessments=[assessment("2030-01-07T23:59:59Z"), assessment("2030-01-11T00:00:00Z")],
        self_reports=[report("2030-01-07T23:59:59Z"), report("2030-01-11T00:00:00Z")],
        sessions=[session("2030-01-07T23:59:59Z"), session("2030-01-11T00:00:00Z")],
        feature_sets=[feature_set("2030-01-07T23:59:59Z", 7.0), feature_set("2030-01-11T00:00:00Z", 7.0)],
        baseline=baseline(),
    )

    assert all(d["level"] == "nodata" and d["session"] is None and d["report"] is None and d["length_delta_mm"] is None for d in result["days"])


# --- length --------------------------------------------------------------------------------------


def test_length_delta_is_the_latest_elongation_of_the_day_minus_its_baseline_mean():
    result = view(
        feature_sets=[feature_set("2030-01-09T09:00:00Z", 9.0), feature_set("2030-01-09T17:00:00Z", 8.5), feature_set("2030-01-08T09:00:00Z", 5.0)],
        baseline=baseline(mean=6.0),
    )

    assert [d["length_delta_mm"] for d in result["days"]] == [-1.0, 2.5, None]


def test_length_delta_uses_the_baseline_entry_of_each_measure_protocol_and_gravity():
    base = {"entries": [*baseline(mean=6.0)["entries"], *baseline(mean=1.0, protocol="other_v1", gravity="microgravity")["entries"]]}
    result = view(
        feature_sets=[feature_set("2030-01-08T09:00:00Z", 7.0), feature_set("2030-01-09T09:00:00Z", 7.0, protocol="other_v1", gravity="microgravity")],
        baseline=base,
    )

    assert [d["length_delta_mm"] for d in result["days"]] == [1.0, 6.0, None]


def test_length_delta_is_null_without_baseline_or_matching_entry_never_zero():
    sets = [feature_set("2030-01-09T09:00:00Z", 7.0)]

    assert day_of(view(feature_sets=sets, baseline=None), "2030-01-09")["length_delta_mm"] is None
    assert day_of(view(feature_sets=sets, baseline=baseline(protocol="other_v1")), "2030-01-09")["length_delta_mm"] is None
    assert day_of(view(feature_sets=sets, baseline={"entries": []}), "2030-01-09")["length_delta_mm"] is None


def test_length_delta_is_null_when_the_day_has_no_elongation():
    result = view(feature_sets=[feature_set("2030-01-09T09:00:00Z", None)], baseline=baseline())

    assert day_of(result, "2030-01-09")["length_delta_mm"] is None


def test_a_genuine_zero_delta_is_reported_as_zero():
    result = view(feature_sets=[feature_set("2030-01-09T09:00:00Z", 6.0)], baseline=baseline(mean=6.0))

    assert day_of(result, "2030-01-09")["length_delta_mm"] == 0.0


# --- preflight range -----------------------------------------------------------------------------


def test_preflight_range_is_the_entry_of_the_most_recent_measure():
    base = {"entries": [*baseline(6.0, 4.0, 9.0)["entries"], *baseline(10.0, 8.0, 11.0, protocol="other_v1")["entries"]]}
    sets = [feature_set("2030-01-09T09:00:00Z", 7.0), feature_set("2030-01-01T09:00:00Z", 7.0, protocol="other_v1")]

    assert view(feature_sets=sets, baseline=base)["preflight_range_mm"] == {"lo": -2.0, "hi": 3.0}


def test_preflight_range_considers_measures_outside_the_window():
    base = {"entries": [*baseline(6.0, 4.0, 9.0)["entries"], *baseline(10.0, 8.0, 11.0, protocol="other_v1")["entries"]]}
    result = view(feature_sets=[feature_set("2029-12-01T09:00:00Z", 7.0, protocol="other_v1")], baseline=base)

    assert result["preflight_range_mm"] == {"lo": -2.0, "hi": 1.0}


def test_preflight_range_falls_back_to_the_first_lumbar_entry_without_measures():
    other_feature = {**baseline()["entries"][0], "feature_id": "emg.x"}
    base = {"entries": [other_feature, *baseline(6.0, 4.0, 9.0)["entries"], *baseline(1.0, 0.0, 2.0, protocol="other_v1")["entries"]]}

    assert view(baseline=base)["preflight_range_mm"] == {"lo": -2.0, "hi": 3.0}


def test_preflight_range_is_null_without_baseline_or_when_the_measure_has_no_entry():
    assert view(baseline=None)["preflight_range_mm"] is None
    assert view(baseline={"entries": []})["preflight_range_mm"] is None
    assert view(feature_sets=[feature_set("2030-01-09T09:00:00Z", 7.0)], baseline=baseline(protocol="other_v1"))["preflight_range_mm"] is None


# --- pain, session, report -----------------------------------------------------------------------


def test_pain_is_that_of_the_latest_daily_report_of_the_day():
    result = view(
        self_reports=[
            report("2030-01-09T08:00:00Z", pain=2),
            report("2030-01-09T20:00:00Z", pain=7),
            report("2030-01-09T22:00:00Z", kind="weekly_odi", pain=None),
            report("2030-01-08T08:00:00Z", kind="red_flag", pain=None, flags=["leg_weakness"]),
        ]
    )

    assert [d["pain_nrs"] for d in result["days"]] == [None, 7, None]


def test_a_zero_pain_is_not_null():
    assert day_of(view(self_reports=[report("2030-01-09T08:00:00Z", pain=0)]), "2030-01-09")["pain_nrs"] == 0


def test_report_is_the_latest_of_the_day_with_its_red_flags():
    result = view(
        self_reports=[
            report("2030-01-09T08:00:00Z"),
            report("2030-01-09T20:00:00Z", kind="red_flag", pain=None, flags=["leg_weakness", "saddle_numbness", "leg_weakness"]),
            report("2030-01-08T08:00:00Z", kind="weekly_odi", pain=None),
        ]
    )

    assert [d["report"] for d in result["days"]] == [
        {"kind": "weekly_odi", "red_flags": []},
        {"kind": "red_flag", "red_flags": ["leg_weakness", "saddle_numbness"]},
        None,
    ]


def test_a_report_without_red_flags_key_has_an_empty_list():
    entry = {"timestamp": "2030-01-09T08:00:00Z", "kind": "weekly_odi"}

    assert day_of(view(self_reports=[entry]), "2030-01-09")["report"] == {"kind": "weekly_odi", "red_flags": []}


def test_session_is_the_latest_of_the_day():
    early, late = session("2030-01-09T08:00:00Z", "ended"), session("2030-01-09T19:00:00Z", "processing", protocol="spine_length_rest_v1")
    result = view(sessions=[late, early, session("2030-01-10T08:00:00Z")])

    assert day_of(result, "2030-01-09")["session"] == {"session_id": late.payload["session_id"], "state": "processing", "protocol_id": "spine_length_rest_v1"}
    assert day_of(result, "2030-01-08")["session"] is None


def test_the_full_view_validates_against_its_schema():
    payload = {"session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a03", "started_at": "2030-01-09T09:00:00Z", "protocol_id": PROTOCOL}
    result = view(
        assessments=[assessment("2030-01-09T09:00:00Z", "amber")],
        feature_sets=[feature_set("2030-01-09T09:00:00Z", 8.0)],
        self_reports=[report("2030-01-09T08:00:00Z", flags=["leg_weakness"])],
        sessions=[SessionRow(payload, "processed", None)],
        baseline=baseline(),
    )

    assert validation.errors("app/ui-history", result) == []
    assert day_of(result, "2030-01-09")["length_delta_mm"] == 2.0


# --- properties ----------------------------------------------------------------------------------

dates = st.dates(min_value=date(2029, 1, 1), max_value=date(2031, 1, 1))


@pytest.mark.property
@given(first=dates, span=st.integers(0, 120))
def test_property_one_ascending_row_per_day_all_nodata_without_data(first, span):
    last = first + timedelta(days=span)
    result = view(first, last)

    assert len(result["days"]) == span + 1
    assert [d["date"] for d in result["days"]] == [(first + timedelta(days=i)).isoformat() for i in range(span + 1)]
    assert all(d["level"] == "nodata" and d["length_delta_mm"] is None and d["pain_nrs"] is None for d in result["days"])


@pytest.mark.property
@given(
    first=dates,
    span=st.integers(0, 20),
    data=st.lists(st.tuples(st.integers(-3, 25), st.integers(0, 23), st.sampled_from(["green", "amber", "red"]), st.floats(0, 20)), max_size=15),
)
def test_property_nodata_exactly_when_no_assessment_and_deltas_only_from_measures(first, span, data):
    last = first + timedelta(days=span)

    def stamp(n: int, hour: int) -> str:
        return f"{(first + timedelta(days=n)).isoformat()}T{hour:02d}:00:00Z"

    assessments = [assessment(stamp(n, h), level) for n, h, level, _ in data]
    sets = [feature_set(stamp(n, h), value) for n, h, _, value in data]
    result = view(first, last, assessments=assessments, feature_sets=sets, baseline=baseline(mean=6.0))

    measured = {(first + timedelta(days=n)).isoformat() for n, *_ in data}
    for row in result["days"]:
        assert (row["level"] != "nodata") == (row["date"] in measured)
        assert (row["length_delta_mm"] is not None) == (row["date"] in measured)
    assert validation.errors("app/ui-history", result) == []
