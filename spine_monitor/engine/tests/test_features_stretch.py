"""``stretch`` family (#45): cord elongation, peak and range per repetition, recovery and rest resistance.

Time grid: 10 Hz (one sample every 100 ms) from ``T0``; sample ``i`` falls at ``T0 + 100 ms·i``.
Test curve: (1000 Ω, 100 mm) and (2000 Ω, 200 mm) with a rest of 100 mm, so the elongation in mm is
``(R − 1000) / 10`` and ``R = 1000 + 10·e``. Repetitions are fixed with a ``FakeService`` (boundary).
"""

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from spine_contracts import FeaturesRequest
from spine_contracts.catalog import all_feature_ids, expand_template
from spine_contracts.models.domain.feature_set_schema import Reason
from spine_engine.features.calibrations import CalibrationResolution, CalibrationStatus
from spine_engine.features.context import ExtractionContext, build_context
from spine_engine.features.registry import ComputedFeature, FamilyResult
from spine_engine.features.repetitions import RepetitionService, RepWindow, RepWindows
from spine_engine.features.stretch import StretchFamily

_EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "features-request.json"
T0 = datetime(2026, 11, 13, 10, 3, 0, 100000, tzinfo=UTC)  # UTC of the example's clock anchor (device 180000 ms)
ANCHOR_DEVICE_MS = 180_000
BOOT = 2893746120
RATE_HZ = 10.0
STREAM = "stretch_spine"
CAL_ID = "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f11"
OTHER_CAL_ID = "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f99"
PREFIX = "stretch.full_spine"
NAMES = ("elongation_mm", "elongation_pct", "peak_elongation_mm", "range_mm", "recovery_time_s", "rest_resistance_ohm")
CALIBRATED = ("elongation_mm", "elongation_pct", "peak_elongation_mm", "range_mm", "recovery_time_s")
PER_REP = ("peak_elongation_mm", "range_mm", "recovery_time_s")
DEFAULT_WAIT_S = 2.0


def fid(name: str, region: str = "full_spine") -> str:
    return f"stretch.{region}.{name}"


def at(samples: int) -> datetime:
    return T0 + timedelta(milliseconds=100 * samples)


def window(index: int, start: int, end: int, rest_end: int | None) -> RepWindow:
    """Repetition with active [start, end) and rest [end, rest_end), in grid samples."""
    return RepWindow(index, at(start), at(end), at(end) if rest_end is not None else None, at(rest_end) if rest_end is not None else None)


class FakeService(RepetitionService):
    """Boundary: returns fixed windows, ignoring the context."""

    def __init__(self, windows: tuple[RepWindow, ...]) -> None:
        super().__init__()
        self._result = RepWindows(windows, "events" if windows else "none")

    def windows(self, context: ExtractionContext) -> RepWindows:
        return self._result


def ohm(elongations: list[float]) -> list[float]:
    """Resistances that give those elongations (mm) with the test curve."""
    return [1000.0 + 10.0 * e for e in elongations]


def calibration(
    *,
    calibration_id: str = CAL_ID,
    points: list[tuple[float, float]] | None = None,
    rest_length_mm: float = 100.0,
    recovery_wait_s: float | None = None,
    valid_until: datetime | None = None,
) -> dict[str, Any]:
    found: dict[str, Any] = {
        "schema_version": "1.0.0",
        "calibration_id": calibration_id,
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "stream_id": STREAM,
        "created_at": "2026-11-13T07:05:00Z",
        "valid_until": valid_until.isoformat() if valid_until else None,
        "source": "device",
        "calibration_type": "stretch",
        "rest_resistance_ohm": 1000.0,
        "rest_length_mm": rest_length_mm,
        "curve": {
            "model": "piecewise_linear",
            "points": [{"resistance_ohm": r, "length_mm": length} for r, length in (points or [(1000.0, 100.0), (2000.0, 200.0)])],
        },
    }
    if recovery_wait_s is not None:
        found["recovery_wait_s"] = recovery_wait_s
    return found


def chunks_of(
    resistances: list[float] | None,
    counts: list[int] | None,
    circuit: dict[str, Any] | None,
    *,
    size: int = 10,
    calibration_id: str | None = None,
) -> list[dict[str, Any]]:
    total = len(counts) if counts is not None else len(resistances or [])
    out: list[dict[str, Any]] = []
    for seq, start in enumerate(range(0, total, size)):
        n = min(size, total - start)
        data: dict[str, Any] = {"adc_counts": counts[start : start + n] if counts is not None else [500] * n}
        units = {"adc_counts": "count"}
        if resistances is not None:
            data["resistance_ohm"] = resistances[start : start + n]
            units["resistance_ohm"] = "ohm"
        chunk: dict[str, Any] = {
            "schema_version": "1.0.0",
            "astronaut_id": "AST-01",
            "device_id": "wb-01",
            "stream_id": STREAM,
            "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01",
            "source": "device",
            "boot_id": BOOT,
            "seq": seq,
            "t0_device_ms": ANCHOR_DEVICE_MS + 100 * start,
            "received_at": T0.isoformat(),
            "sample_rate_hz": RATE_HZ,
            "n_samples": n,
            "stream_kind": "stretch",
            "units": units,
            "data": data,
            "quality": {"adc_counts": {"score": 1.0, "flags": [], "assessed_by": "gateway"}},
        }
        if circuit is not None:
            chunk["circuit"] = circuit
        if calibration_id is not None:
            chunk["calibration_id"] = calibration_id
        out.append(chunk)
    return out


def make_context(
    resistances: list[float] | None = None,
    calibrations: list[dict[str, Any]] | None = None,
    *,
    counts: list[int] | None = None,
    circuit: dict[str, Any] | None = None,
    region: str = "full_spine",
    session_calibration_id: str | None = CAL_ID,
    chunk_calibration_id: str | None = None,
) -> ExtractionContext:
    """Context with a single declared stretch stream (``region``), its chunks and calibrations."""
    payload = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    declared = {"stream_id": STREAM, "stream_kind": "stretch", "placement": {"spine_region": region}, "sample_rate_hz": RATE_HZ}
    payload["session"]["streams"] = [{**declared, "calibration_id": session_calibration_id}]
    payload["chunks"] = chunks_of(resistances, counts, circuit, calibration_id=chunk_calibration_id)
    payload["calibrations"] = [calibration()] if calibrations is None else calibrations
    return build_context(FeaturesRequest.model_validate(payload))


def family_for(*windows: RepWindow, wait_s: float = DEFAULT_WAIT_S) -> StretchFamily:
    return StretchFamily(FakeService(windows), default_recovery_wait_s=wait_s)


def computed(result: FamilyResult) -> dict[str, ComputedFeature]:
    return {f.feature_id: f for f in result.computed}


def values(result: FamilyResult) -> dict[str, float]:
    return {f.feature_id: f.value for f in result.computed}


def skipped(result: FamilyResult) -> dict[str, Reason]:
    return {s.feature_id: s.reason for s in result.skipped}


@pytest.mark.parametrize(("protocol", "name", "resistance"), [
    ("spine_resistance_straight_v1", "straight_resistance_ohm", 4500.0),
    ("spine_resistance_max_flexion_v1", "max_flexion_resistance_ohm", 5650.0),
])
def test_posture_resistance_needs_no_length_calibration(protocol: str, name: str, resistance: float):
    context = make_context([resistance] * 10, [])
    context = dataclasses.replace(context, session=context.session.model_copy(update={"protocol_id": protocol}))
    result = family_for().extract(context)
    assert values(result)[fid(name)] == resistance
    assert computed(result)[fid(name)].n == 10
    assert set(values(result)) == {fid(name)}
    assert skipped(result) == {}
    other = "max_flexion_resistance_ohm" if name == "straight_resistance_ohm" else "straight_resistance_ohm"
    assert fid(other) not in values(result)
    assert fid(other) not in skipped(result)


def test_posture_resistance_without_samples_is_skipped():
    context = make_context(counts=[500] * 10, calibrations=[])
    context = dataclasses.replace(context, session=context.session.model_copy(update={"protocol_id": "spine_resistance_straight_v1"}))
    assert skipped(family_for().extract(context))[fid("straight_resistance_ohm")] is Reason.NO_DATA


# --- without repetitions: the passive part -----------------------------------------------------------------------------


def test_the_family_declares_every_concrete_catalog_id_of_the_stretch_features():
    family = family_for()

    assert family.family == "stretch"
    names = (*NAMES, "straight_resistance_ohm", "max_flexion_resistance_ohm")
    assert tuple(family.feature_ids) == tuple(i for name in names for i in expand_template(f"stretch.{{spine_region}}.{name}"))
    assert set(family.feature_ids) <= set(all_feature_ids())
    assert fid("elongation_mm", "cervical") in family.feature_ids


def test_without_repetitions_the_passive_features_are_the_means_over_all_samples():
    # elongations 2 and 4 mm in equal parts: mean 3 mm = 3 % of 100 mm; mean R 1030 Ω
    context = make_context(ohm([2.0] * 10 + [4.0] * 10))

    result = family_for().extract(context)

    assert values(result) == {
        fid("elongation_mm"): pytest.approx(3.0),
        fid("elongation_pct"): pytest.approx(3.0),
        fid("rest_resistance_ohm"): pytest.approx(1030.0),
    }
    assert computed(result)[fid("elongation_mm")].n == 20
    assert computed(result)[fid("rest_resistance_ohm")].n == 20
    assert computed(result)[fid("elongation_mm")].streams == (STREAM,)
    assert skipped(result) == dict.fromkeys((fid(n) for n in PER_REP), Reason.TOO_FEW_REPETITIONS)


# --- from ADC counts to resistance ---------------------------------------------------------------------

CIRCUIT = {"adc_bits": 10, "vref_v": 3.3, "series_resistor_ohm": 1000.0}
# 10 bits (full 1023) and Rs 1000 Ω: 341 counts -> 500 Ω, 682 -> 2000 Ω; with the curve (500, 100) (2000, 250) and
# rest 100 mm, 500 Ω is elongation 0 and 2000 Ω, 150 mm.
ADC_CURVE = [(500.0, 100.0), (2000.0, 250.0)]


def test_the_resistance_comes_from_the_adc_counts_and_the_chunk_circuit():
    context = make_context(counts=[341] * 5 + [682] * 5, circuit=CIRCUIT, calibrations=[calibration(points=ADC_CURVE)])

    result = family_for().extract(context)

    assert values(result) == {
        fid("elongation_mm"): pytest.approx(75.0),
        fid("elongation_pct"): pytest.approx(75.0),
        fid("rest_resistance_ohm"): pytest.approx(1250.0),
    }


def test_the_adc_counts_win_over_a_derived_resistance_in_the_same_chunk():
    context = make_context(ohm([9.0] * 10), counts=[341] * 5 + [682] * 5, circuit=CIRCUIT, calibrations=[calibration(points=ADC_CURVE)])

    result = family_for().extract(context)

    assert values(result)[fid("rest_resistance_ohm")] == pytest.approx(1250.0)


def test_saturated_or_zero_adc_counts_are_discarded():
    # 0 and 1023 (= 2^10 − 1) are not invertible: they come out NaN and do not count
    context = make_context(counts=[0, 1023] + [341] * 4 + [682] * 4, circuit=CIRCUIT, calibrations=[calibration(points=ADC_CURVE)])

    result = family_for().extract(context)

    assert values(result)[fid("elongation_mm")] == pytest.approx(75.0)
    assert computed(result)[fid("elongation_mm")].n == 8
    assert computed(result)[fid("rest_resistance_ohm")].n == 8


@pytest.mark.parametrize("incomplete", [{"adc_bits": 10, "vref_v": 3.3}, {"series_resistor_ohm": 1000.0}])
def test_a_circuit_without_bits_or_series_resistor_falls_back_to_the_derived_resistance(incomplete: dict[str, Any]):
    context = make_context(ohm([3.0] * 10), counts=[341] * 10, circuit=incomplete)

    result = family_for().extract(context)

    assert values(result)[fid("elongation_mm")] == pytest.approx(3.0)


def test_without_circuit_nor_resistance_everything_is_skipped_as_no_data():
    result = family_for().extract(make_context(counts=[341] * 10))

    assert result.computed == ()
    assert skipped(result) == {
        **dict.fromkeys((fid(n) for n in ("elongation_mm", "elongation_pct", "rest_resistance_ohm")), Reason.NO_DATA),
        **dict.fromkeys((fid(n) for n in PER_REP), Reason.TOO_FEW_REPETITIONS),
    }


def test_only_the_chunks_with_a_resistance_source_contribute():
    # first chunk without circuit or resistance; the second with a resistance derived from 5 mm
    context = make_context(counts=[341] * 20)
    payload = context.request.model_dump(mode="json")
    payload["chunks"][1]["data"]["resistance_ohm"] = ohm([5.0] * 10)
    payload["chunks"][1]["units"]["resistance_ohm"] = "ohm"
    context = build_context(FeaturesRequest.model_validate(payload))

    result = family_for().extract(context)

    assert values(result)[fid("elongation_mm")] == pytest.approx(5.0)
    assert computed(result)[fid("elongation_mm")].n == 10


def test_nan_derived_resistances_are_discarded():
    context = make_context(ohm([4.0] * 9) + [float("nan")])

    result = family_for().extract(context)

    assert values(result)[fid("elongation_mm")] == pytest.approx(4.0)
    assert computed(result)[fid("elongation_mm")].n == 9


# --- calibration, missing stream and region --------------------------------------------------------------------

PASSIVE_SIGNAL = ohm([2.0] * 10 + [4.0] * 10)


def test_without_calibration_the_calibrated_features_are_skipped_and_the_rest_resistance_is_computed():
    result = family_for().extract(make_context(PASSIVE_SIGNAL, []))

    assert skipped(result) == dict.fromkeys((fid(n) for n in CALIBRATED), Reason.NO_CALIBRATION)
    assert values(result) == {fid("rest_resistance_ohm"): pytest.approx(1030.0)}


def test_an_expired_calibration_skips_the_calibrated_features_as_calibration_expired():
    expired = [calibration(valid_until=datetime(2026, 11, 13, 9, 0, tzinfo=UTC))]

    result = family_for().extract(make_context(PASSIVE_SIGNAL, expired))

    assert skipped(result) == dict.fromkeys((fid(n) for n in CALIBRATED), Reason.CALIBRATION_EXPIRED)
    assert set(computed(result)) == {fid("rest_resistance_ohm")}


def test_a_calibration_reason_comes_before_the_lack_of_data():
    result = family_for().extract(make_context(counts=[341] * 10, calibrations=[]))

    assert skipped(result) == {**dict.fromkeys((fid(n) for n in CALIBRATED), Reason.NO_CALIBRATION), fid("rest_resistance_ohm"): Reason.NO_DATA}


def test_a_curve_with_repeated_resistances_is_unusable_and_skipped_as_no_calibration():
    broken = [calibration(points=[(1000.0, 100.0), (1000.0, 200.0)])]

    result = family_for().extract(make_context(PASSIVE_SIGNAL, broken))

    assert skipped(result) == dict.fromkeys((fid(n) for n in CALIBRATED), Reason.NO_CALIBRATION)
    assert set(computed(result)) == {fid("rest_resistance_ohm")}


def test_the_calibration_id_of_the_chunk_wins_over_the_one_of_the_session():
    # the session points to a curve that would give 2·e; the chunk, to the tests' one
    calibrations = [calibration(), calibration(calibration_id=OTHER_CAL_ID, points=[(1000.0, 100.0), (2000.0, 300.0)])]
    context = make_context(PASSIVE_SIGNAL, calibrations, session_calibration_id=OTHER_CAL_ID, chunk_calibration_id=CAL_ID)

    result = family_for().extract(context)

    assert values(result)[fid("elongation_mm")] == pytest.approx(3.0)


def test_a_resolved_calibration_that_is_not_a_stretch_one_counts_as_no_calibration():
    context = make_context(PASSIVE_SIGNAL)
    example = FeaturesRequest.model_validate(json.loads(_EXAMPLE.read_text(encoding="utf-8")))
    emg = next(c for c in example.calibrations or () if c.calibration_type == "emg_mvc")
    wrong = CalibrationResolution(STREAM, CAL_ID, emg, CalibrationStatus.RESOLVED)
    context = dataclasses.replace(context, calibrations={STREAM: wrong})

    result = family_for().extract(context)

    assert skipped(result) == dict.fromkeys((fid(n) for n in CALIBRATED), Reason.NO_CALIBRATION)


def test_a_declared_stream_without_chunks_is_skipped_as_stream_missing():
    # the example with its EMG and IMU chunks but without those of the declared lumbar cord
    payload = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    payload["chunks"] = [c for c in payload["chunks"] if c["stream_kind"] != "stretch"]

    result = family_for().extract(build_context(FeaturesRequest.model_validate(payload)))

    assert result.computed == ()
    assert skipped(result) == dict.fromkeys((fid(n) for n in NAMES), Reason.STREAM_MISSING)


@pytest.mark.parametrize("region", ["cervical", "thoracic", "thoracolumbar", "sacral"])
def test_the_ids_follow_the_declared_spine_region(region: str):
    result = family_for().extract(make_context(PASSIVE_SIGNAL, region=region))

    assert set(values(result)) == {fid(n, region) for n in ("elongation_mm", "elongation_pct", "rest_resistance_ohm")}
    assert set(skipped(result)) == {fid(n, region) for n in PER_REP}


def test_the_output_order_follows_the_catalog():
    result = family_for().extract(make_context(PASSIVE_SIGNAL))

    assert [f.feature_id for f in result.computed] == [fid("elongation_mm"), fid("elongation_pct"), fid("rest_resistance_ohm")]
    assert [s.feature_id for s in result.skipped] == [fid(n) for n in PER_REP]


def test_non_stretch_streams_are_ignored():
    # the example also declares EMG and IMU: only the lumbar cord gives features of this family
    context = build_context(FeaturesRequest.model_validate(json.loads(_EXAMPLE.read_text(encoding="utf-8"))))

    result = family_for().extract(context)

    assert {f.feature_id for f in (*result.computed, *result.skipped)} <= {fid(n) for n in NAMES}


def test_no_declared_stretch_stream_gives_an_empty_result():
    payload = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    payload["session"]["streams"] = [s for s in payload["session"]["streams"] if s["stream_kind"] != "stretch"]

    result = family_for().extract(build_context(FeaturesRequest.model_validate(payload)))

    assert result == FamilyResult()


# --- per repetition: peak and range ----------------------------------------------------------------------------

# rep 1: active [0,10) with 2 and 8 mm, rest [10,30) that drops to 0.5 mm at sample 12 and to 0 afterwards;
# rep 2: active [30,40) with 1 and 10 mm, rest [40,60) that drops to 0.9 mm at 44; passive tail [60,70) at 3 mm.
REP1 = [2.0] * 5 + [8.0] * 5 + [4.0, 4.0, 0.5] + [0.0] * 17
REP2 = [1.0] * 5 + [10.0] * 5 + [5.0] * 4 + [0.9] + [0.0] * 15
TAIL = [3.0] * 10
TWO_REPS = (window(1, 0, 10, 30), window(2, 30, 40, 60))
TWO_REP_SIGNAL = ohm(REP1 + REP2 + TAIL)
# rep 3: active [70,80) with 0 and 6 mm, no rest or samples afterwards
THREE_REPS = (*TWO_REPS, window(3, 70, 80, None))
THREE_REP_SIGNAL = ohm(REP1 + REP2 + TAIL + [0.0] * 5 + [6.0] * 5)


def test_peak_and_range_are_the_means_of_each_repetition_max_and_max_minus_min():
    result = family_for(*TWO_REPS).extract(make_context(TWO_REP_SIGNAL))

    by_id = computed(result)
    assert by_id[fid("peak_elongation_mm")].value == pytest.approx(9.0)  # (8 + 10) / 2, not the global maximum 10
    assert by_id[fid("range_mm")].value == pytest.approx(7.5)  # (6 + 9) / 2, not the global range 9
    assert by_id[fid("peak_elongation_mm")].n == 2
    assert by_id[fid("range_mm")].n == 2


def test_peak_and_range_with_three_repetitions():
    result = family_for(*THREE_REPS).extract(make_context(THREE_REP_SIGNAL))

    by_id = computed(result)
    assert by_id[fid("peak_elongation_mm")].value == pytest.approx(8.0)  # (8 + 10 + 6) / 3
    assert by_id[fid("range_mm")].value == pytest.approx(7.0)  # (6 + 9 + 6) / 3
    assert by_id[fid("peak_elongation_mm")].n == 3


def test_a_negative_peak_is_kept():
    # the cord below its rest in both actives: -3 and -1 mm (the curve drops to 50 mm)
    signal = ohm([-3.0] * 10 + [-1.0] * 10)
    curve = [calibration(points=[(500.0, 50.0), (1000.0, 100.0), (2000.0, 200.0)])]

    result = family_for(window(1, 0, 10, None), window(2, 10, 20, None)).extract(make_context(signal, curve))

    assert computed(result)[fid("peak_elongation_mm")].value == pytest.approx(-2.0)
    assert computed(result)[fid("range_mm")].value == pytest.approx(0.0)


def test_the_active_window_is_half_open():
    # actives [2,4) and [6,8): samples 4 and 8 (100 mm) do not count; 2 and 6 do
    signal = ohm([0.0, 0.0, 1.0, 2.0, 100.0, 0.0, 3.0, 4.0, 100.0, 0.0])

    result = family_for(window(1, 2, 4, None), window(2, 6, 8, None)).extract(make_context(signal))

    assert computed(result)[fid("peak_elongation_mm")].value == pytest.approx(3.0)  # (2 + 4) / 2
    assert computed(result)[fid("range_mm")].value == pytest.approx(1.0)


def test_a_single_repetition_is_too_few_for_the_per_repetition_features():
    result = family_for(TWO_REPS[0]).extract(make_context(TWO_REP_SIGNAL))

    assert skipped(result) == dict.fromkeys((fid(n) for n in PER_REP), Reason.TOO_FEW_REPETITIONS)


def test_two_repetitions_with_only_one_with_samples_are_too_few():
    result = family_for(TWO_REPS[0], window(2, 500, 510, None)).extract(make_context(TWO_REP_SIGNAL))

    assert skipped(result) == dict.fromkeys((fid(n) for n in PER_REP), Reason.TOO_FEW_REPETITIONS)


def test_repetitions_without_samples_are_no_data():
    result = family_for(window(1, 500, 510, None), window(2, 600, 610, None)).extract(make_context(TWO_REP_SIGNAL))

    assert skipped(result) == dict.fromkeys((fid(n) for n in PER_REP), Reason.NO_DATA)


def test_the_calibration_reason_comes_before_too_few_repetitions():
    result = family_for().extract(make_context(TWO_REP_SIGNAL, []))

    assert skipped(result) == dict.fromkeys((fid(n) for n in CALIBRATED), Reason.NO_CALIBRATION)


# --- the passive part with repetitions --------------------------------------------------------------------------------


def test_with_repetitions_the_passive_features_skip_the_active_windows_and_their_recovery_wait():
    # with the 2 s wait (20 samples) from each active end only the tail [60,70) at 3 mm remains
    result = family_for(*TWO_REPS).extract(make_context(TWO_REP_SIGNAL))

    by_id = computed(result)
    assert by_id[fid("elongation_mm")].value == pytest.approx(3.0)
    assert by_id[fid("elongation_pct")].value == pytest.approx(3.0)
    assert by_id[fid("rest_resistance_ohm")].value == pytest.approx(1030.0)
    assert by_id[fid("elongation_mm")].n == 10


def test_the_passive_samples_start_exactly_when_the_recovery_wait_ends():
    # one rep [0,10) with a 1 s wait: excludes [0,20); sample 20 (7 mm) already counts, 19 (100 mm) does not
    signal = ohm([0.0] * 19 + [100.0] + [7.0])

    result = family_for(window(1, 0, 10, None), wait_s=1.0).extract(make_context(signal))

    assert computed(result)[fid("elongation_mm")].value == pytest.approx(7.0)
    assert computed(result)[fid("elongation_mm")].n == 1


def test_the_samples_before_the_first_repetition_are_passive():
    signal = ohm([5.0] * 10 + [50.0] * 10)

    result = family_for(window(1, 10, 20, None), window(2, 15, 20, None)).extract(make_context(signal))

    assert computed(result)[fid("elongation_mm")].value == pytest.approx(5.0)
    assert computed(result)[fid("elongation_mm")].n == 10


def test_when_the_repetitions_cover_every_sample_the_passive_features_are_no_data():
    result = family_for(*TWO_REPS).extract(make_context(TWO_REP_SIGNAL[:60]))

    assert skipped(result)[fid("elongation_mm")] is Reason.NO_DATA
    assert skipped(result)[fid("elongation_pct")] is Reason.NO_DATA
    assert skipped(result)[fid("rest_resistance_ohm")] is Reason.NO_DATA


# --- recovery ---------------------------------------------------------------------------------------------


def recovering_reps(offset: int, *, rest: int = 40, low: float = 0.0, with_rest: bool = True) -> tuple[tuple[RepWindow, ...], list[float]]:
    """Two equal repetitions: an active phase of 10 samples at 10 mm and then ``rest`` samples at 5 mm until ``offset``
    (samples from the end of the active phase) and at ``low`` mm from there. The threshold is the default fraction (0.1) · 10 mm."""
    period = 10 + rest
    signal = ([10.0] * 10 + [5.0] * offset + [low] * (rest - offset)) * 2
    reps = tuple(window(i + 1, i * period, i * period + 10, i * period + period if with_rest else None) for i in range(2))
    return reps, ohm(signal)


def test_the_recovery_time_is_the_mean_time_from_the_end_of_each_repetition_to_the_first_recovered_sample():
    result = family_for(*TWO_REPS).extract(make_context(TWO_REP_SIGNAL))

    assert computed(result)[fid("recovery_time_s")].value == pytest.approx(0.3)  # (0,2 + 0,4) / 2
    assert computed(result)[fid("recovery_time_s")].n == 2


def test_a_repetition_that_does_not_recover_is_left_out_of_the_recovery_time():
    # rep 3 has no samples after its active phase: it does not recover and does not count
    result = family_for(*THREE_REPS).extract(make_context(THREE_REP_SIGNAL))

    assert computed(result)[fid("recovery_time_s")].value == pytest.approx(0.3)
    assert computed(result)[fid("recovery_time_s")].n == 2


def test_a_sample_recovered_right_at_the_end_of_the_repetition_gives_zero():
    reps, signal = recovering_reps(0)

    result = family_for(*reps).extract(make_context(signal))

    assert computed(result)[fid("recovery_time_s")].value == 0.0


def test_the_last_sample_inside_the_recovery_wait_still_counts():
    # wait 2 s = 20 samples: the search is [end, end + 2 s); sample 19 after the end (1.9 s) is inside
    reps, signal = recovering_reps(19)

    result = family_for(*reps).extract(make_context(signal))

    assert computed(result)[fid("recovery_time_s")].value == pytest.approx(1.9)


def test_no_recovery_within_the_wait_skips_the_recovery_time_as_no_data():
    reps, signal = recovering_reps(20)

    result = family_for(*reps).extract(make_context(signal))

    assert skipped(result)[fid("recovery_time_s")] is Reason.NO_DATA
    assert fid("peak_elongation_mm") in computed(result)


def test_the_rest_window_limits_the_search():
    # rest of 15 samples and recovery at 16: inside the 2 s wait but already outside the rest
    reps, signal = recovering_reps(16, rest=40)
    reps = tuple(dataclasses.replace(r, rest_end=r.end + timedelta(milliseconds=1500)) for r in reps)

    result = family_for(*reps).extract(make_context(signal))

    assert skipped(result)[fid("recovery_time_s")] is Reason.NO_DATA


def test_without_a_rest_window_the_search_is_limited_only_by_the_wait():
    reps, signal = recovering_reps(16, with_rest=False)

    result = family_for(*reps).extract(make_context(signal))

    assert computed(result)[fid("recovery_time_s")].value == pytest.approx(1.6)


@pytest.mark.parametrize(("low", "recovers"), [(0.95, True), (1.0, True), (1.05, False)])
def test_recovered_means_at_or_below_a_tenth_of_the_repetition_peak(low: float, recovers: bool):
    # pico 10 mm: umbral 1 mm (incluido)
    reps, signal = recovering_reps(3, low=low)

    result = family_for(*reps).extract(make_context(signal))

    if recovers:
        assert computed(result)[fid("recovery_time_s")].value == pytest.approx(0.3)
    else:
        assert skipped(result)[fid("recovery_time_s")] is Reason.NO_DATA


def test_the_threshold_uses_the_peak_of_each_repetition():
    # rep 1 with peak 10 (threshold 1) and rep 2 with peak 30 (threshold 3): 2 mm recovers only the second
    signal = ohm([10.0] * 10 + [5.0] * 2 + [2.0] * 28 + [30.0] * 10 + [5.0] * 2 + [2.0] * 28)
    reps = (window(1, 0, 10, 40), window(2, 40, 50, 80))

    result = family_for(*reps).extract(make_context(signal))

    assert computed(result)[fid("recovery_time_s")].value == pytest.approx(0.2)
    assert computed(result)[fid("recovery_time_s")].n == 1


def test_the_recovery_wait_of_the_calibration_wins_over_the_default_when_shorter():
    # recovers at 1.5 s: with the calibration's wait (1 s) no; with the default one (3 s) yes
    reps, signal = recovering_reps(15)

    result = family_for(*reps, wait_s=3.0).extract(make_context(signal, [calibration(recovery_wait_s=1.0)]))

    assert skipped(result)[fid("recovery_time_s")] is Reason.NO_DATA


def test_the_recovery_wait_of_the_calibration_wins_over_the_default_when_longer():
    reps, signal = recovering_reps(15)

    result = family_for(*reps, wait_s=1.0).extract(make_context(signal, [calibration(recovery_wait_s=3.0)]))

    assert computed(result)[fid("recovery_time_s")].value == pytest.approx(1.5)


def test_a_zero_recovery_wait_in_the_calibration_is_used_and_not_replaced_by_the_default():
    reps, signal = recovering_reps(0)

    result = family_for(*reps, wait_s=3.0).extract(make_context(signal, [calibration(recovery_wait_s=0.0)]))

    assert skipped(result)[fid("recovery_time_s")] is Reason.NO_DATA


def test_without_recovery_wait_in_the_calibration_the_default_is_used():
    reps, signal = recovering_reps(15)

    longer = family_for(*reps, wait_s=3.0).extract(make_context(signal))
    shorter = family_for(*reps, wait_s=1.0).extract(make_context(signal))

    assert computed(longer)[fid("recovery_time_s")].value == pytest.approx(1.5)
    assert skipped(shorter)[fid("recovery_time_s")] is Reason.NO_DATA


def test_the_passive_samples_also_use_the_recovery_wait_of_the_calibration():
    # rep [0,10); calibration with a 1 s wait: excludes [0,20); the tail from 20 is at 7 mm
    signal = ohm([0.0] * 19 + [100.0] + [7.0] * 5)

    result = family_for(window(1, 0, 10, None), wait_s=5.0).extract(make_context(signal, [calibration(recovery_wait_s=1.0)]))

    assert computed(result)[fid("elongation_mm")].value == pytest.approx(7.0)


def test_a_chunk_of_another_kind_under_the_stretch_stream_id_is_ignored():
    payload = make_context(ohm([3.0] * 10)).request.model_dump(mode="json")
    emg = next(c for c in json.loads(_EXAMPLE.read_text(encoding="utf-8"))["chunks"] if c["stream_kind"] == "emg")
    payload["chunks"].append({**emg, "stream_id": STREAM, "seq": 1, "t0_device_ms": ANCHOR_DEVICE_MS + 1000})

    result = family_for().extract(build_context(FeaturesRequest.model_validate(payload)))

    assert values(result)[fid("elongation_mm")] == pytest.approx(3.0)
    assert computed(result)[fid("elongation_mm")].n == 10


@pytest.mark.property
@settings(deadline=None, max_examples=25)
@given(offset=st.integers(min_value=0, max_value=19), peak=st.floats(min_value=1.0, max_value=90.0, allow_nan=False))
@example(offset=0, peak=1.0)
@example(offset=19, peak=90.0)
def test_the_recovery_time_is_the_offset_of_the_first_sample_at_or_below_the_threshold(offset: int, peak: float):
    # two equal reps with peak ``peak``; before ``offset`` the elongation is half the peak (not recovered)
    signal = ohm(([peak] * 10 + [peak / 2.0] * offset + [0.0] * (40 - offset)) * 2)
    reps = (window(1, 0, 10, 50), window(2, 50, 60, 100))

    result = family_for(*reps).extract(make_context(signal))

    assert computed(result)[fid("recovery_time_s")].value == pytest.approx(offset / 10.0)
    assert computed(result)[fid("peak_elongation_mm")].value == pytest.approx(peak)


def test_recovery_fraction_is_configurable():
    reps, signal = recovering_reps(3, low=2.0)
    result = StretchFamily(FakeService(reps), default_recovery_wait_s=2.0, recovery_fraction=0.25).extract(make_context(signal))
    assert computed(result)[fid("recovery_time_s")].value == pytest.approx(0.3)


def test_default_registry_includes_stretch():
    from spine_engine.features.registry import default_registry

    assert "stretch" in [family.family for family in default_registry()]


def test_measured_stretch_requires_straight_reference():
    from spine_engine.features.measured import MeasuredFamily
    from spine_engine.measurements import STRETCH

    marked = calibration()
    marked["reference_posture"] = "straight_back"
    result = MeasuredFamily().extract(make_context([1040.0] * 50, [marked]))
    assert next(f.value for f in result.computed if f.feature_id == STRETCH) == pytest.approx(4)
    unmarked = MeasuredFamily().extract(make_context([1040.0] * 50, [calibration()]))
    assert STRETCH in {s.feature_id for s in unmarked.skipped}


def test_measured_stretch_rejects_missing_time_despite_full_sample_count():
    from spine_engine.features.context import build_context
    from spine_engine.features.measured import MeasuredFamily
    from spine_engine.measurements import STRETCH

    marked = calibration()
    marked["reference_posture"] = "straight_back"
    ctx = make_context([1040.0] * 50, [marked])
    request = ctx.request.model_copy(deep=True)
    request.chunks[1].t0_device_ms += 100
    result = MeasuredFamily().extract(build_context(request))
    assert STRETCH not in values(result)


def test_measured_stretch_ignores_old_saturation_and_packet_gap():
    from spine_engine.features.measured import MeasuredFamily
    from spine_engine.measurements import STRETCH

    marked = calibration()
    marked["reference_posture"] = "straight_back"
    ctx = make_context([1040.0] * 100, [marked], counts=[0] * 10 + [500] * 90, circuit={"adc_bits": 10})
    request = ctx.request.model_copy(deep=True)
    request.chunks.pop(1)
    assert values(MeasuredFamily().extract(build_context(request)))[STRETCH] == pytest.approx(4)


def test_measured_feature_keeps_its_five_second_window_in_session_aggregate():
    from spine_contracts import Completeness, Level
    from spine_engine.features.composite import CompositeFeatureExtractor
    from spine_engine.features.measured import MeasuredFamily
    from spine_engine.features.registry import ExtractorRegistry
    from spine_engine.interfaces import RiskResult, TriageContext
    from spine_engine.measurements import STRETCH, measurements
    from spine_engine.triage import DeclarativeTriagePolicy, PolicyConfig

    marked = calibration()
    marked["reference_posture"] = "straight_back"
    context = make_context([1000.0] * 50 + [1075.0] * 50, [marked])
    registry = ExtractorRegistry()
    registry.register(MeasuredFamily())
    output = CompositeFeatureExtractor(registry).extract(context.request)
    assert output.window.end - output.window.start == timedelta(seconds=10)
    feature = next(f for f in output.features if f.feature_id == STRETCH)
    assert feature.window.end - feature.window.start == timedelta(seconds=5)
    assert measurements([output], output.window.end)[STRETCH] == pytest.approx(7.5)
    decision = DeclarativeTriagePolicy(PolicyConfig()).decide(
        TriageContext(RiskResult(0), [], Completeness(score=1, missing=[]), [], output.window.end, [output])
    )
    assert decision.level == Level.RED


def test_requested_window_can_assess_a_historical_five_seconds_without_aggregate_features():
    from spine_contracts.models.engine.features_request_schema import RequestedWindow
    from spine_engine.features.composite import CompositeFeatureExtractor
    from spine_engine.features.registry import default_registry
    from spine_engine.measurements import STRETCH

    marked = calibration()
    marked["reference_posture"] = "straight_back"
    context = make_context([1075.0] * 50 + [1000.0] * 50, [marked])
    req = context.request.model_copy(update={"requested_window": RequestedWindow(start=at(0), end=at(50))})
    result = CompositeFeatureExtractor(default_registry()).extract(req)
    assert result.window.start == at(0) and result.window.end == at(50)
    assert {f.feature_id: f.value for f in result.features}[STRETCH] == pytest.approx(7.5)
    assert not any(f.feature_id.startswith("exercise.") for f in result.features)


def test_requested_window_with_no_chunks_is_incomplete():
    from spine_contracts.models.engine.features_request_schema import RequestedWindow
    from spine_engine.features.composite import CompositeFeatureExtractor
    from spine_engine.features.registry import default_registry
    from spine_engine.measurements import UNITS

    context = make_context([1000.0] * 50, [calibration()])
    req = context.request.model_copy(update={"chunks": [], "requested_window": RequestedWindow(start=at(0), end=at(50))})
    result = CompositeFeatureExtractor(default_registry()).extract(req)
    assert result.features == []
    assert {item.feature_id for item in result.skipped} == set(UNITS)
    assert result.window.start == at(0) and result.window.end == at(50)


def test_requested_window_truncates_future_samples_inside_a_chunk():
    from spine_contracts.models.engine.features_request_schema import RequestedWindow
    from spine_engine.features.context import build_context

    context = make_context([1000.0] * 100, [calibration()])
    req = context.request.model_copy(update={"requested_window": RequestedWindow(start=at(5), end=at(55))})
    bounded = build_context(req)
    chunks = [chunk for segment in bounded.segments(STREAM) for chunk in segment.chunks]
    assert sum(chunk.n_samples for chunk in chunks) == 55
    assert chunks[-1].n_samples == 5
