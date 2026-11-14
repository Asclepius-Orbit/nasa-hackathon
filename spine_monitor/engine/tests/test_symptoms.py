"""Features de síntomas (#47): medias de 7 días sobre autoinformes, sin rellenar nunca con ceros."""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from spine_contracts import SCHEMA_VERSION, DailyContext, FeatureSet, SelfReport
from spine_contracts.messages import self_report_adapter
from spine_contracts.models.common_schema import GravityEnvironment
from spine_contracts.models.domain.feature_set_schema import Reason
from spine_engine.features.symptoms import WINDOW, symptom_feature_set

AS_OF = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
AST = "AST-01"
PAIN_MEAN = "symptoms.pain_nrs_7d_mean"
RETIRED_PAIN_MAX = "symptoms.pain_nrs_7d_max"  # retirada del catálogo: el engine ya no la produce
STIFF = "symptoms.stiffness_nrs_7d_mean"
SLEEP = "symptoms.sleep_hours_7d_mean"
FLAGS = "symptoms.red_flag_count_7d"
ALL_IDS = [FLAGS, PAIN_MEAN, SLEEP, STIFF]


def daily(
    at: datetime = AS_OF,
    pain: int = 3,
    stiffness: int = 2,
    sleep: float | None = None,
    red_flags: list[str] | None = None,
    astronaut: str = AST,
) -> SelfReport:
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_id": "11111111-1111-4111-8111-111111111111",
        "astronaut_id": astronaut,
        "timestamp": at.isoformat(),
        "source": "simulator",
        "kind": "daily",
        "pain_nrs": pain,
        "stiffness_nrs": stiffness,
    }
    if sleep is not None:
        body["sleep"] = {"hours": sleep}
    if red_flags is not None:
        body["red_flags"] = red_flags
    return self_report_adapter.validate_python(body)


def red_flag_report(at: datetime = AS_OF, flags: tuple[str, ...] = ("leg_weakness",), astronaut: str = AST) -> SelfReport:
    return self_report_adapter.validate_python(
        {
            "schema_version": SCHEMA_VERSION,
            "report_id": "22222222-2222-4222-8222-222222222222",
            "astronaut_id": astronaut,
            "timestamp": at.isoformat(),
            "source": "simulator",
            "kind": "red_flag",
            "red_flags": list(flags),
        }
    )


def odi(at: datetime = AS_OF) -> SelfReport:
    return self_report_adapter.validate_python(
        {
            "schema_version": SCHEMA_VERSION,
            "report_id": "33333333-3333-4333-8333-333333333333",
            "astronaut_id": AST,
            "timestamp": at.isoformat(),
            "source": "simulator",
            "kind": "weekly_odi",
            "items": [{"item_id": "lifting", "score": 5}],
        }
    )


def context(day: date, gravity: str = "microgravity", astronaut: str = AST) -> DailyContext:
    return DailyContext.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "astronaut_id": astronaut,
            "date": day.isoformat(),
            "phase": "inflight",
            "gravity_environment": gravity,
        }
    )


def run(reports: list[SelfReport], contexts: list[DailyContext] | None = None) -> FeatureSet:
    return symptom_feature_set(reports, contexts or [], AS_OF, AST)


def values(feature_set: FeatureSet) -> dict[str, float]:
    return {f.feature_id: f.value for f in feature_set.features}


def skipped(feature_set: FeatureSet) -> dict[str, Reason]:
    return {s.feature_id: s.reason for s in feature_set.skipped or []}


def test_envelope_is_rolling_window_without_session() -> None:
    fs = run([daily()])
    assert fs.session_id is None
    assert fs.protocol_id is None
    assert fs.source is None
    assert fs.astronaut_id == AST
    assert fs.schema_version == SCHEMA_VERSION
    assert fs.computed_at == AS_OF
    assert fs.window is not None
    assert (fs.window.start, fs.window.end) == (AS_OF - WINDOW, AS_OF)
    assert timedelta(days=7) == WINDOW


def test_mean_of_known_values() -> None:
    fs = run(
        [
            daily(AS_OF - timedelta(days=1), pain=2, stiffness=1),
            daily(AS_OF - timedelta(days=2), pain=7, stiffness=4),
            daily(AS_OF, pain=3, stiffness=1),
        ]
    )
    assert values(fs)[PAIN_MEAN] == pytest.approx(4.0)
    assert RETIRED_PAIN_MAX not in values(fs)
    assert RETIRED_PAIN_MAX not in skipped(fs)
    assert values(fs)[STIFF] == pytest.approx(2.0)


def test_features_carry_catalog_unit_count_and_quality() -> None:
    fs = run([daily(pain=2, sleep=6.0, red_flags=[]), daily(AS_OF - timedelta(days=1), pain=4)])
    by_id = {f.feature_id: f for f in fs.features}
    assert by_id[PAIN_MEAN].unit == "nrs"
    assert by_id[PAIN_MEAN].n == 2
    assert by_id[SLEEP].unit == "h"
    assert by_id[SLEEP].n == 1
    assert by_id[FLAGS].unit == "count"
    assert by_id[PAIN_MEAN].quality.score == 1.0
    assert by_id[PAIN_MEAN].quality.assessed_by.value == "engine"
    assert by_id[PAIN_MEAN].quality.flags == []


def test_features_and_skipped_are_sorted_by_id() -> None:
    fs = run([daily()])
    assert [f.feature_id for f in fs.features] == sorted(f.feature_id for f in fs.features)
    assert fs.skipped is not None
    assert [s.feature_id for s in fs.skipped] == sorted(s.feature_id for s in fs.skipped)


def test_sleep_mean_only_over_reports_with_sleep_hours() -> None:
    fs = run([daily(AS_OF, sleep=6.0), daily(AS_OF - timedelta(days=1), sleep=7.0), daily(AS_OF - timedelta(days=2))])
    assert values(fs)[SLEEP] == pytest.approx(6.5)
    assert next(f.n for f in fs.features if f.feature_id == SLEEP) == 2


def test_sleep_skipped_when_no_report_has_sleep() -> None:
    fs = run([daily()])
    assert skipped(fs) == {SLEEP: Reason.NO_DATA, FLAGS: Reason.NO_DATA}
    assert SLEEP not in values(fs)


def test_red_flags_counted_from_daily_and_red_flag_reports() -> None:
    fs = run(
        [
            daily(red_flags=["leg_weakness", "saddle_numbness"]),
            red_flag_report(flags=("trauma_event", "fever_with_back_pain", "leg_weakness")),
            daily(AS_OF - timedelta(days=1), red_flags=[]),
        ]
    )
    assert values(fs)[FLAGS] == 5


def test_daily_with_empty_red_flags_counts_zero_but_is_present() -> None:
    fs = run([daily(red_flags=[])])
    assert values(fs)[FLAGS] == 0
    assert FLAGS not in skipped(fs)


def test_daily_without_red_flags_field_is_missing_not_zero() -> None:
    fs = run([daily(red_flags=None)])
    assert FLAGS not in values(fs)
    assert skipped(fs)[FLAGS] == Reason.NO_DATA


def test_unanswered_daily_is_not_counted_as_a_flag_observation() -> None:
    fs = run([daily(red_flags=None), daily(red_flags=[]), red_flag_report()])
    flags = next(feature for feature in fs.features if feature.feature_id == FLAGS)
    assert (flags.value, flags.n) == (1, 2)


def test_only_a_red_flag_report_gives_a_count_and_skips_the_daily_metrics() -> None:
    fs = run([red_flag_report(flags=("leg_weakness", "trauma_event"))])
    assert values(fs) == {FLAGS: 2}
    assert skipped(fs) == {PAIN_MEAN: Reason.NO_DATA, STIFF: Reason.NO_DATA, SLEEP: Reason.NO_DATA}


def test_weekly_odi_contributes_to_nothing() -> None:
    fs = run([odi()])
    assert fs.features == []
    assert skipped(fs) == dict.fromkeys(ALL_IDS, Reason.NO_DATA)


def test_weekly_odi_does_not_alter_other_features() -> None:
    assert run([daily(), odi()]).features == run([daily()]).features


def test_no_reports_skips_everything_with_no_data() -> None:
    fs = run([])
    assert fs.features == []
    assert skipped(fs) == dict.fromkeys(ALL_IDS, Reason.NO_DATA)
    assert fs.skipped is not None
    assert len(fs.skipped) == 4


def test_zero_pain_is_a_value_not_missing() -> None:
    fs = run([daily(pain=0, stiffness=0)])
    assert values(fs)[PAIN_MEAN] == 0


def test_reports_of_another_astronaut_are_ignored() -> None:
    fs = run([daily(pain=9, astronaut="AST-02"), red_flag_report(astronaut="AST-02")])
    assert fs.features == []
    assert skipped(fs) == dict.fromkeys(ALL_IDS, Reason.NO_DATA)
    mixed = run([daily(pain=2), daily(pain=9, astronaut="AST-02")])
    assert values(mixed)[PAIN_MEAN] == 2


EDGES = [
    (timedelta(0), True),
    (-WINDOW, True),
    (-WINDOW - timedelta(microseconds=1), False),
    (timedelta(microseconds=1), False),
]


@pytest.mark.parametrize(("offset", "included"), EDGES)
def test_daily_window_edges_are_inclusive_and_exact(offset: timedelta, included: bool) -> None:
    fs = run([daily(AS_OF + offset, pain=5)])
    assert (PAIN_MEAN in values(fs)) is included


@pytest.mark.parametrize(("offset", "included"), EDGES)
def test_red_flag_report_window_edges(offset: timedelta, included: bool) -> None:
    assert (FLAGS in values(run([red_flag_report(AS_OF + offset)]))) is included


def test_timezone_offsets_are_compared_as_instants() -> None:
    plus2 = timezone(timedelta(hours=2))
    inside = (AS_OF - WINDOW).astimezone(plus2)
    assert PAIN_MEAN in values(run([daily(inside)]))
    assert PAIN_MEAN not in values(run([daily(inside - timedelta(microseconds=1))]))


def test_gravity_is_that_of_the_most_recent_context_in_window() -> None:
    contexts = [
        context(date(2026, 11, 10), "earth_1g"),
        context(date(2026, 11, 12), "microgravity"),
        context(date(2026, 11, 11), "earth_1g"),
    ]
    assert run([daily()], contexts).gravity_environment == GravityEnvironment.MICROGRAVITY
    assert run([daily()], contexts[::-1]).gravity_environment == GravityEnvironment.MICROGRAVITY


def test_gravity_is_none_without_context() -> None:
    assert run([daily()], []).gravity_environment is None
    assert run([]).gravity_environment is None


def test_gravity_applies_even_with_no_reports() -> None:
    assert run([], [context(date(2026, 11, 13), "earth_1g")]).gravity_environment == GravityEnvironment.EARTH_1G


@pytest.mark.parametrize(
    ("day", "included"),
    [(date(2026, 11, 13), True), (date(2026, 11, 6), True), (date(2026, 11, 5), False), (date(2026, 11, 14), False)],
)
def test_context_window_edges_compare_dates(day: date, included: bool) -> None:
    gravity = run([], [context(day, "earth_1g")]).gravity_environment
    assert (gravity is not None) is included


def test_context_of_another_astronaut_is_ignored() -> None:
    assert run([], [context(date(2026, 11, 13), astronaut="AST-02")]).gravity_environment is None


def test_context_date_uses_utc_date_of_as_of() -> None:
    as_of = datetime(2026, 11, 13, 23, 30, tzinfo=timezone(timedelta(hours=-3)))  # 02:30 UTC del 14
    fs = symptom_feature_set([], [context(date(2026, 11, 14), "earth_1g")], as_of, AST)
    assert fs.gravity_environment == GravityEnvironment.EARTH_1G
