"""Tests de `compare_periods`: la comparación de dos ventanas de historial que devuelve la herramienta MCP."""

import copy
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import History, dump, validation
from spine_mcp_server.compare import compare_periods

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
HISTORY_EXAMPLE: dict[str, Any] = json.loads((EXAMPLES / "app" / "history.json").read_text(encoding="utf-8"))
ASSESSMENT_EXAMPLE: dict[str, Any] = HISTORY_EXAMPLE["assessments"][0]

PROTO = "lat_pulldown_standing_v1"
MICRO = "microgravity"
RMS = "emg.latissimus_dorsi.left.rms_pct_mvc"
ROM = "imu.t9.sagittal.rom_deg"
FREQ = "emg.latissimus_dorsi.left.median_freq_hz"
T0 = datetime(2027, 2, 2, tzinfo=UTC)


def _ts(hours: float) -> str:
    return (T0 + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def _feature_set(features: list[tuple[str, float, str]], protocol: str | None = PROTO, gravity: str | None = MICRO, hours: float = 1.0) -> dict[str, Any]:
    """Feature set mínimo; protocol/gravity a None los omite (features de ventana móvil)."""
    fs: dict[str, Any] = {
        "schema_version": "1.0.0",
        "astronaut_id": "AST-01",
        "session_id": None,
        "computed_at": _ts(hours),
        "features": [
            {"feature_id": fid, "value": value, "unit": unit, "quality": {"score": 1.0, "flags": [], "assessed_by": "engine"}} for fid, value, unit in features
        ],
    }
    if protocol is not None:
        fs["protocol_id"] = protocol
    if gravity is not None:
        fs["gravity_environment"] = gravity
    return fs


def _assessment(level: str = "green", risk: float = 0.25, confidence: float = 0.5, completeness: float = 0.75, hours: float = 1.0) -> dict[str, Any]:
    a = copy.deepcopy(ASSESSMENT_EXAMPLE)
    a.update(level=level, risk_score=risk, confidence=confidence, as_of=_ts(hours))
    a["completeness"]["score"] = completeness
    return a


def _history(
    feature_sets: list[dict[str, Any]] | None = None,
    assessments: list[dict[str, Any]] | None = None,
    astronaut: str = "AST-01",
    truncated: bool = False,
    start: float = 0.0,
) -> History:
    return History.model_validate(
        {
            "schema_version": "1.0.0",
            "astronaut_id": astronaut,
            "from": _ts(start),
            "to": _ts(start + 100),
            "limit": 100,
            "truncated": truncated,
            "feature_sets": feature_sets or [],
            "assessments": assessments or [],
        }
    )


def _key(fid: str, protocol: str = PROTO, gravity: str = MICRO) -> dict[str, str]:
    return {"feature_id": fid, "protocol_id": protocol, "gravity_environment": gravity}


def _out(a: History, b: History) -> dict[str, Any]:
    return dump(compare_periods(a, b))


# --- metadatos ---------------------------------------------------------------------------------------------


def test_header_and_period_metadata_come_from_each_history():
    a = _history([_feature_set([(RMS, 1.0, "%")])] * 3, [_assessment()] * 2, astronaut="AST-07", start=0)
    b = _history([_feature_set([(RMS, 1.0, "%")])], [_assessment()] * 5, astronaut="AST-07", truncated=True, start=200)
    out = _out(a, b)
    assert out["schema_version"] == "1.0.0"
    assert out["astronaut_id"] == "AST-07"
    assert out["period_a"] == {"from": _ts(0), "to": _ts(100), "assessments_count": 2, "feature_sets_count": 3, "truncated": False}
    assert out["period_b"] == {"from": _ts(200), "to": _ts(300), "assessments_count": 5, "feature_sets_count": 1, "truncated": True}


def test_different_astronauts_raise_value_error():
    with pytest.raises(ValueError, match="AST-01.*AST-02"):
        compare_periods(_history(astronaut="AST-01"), _history(astronaut="AST-02"))


# --- features ----------------------------------------------------------------------------------------------


def test_identical_periods_give_zero_deltas():
    fs = [_feature_set([(RMS, 40.0, "%"), (ROM, 30.0, "deg")]), _feature_set([(RMS, 50.0, "%"), (ROM, 32.0, "deg")])]
    out = _out(_history(fs), _history(fs))
    assert out["features"] == [
        {
            **_key(RMS),
            "unit": "%",
            "a": {"n": 2, "mean": 45.0, "median": 45.0},
            "b": {"n": 2, "mean": 45.0, "median": 45.0},
            "delta_mean": 0.0,
            "delta_median": 0.0,
        },
        {
            **_key(ROM),
            "unit": "deg",
            "a": {"n": 2, "mean": 31.0, "median": 31.0},
            "b": {"n": 2, "mean": 31.0, "median": 31.0},
            "delta_mean": 0.0,
            "delta_median": 0.0,
        },
    ]
    assert out["only_in_a"] == []
    assert out["only_in_b"] == []
    assert out["warnings"] == []


def test_shift_aggregates_values_across_feature_sets_with_odd_and_even_medians():
    # A: [1, 2, 6] -> n 3, media 3, mediana 2 (impar). B: [8, 2, 6, 4] -> n 4, media 5, mediana 5 (par, media de 4 y 6).
    a = _history([_feature_set([(RMS, 1.0, "%")]), _feature_set([(RMS, 2.0, "%"), (RMS, 6.0, "%")])])
    b = _history([_feature_set([(RMS, 8.0, "%"), (RMS, 2.0, "%")]), _feature_set([(RMS, 6.0, "%")]), _feature_set([(RMS, 4.0, "%")])])
    (feature,) = _out(a, b)["features"]
    assert feature == {
        **_key(RMS),
        "unit": "%",
        "a": {"n": 3, "mean": 3.0, "median": 2.0},
        "b": {"n": 4, "mean": 5.0, "median": 5.0},
        "delta_mean": 2.0,
        "delta_median": 3.0,
    }


def test_deltas_are_b_minus_a_so_a_drop_is_negative():
    a = _history([_feature_set([(ROM, 31.0, "deg"), (ROM, 33.0, "deg")])])
    b = _history([_feature_set([(ROM, 28.0, "deg")])])
    (feature,) = _out(a, b)["features"]
    assert feature["a"] == {"n": 2, "mean": 32.0, "median": 32.0}
    assert feature["b"] == {"n": 1, "mean": 28.0, "median": 28.0}
    assert feature["delta_mean"] == -4.0
    assert feature["delta_median"] == -4.0


def test_keys_only_in_one_period_are_listed_and_not_compared():
    a = _history([_feature_set([(RMS, 1.0, "%"), (FREQ, 80.0, "Hz")])])
    b = _history([_feature_set([(RMS, 2.0, "%"), (ROM, 30.0, "deg")])])
    out = _out(a, b)
    assert [f["feature_id"] for f in out["features"]] == [RMS]
    assert out["only_in_a"] == [_key(FREQ)]
    assert out["only_in_b"] == [_key(ROM)]
    assert out["warnings"] == []


def test_protocol_and_gravity_are_part_of_the_key():
    a = _history([_feature_set([(RMS, 1.0, "%")], protocol="row_seated_v1"), _feature_set([(RMS, 1.0, "%")], gravity="earth_1g")])
    b = _history([_feature_set([(RMS, 2.0, "%")], protocol="row_seated_v2"), _feature_set([(RMS, 2.0, "%")], gravity="moon_0_16g")])
    out = _out(a, b)
    assert out["features"] == []
    assert out["only_in_a"] == [_key(RMS, gravity="earth_1g"), _key(RMS, protocol="row_seated_v1")]
    assert out["only_in_b"] == [_key(RMS, gravity="moon_0_16g"), _key(RMS, protocol="row_seated_v2")]


def test_feature_sets_without_protocol_or_gravity_are_not_comparable_and_ignored():
    a = _history([_feature_set([(RMS, 1.0, "%")], protocol=None), _feature_set([(ROM, 1.0, "deg")], gravity=None)])
    b = _history([_feature_set([(RMS, 2.0, "%")], protocol=None), _feature_set([(ROM, 2.0, "deg")], gravity=None)])
    out = _out(a, b)
    assert (out["features"], out["only_in_a"], out["only_in_b"]) == ([], [], [])
    assert out["period_a"]["feature_sets_count"] == 2


def test_an_ignored_feature_set_does_not_hide_the_ones_after_it():
    a = _history([_feature_set([(RMS, 9.0, "%")], protocol=None), _feature_set([(RMS, 1.0, "%")])])
    b = _history([_feature_set([(RMS, 9.0, "%")], gravity=None), _feature_set([(RMS, 3.0, "%")])])
    (feature,) = _out(a, b)["features"]
    assert (feature["a"]["n"], feature["b"]["n"], feature["delta_mean"]) == (1, 1, 2.0)


def test_unit_mismatch_between_periods_drops_the_key_and_warns():
    a = _history([_feature_set([(RMS, 1.0, "%"), (ROM, 30.0, "deg")])])
    b = _history([_feature_set([(RMS, 2.0, "mV"), (ROM, 31.0, "deg")])])
    out = _out(a, b)
    assert [f["feature_id"] for f in out["features"]] == [ROM]
    assert (out["only_in_a"], out["only_in_b"]) == ([], [])
    assert out["warnings"] == ["unit_mismatch"]


@pytest.mark.parametrize("mixed", ["a", "b"])
def test_mixed_units_inside_one_period_are_a_unit_mismatch(mixed: str):
    mixed_fs = [_feature_set([(RMS, 1.0, "%"), (RMS, 2.0, "mV")])]
    clean_fs = [_feature_set([(RMS, 1.0, "%")])]
    a, b = (mixed_fs, clean_fs) if mixed == "a" else (clean_fs, mixed_fs)
    out = _out(_history(a), _history(b))
    assert out["features"] == []
    assert out["warnings"] == ["unit_mismatch"]


def test_unit_mismatch_is_reported_once_for_several_keys():
    a = _history([_feature_set([(RMS, 1.0, "%"), (ROM, 30.0, "deg")])])
    b = _history([_feature_set([(RMS, 2.0, "mV"), (ROM, 31.0, "rad")])])
    out = _out(a, b)
    assert out["features"] == []
    assert out["warnings"] == ["unit_mismatch"]


def test_ordering_is_by_feature_then_protocol_then_gravity_regardless_of_input_order():
    # El orden por feature_id contradice el de protocol_id, y el de protocol_id el de gravity: una clave mal ordenada se nota.
    keys = [
        ("emg.a.x", "zz_proto_v1", "mars_0_38g"),
        ("emg.b.x", "aa_proto_v1", "earth_1g"),
        ("emg.b.x", "bb_proto_v1", "earth_1g"),
        ("emg.b.x", "bb_proto_v1", "microgravity"),
    ]
    sets = [_feature_set([(fid, 1.0, "%")], protocol=p, gravity=g) for fid, p, g in keys]
    expected = [_key(fid, p, g) for fid, p, g in keys]
    for seed in range(5):
        shuffled = sets[:]
        random.Random(seed).shuffle(shuffled)
        out = _out(_history(shuffled), _history(shuffled))
        assert [{k: f[k] for k in ("feature_id", "protocol_id", "gravity_environment")} for f in out["features"]] == expected
        out_a = _out(_history(shuffled), _history())
        out_b = _out(_history(), _history(shuffled))
        assert out_a["only_in_a"] == expected
        assert out_b["only_in_b"] == expected


# --- niveles -----------------------------------------------------------------------------------------------


def test_level_counts_per_period():
    a = _history(assessments=[_assessment("green", hours=1), _assessment("amber", hours=2), _assessment("green", hours=3)])
    b = _history(assessments=[_assessment("red", hours=1), _assessment("amber", hours=2), _assessment("red", hours=3), _assessment("red", hours=4)])
    levels = _out(a, b)["levels"]
    assert levels == {"a": {"green": 2, "amber": 1, "red": 0, "latest": "green"}, "b": {"green": 0, "amber": 1, "red": 3, "latest": "red"}}


def test_latest_level_is_by_as_of_not_by_list_order():
    items = [_assessment("amber", hours=5), _assessment("red", hours=9), _assessment("green", hours=1)]
    for order in (items, items[::-1], [items[2], items[1], items[0]], [items[1], items[0], items[2]]):
        assert _out(_history(assessments=order), _history())["levels"]["a"]["latest"] == "red"


def test_latest_level_compares_instants_not_offsets():
    # 10:00+02:00 (08:00Z) es anterior a 09:00Z aunque su texto sea mayor.
    early = _assessment("red")
    early["as_of"] = "2027-02-02T10:00:00+02:00"
    late = _assessment("green")
    late["as_of"] = "2027-02-02T09:00:00Z"
    assert _out(_history(assessments=[late, early]), _history())["levels"]["a"]["latest"] == "green"


@pytest.mark.parametrize("worst", ["amber", "red"])
def test_latest_level_tie_on_as_of_takes_the_worst_level(worst: str):
    tied = [_assessment("green", hours=3), _assessment(worst, hours=3), _assessment("green", hours=3), _assessment("red", hours=1)]
    for order in (tied, tied[::-1]):
        assert _out(_history(assessments=order), _history())["levels"]["a"]["latest"] == worst


def test_amber_beats_green_on_tie():
    tied = [_assessment("green", hours=3), _assessment("amber", hours=3)]
    for order in (tied, tied[::-1]):
        assert _out(_history(assessments=order), _history())["levels"]["a"]["latest"] == "amber"


# --- scores ------------------------------------------------------------------------------------------------


def test_scores_are_means_per_period_and_delta_is_b_minus_a():
    a = _history(assessments=[_assessment(risk=0.25, confidence=0.5, completeness=1.0), _assessment(risk=0.75, confidence=1.0, completeness=0.0)])
    b = _history(assessments=[_assessment(risk=0.75, confidence=0.25, completeness=0.5), _assessment(risk=0.5, confidence=0.25, completeness=1.0)])
    scores = _out(a, b)["scores"]
    assert scores == {
        "risk_score": {"a": 0.5, "b": 0.625, "delta": 0.125},
        "confidence": {"a": 0.75, "b": 0.25, "delta": -0.5},
        "completeness": {"a": 0.5, "b": 0.75, "delta": 0.25},
    }


# --- vacíos y avisos ---------------------------------------------------------------------------------------


def test_both_periods_empty():
    out = _out(_history(), _history())
    assert out["features"] == out["only_in_a"] == out["only_in_b"] == []
    assert out["levels"] == {"a": {"green": 0, "amber": 0, "red": 0, "latest": None}, "b": {"green": 0, "amber": 0, "red": 0, "latest": None}}
    null = {"a": None, "b": None, "delta": None}
    assert out["scores"] == {"risk_score": null, "confidence": null, "completeness": null}
    assert out["warnings"] == ["period_a_empty", "period_b_empty"]


def test_period_a_empty_gives_null_a_and_delta():
    b = _history([_feature_set([(RMS, 1.0, "%")])], [_assessment(risk=0.25, confidence=0.5, completeness=0.75)])
    out = _out(_history(), b)
    assert out["scores"]["risk_score"] == {"a": None, "b": 0.25, "delta": None}
    assert out["scores"]["confidence"] == {"a": None, "b": 0.5, "delta": None}
    assert out["scores"]["completeness"] == {"a": None, "b": 0.75, "delta": None}
    assert out["only_in_b"] == [_key(RMS)]
    assert out["warnings"] == ["period_a_empty"]


def test_period_b_empty_gives_null_b_and_delta():
    a = _history([_feature_set([(RMS, 1.0, "%")])], [_assessment(risk=0.25, confidence=0.5, completeness=0.75)])
    out = _out(a, _history())
    assert out["scores"]["risk_score"] == {"a": 0.25, "b": None, "delta": None}
    assert out["scores"]["confidence"] == {"a": 0.5, "b": None, "delta": None}
    assert out["scores"]["completeness"] == {"a": 0.75, "b": None, "delta": None}
    assert out["only_in_a"] == [_key(RMS)]
    assert out["warnings"] == ["period_b_empty"]


def test_feature_sets_without_assessments_is_not_empty_but_has_null_scores():
    only_features = _history([_feature_set([(RMS, 1.0, "%")])])
    only_assessments = _history(assessments=[_assessment()])
    out = _out(only_features, only_assessments)
    assert out["warnings"] == []
    assert out["scores"]["risk_score"] == {"a": None, "b": 0.25, "delta": None}
    assert out["levels"]["a"]["latest"] is None


def test_truncated_periods_warn_in_fixed_order_with_unit_mismatch_last():
    a = _history([_feature_set([(RMS, 1.0, "%")])], truncated=True)
    b = _history([_feature_set([(RMS, 1.0, "mV")])], truncated=True)
    assert _out(a, b)["warnings"] == ["period_a_truncated", "period_b_truncated", "unit_mismatch"]
    assert _out(a, _history([_feature_set([(RMS, 1.0, "%")])]))["warnings"] == ["period_a_truncated"]
    assert _out(_history([_feature_set([(RMS, 1.0, "%")])]), b)["warnings"] == ["period_b_truncated", "unit_mismatch"]


def test_empty_warnings_come_before_truncated_warnings():
    out = _out(_history(truncated=True), _history(truncated=True))
    assert out["warnings"] == ["period_a_empty", "period_b_empty", "period_a_truncated", "period_b_truncated"]


# --- contrato ----------------------------------------------------------------------------------------------


@pytest.mark.contract
def test_output_validates_against_the_compare_periods_schema():
    a = _history(
        [_feature_set([(RMS, 47.0, "%"), (FREQ, 79.5, "Hz"), (ROM, 31.0, "deg")]), _feature_set([(RMS, 46.0, "%")], protocol=None)],
        [_assessment("green", hours=1), _assessment("amber", 0.75, hours=2)],
        truncated=True,
    )
    b = _history([_feature_set([(RMS, 52.4, "%"), (ROM, 28.2, "rad")])], [_assessment("red", 1.0, 0.0, 0.0)], start=200)
    validation.validate("mcp/compare-periods", dump(compare_periods(a, b)))
    validation.validate("mcp/compare-periods", dump(compare_periods(_history(), _history())))


# --- propiedades -------------------------------------------------------------------------------------------

_FIDS = st.sampled_from([RMS, ROM, FREQ])
_PROTOS = st.sampled_from([PROTO, "row_seated_v1"])
_GRAVS = st.sampled_from([MICRO, "earth_1g"])
_VALUES = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False)
_UNIT01 = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
_feature_sets = st.lists(
    st.builds(
        lambda feats, p, g, h: _feature_set(feats, protocol=p, gravity=g, hours=h),
        st.lists(st.tuples(_FIDS, _VALUES, st.sampled_from(["%", "%", "mV"])), max_size=4),
        _PROTOS,
        _GRAVS,
        st.integers(0, 50),
    ),
    max_size=4,
)
_assessments = st.lists(
    st.builds(_assessment, st.sampled_from(["green", "amber", "red"]), _UNIT01, _UNIT01, _UNIT01, st.integers(0, 50)),
    max_size=4,
)
_histories = st.builds(_history, _feature_sets, _assessments, st.just("AST-01"), st.booleans())


def _swap_warnings(warnings: list[str]) -> set[str]:
    a_to_b = {"period_a_empty": "period_b_empty", "period_a_truncated": "period_b_truncated"}
    swap = a_to_b | {b: a for a, b in a_to_b.items()}
    return {swap.get(w, w) for w in warnings}


@pytest.mark.property
@given(_histories, _histories)
def test_swapping_periods_negates_deltas_and_swaps_only_lists(a: History, b: History):
    ab, ba = _out(a, b), _out(b, a)
    assert (ab["period_a"], ab["period_b"]) == (ba["period_b"], ba["period_a"])
    assert ab["only_in_a"] == ba["only_in_b"]
    assert ab["only_in_b"] == ba["only_in_a"]
    assert len(ab["features"]) == len(ba["features"])
    for f, g in zip(ab["features"], ba["features"], strict=True):
        assert (f["a"], f["b"]) == (g["b"], g["a"])
        assert f["delta_mean"] == -g["delta_mean"]
        assert f["delta_median"] == -g["delta_median"]
    for name in ("risk_score", "confidence", "completeness"):
        s, t = ab["scores"][name], ba["scores"][name]
        assert (s["a"], s["b"]) == (t["b"], t["a"])
        assert s["delta"] == (None if t["delta"] is None else -t["delta"])
    assert (ab["levels"]["a"], ab["levels"]["b"]) == (ba["levels"]["b"], ba["levels"]["a"])
    assert _swap_warnings(ab["warnings"]) == set(ba["warnings"])
    validation.validate("mcp/compare-periods", ab)


@pytest.mark.property
@given(_histories)
def test_period_compared_with_itself_has_zero_deltas_and_nothing_only_in_one(h: History):
    out = _out(h, h)
    assert out["only_in_a"] == out["only_in_b"] == []
    assert all(f["delta_mean"] == 0 and f["delta_median"] == 0 and f["a"] == f["b"] for f in out["features"])
    assert all(s["delta"] in (None, 0) for s in out["scores"].values())
    assert "unit_mismatch" not in out["warnings"] or any(
        len({f.unit for fs in h.feature_sets for f in fs.features if f.feature_id == fid}) > 1 for fid in (RMS, ROM, FREQ)
    )
