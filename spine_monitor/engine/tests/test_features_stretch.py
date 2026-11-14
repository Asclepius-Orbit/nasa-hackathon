"""Familia ``stretch`` (#45): elongación del cordón, pico y rango por repetición, recuperación y resistencia de reposo.

Rejilla de tiempo: 10 Hz (una muestra cada 100 ms) desde ``T0``; la muestra ``i`` cae en ``T0 + 100 ms·i``.
Curva de los tests: (1000 Ω, 100 mm) y (2000 Ω, 200 mm) con reposo de 100 mm, así que la elongación en mm es
``(R − 1000) / 10`` y ``R = 1000 + 10·e``. Las repeticiones se fijan con un ``FakeService`` (frontera).
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
T0 = datetime(2026, 11, 13, 10, 3, 0, 100000, tzinfo=UTC)  # UTC de la ancla de reloj del ejemplo (device 180000 ms)
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
    """Repetición con la activa [start, end) y el reposo [end, rest_end), en muestras de la rejilla."""
    return RepWindow(index, at(start), at(end), at(end) if rest_end is not None else None, at(rest_end) if rest_end is not None else None)


class FakeService(RepetitionService):
    """Frontera: devuelve unas ventanas fijas, sin mirar el contexto."""

    def __init__(self, windows: tuple[RepWindow, ...]) -> None:
        super().__init__()
        self._result = RepWindows(windows, "events" if windows else "none")

    def windows(self, context: ExtractionContext) -> RepWindows:
        return self._result


def ohm(elongations: list[float]) -> list[float]:
    """Resistencias que dan esas elongaciones (mm) con la curva de los tests."""
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
    """Contexto con un único stream stretch declarado (``region``), sus chunks y calibraciones."""
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


# --- sin repeticiones: lo pasivo -----------------------------------------------------------------------------


def test_the_family_declares_every_concrete_catalog_id_of_the_stretch_features():
    family = family_for()

    assert family.family == "stretch"
    names = (*NAMES, "straight_resistance_ohm", "max_flexion_resistance_ohm")
    assert tuple(family.feature_ids) == tuple(i for name in names for i in expand_template(f"stretch.{{spine_region}}.{name}"))
    assert set(family.feature_ids) <= set(all_feature_ids())
    assert fid("elongation_mm", "cervical") in family.feature_ids


def test_without_repetitions_the_passive_features_are_the_means_over_all_samples():
    # elongaciones 2 y 4 mm a partes iguales: media 3 mm = 3 % de 100 mm; R media 1030 Ω
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


# --- de las cuentas ADC a la resistencia ---------------------------------------------------------------------

CIRCUIT = {"adc_bits": 10, "vref_v": 3.3, "series_resistor_ohm": 1000.0}
# 10 bits (full 1023) y Rs 1000 Ω: 341 cuentas -> 500 Ω, 682 -> 2000 Ω; con la curva (500, 100) (2000, 250) y
# reposo 100 mm, 500 Ω es elongación 0 y 2000 Ω, 150 mm.
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
    # 0 y 1023 (= 2^10 − 1) no son invertibles: salen NaN y no cuentan
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
    # primer chunk sin circuito ni resistencia; el segundo con resistencia derivada de 5 mm
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


# --- calibración, stream ausente y región --------------------------------------------------------------------

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
    # la sesión apunta a una curva que daría 2·e; el chunk, a la de los tests
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
    # el ejemplo con sus chunks de EMG e IMU pero sin los del cordón lumbar declarado
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
    # el ejemplo declara también EMG e IMU: solo el cordón lumbar da features de esta familia
    context = build_context(FeaturesRequest.model_validate(json.loads(_EXAMPLE.read_text(encoding="utf-8"))))

    result = family_for().extract(context)

    assert {f.feature_id for f in (*result.computed, *result.skipped)} <= {fid(n) for n in NAMES}


def test_no_declared_stretch_stream_gives_an_empty_result():
    payload = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    payload["session"]["streams"] = [s for s in payload["session"]["streams"] if s["stream_kind"] != "stretch"]

    result = family_for().extract(build_context(FeaturesRequest.model_validate(payload)))

    assert result == FamilyResult()


# --- por repetición: pico y rango ----------------------------------------------------------------------------

# rep 1: activa [0,10) con 2 y 8 mm, reposo [10,30) que baja a 0,5 mm en la muestra 12 y a 0 después;
# rep 2: activa [30,40) con 1 y 10 mm, reposo [40,60) que baja a 0,9 mm en la 44; cola pasiva [60,70) a 3 mm.
REP1 = [2.0] * 5 + [8.0] * 5 + [4.0, 4.0, 0.5] + [0.0] * 17
REP2 = [1.0] * 5 + [10.0] * 5 + [5.0] * 4 + [0.9] + [0.0] * 15
TAIL = [3.0] * 10
TWO_REPS = (window(1, 0, 10, 30), window(2, 30, 40, 60))
TWO_REP_SIGNAL = ohm(REP1 + REP2 + TAIL)
# rep 3: activa [70,80) con 0 y 6 mm, sin reposo ni muestras después
THREE_REPS = (*TWO_REPS, window(3, 70, 80, None))
THREE_REP_SIGNAL = ohm(REP1 + REP2 + TAIL + [0.0] * 5 + [6.0] * 5)


def test_peak_and_range_are_the_means_of_each_repetition_max_and_max_minus_min():
    result = family_for(*TWO_REPS).extract(make_context(TWO_REP_SIGNAL))

    by_id = computed(result)
    assert by_id[fid("peak_elongation_mm")].value == pytest.approx(9.0)  # (8 + 10) / 2, no el máximo global 10
    assert by_id[fid("range_mm")].value == pytest.approx(7.5)  # (6 + 9) / 2, no el rango global 9
    assert by_id[fid("peak_elongation_mm")].n == 2
    assert by_id[fid("range_mm")].n == 2


def test_peak_and_range_with_three_repetitions():
    result = family_for(*THREE_REPS).extract(make_context(THREE_REP_SIGNAL))

    by_id = computed(result)
    assert by_id[fid("peak_elongation_mm")].value == pytest.approx(8.0)  # (8 + 10 + 6) / 3
    assert by_id[fid("range_mm")].value == pytest.approx(7.0)  # (6 + 9 + 6) / 3
    assert by_id[fid("peak_elongation_mm")].n == 3


def test_a_negative_peak_is_kept():
    # el cordón por debajo de su reposo en las dos activas: -3 y -1 mm (la curva baja hasta 50 mm)
    signal = ohm([-3.0] * 10 + [-1.0] * 10)
    curve = [calibration(points=[(500.0, 50.0), (1000.0, 100.0), (2000.0, 200.0)])]

    result = family_for(window(1, 0, 10, None), window(2, 10, 20, None)).extract(make_context(signal, curve))

    assert computed(result)[fid("peak_elongation_mm")].value == pytest.approx(-2.0)
    assert computed(result)[fid("range_mm")].value == pytest.approx(0.0)


def test_the_active_window_is_half_open():
    # activas [2,4) y [6,8): las muestras 4 y 8 (100 mm) no cuentan; la 2 y la 6 sí
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


# --- lo pasivo con repeticiones --------------------------------------------------------------------------------


def test_with_repetitions_the_passive_features_skip_the_active_windows_and_their_recovery_wait():
    # con la espera de 2 s (20 muestras) desde cada fin de activa solo queda la cola [60,70) a 3 mm
    result = family_for(*TWO_REPS).extract(make_context(TWO_REP_SIGNAL))

    by_id = computed(result)
    assert by_id[fid("elongation_mm")].value == pytest.approx(3.0)
    assert by_id[fid("elongation_pct")].value == pytest.approx(3.0)
    assert by_id[fid("rest_resistance_ohm")].value == pytest.approx(1030.0)
    assert by_id[fid("elongation_mm")].n == 10


def test_the_passive_samples_start_exactly_when_the_recovery_wait_ends():
    # una rep [0,10) con espera de 1 s: excluye [0,20); la muestra 20 (7 mm) ya cuenta, la 19 (100 mm) no
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


# --- recuperación ---------------------------------------------------------------------------------------------


def recovering_reps(offset: int, *, rest: int = 40, low: float = 0.0, with_rest: bool = True) -> tuple[tuple[RepWindow, ...], list[float]]:
    """Dos repeticiones iguales: activa de 10 muestras a 10 mm y luego ``rest`` muestras a 5 mm hasta ``offset``
    (muestras desde el fin de la activa) y a ``low`` mm desde ahí. El umbral es la fracción por defecto (0,1) · 10 mm."""
    period = 10 + rest
    signal = ([10.0] * 10 + [5.0] * offset + [low] * (rest - offset)) * 2
    reps = tuple(window(i + 1, i * period, i * period + 10, i * period + period if with_rest else None) for i in range(2))
    return reps, ohm(signal)


def test_the_recovery_time_is_the_mean_time_from_the_end_of_each_repetition_to_the_first_recovered_sample():
    result = family_for(*TWO_REPS).extract(make_context(TWO_REP_SIGNAL))

    assert computed(result)[fid("recovery_time_s")].value == pytest.approx(0.3)  # (0,2 + 0,4) / 2
    assert computed(result)[fid("recovery_time_s")].n == 2


def test_a_repetition_that_does_not_recover_is_left_out_of_the_recovery_time():
    # la rep 3 no tiene muestras tras su activa: no recupera y no cuenta
    result = family_for(*THREE_REPS).extract(make_context(THREE_REP_SIGNAL))

    assert computed(result)[fid("recovery_time_s")].value == pytest.approx(0.3)
    assert computed(result)[fid("recovery_time_s")].n == 2


def test_a_sample_recovered_right_at_the_end_of_the_repetition_gives_zero():
    reps, signal = recovering_reps(0)

    result = family_for(*reps).extract(make_context(signal))

    assert computed(result)[fid("recovery_time_s")].value == 0.0


def test_the_last_sample_inside_the_recovery_wait_still_counts():
    # espera 2 s = 20 muestras: la búsqueda es [fin, fin + 2 s); la muestra 19 tras el fin (1,9 s) está dentro
    reps, signal = recovering_reps(19)

    result = family_for(*reps).extract(make_context(signal))

    assert computed(result)[fid("recovery_time_s")].value == pytest.approx(1.9)


def test_no_recovery_within_the_wait_skips_the_recovery_time_as_no_data():
    reps, signal = recovering_reps(20)

    result = family_for(*reps).extract(make_context(signal))

    assert skipped(result)[fid("recovery_time_s")] is Reason.NO_DATA
    assert fid("peak_elongation_mm") in computed(result)


def test_the_rest_window_limits_the_search():
    # reposo de 15 muestras y recuperación en la 16: dentro de la espera de 2 s pero ya fuera del reposo
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
    # rep 1 con pico 10 (umbral 1) y rep 2 con pico 30 (umbral 3): 2 mm recupera solo la segunda
    signal = ohm([10.0] * 10 + [5.0] * 2 + [2.0] * 28 + [30.0] * 10 + [5.0] * 2 + [2.0] * 28)
    reps = (window(1, 0, 10, 40), window(2, 40, 50, 80))

    result = family_for(*reps).extract(make_context(signal))

    assert computed(result)[fid("recovery_time_s")].value == pytest.approx(0.2)
    assert computed(result)[fid("recovery_time_s")].n == 1


def test_the_recovery_wait_of_the_calibration_wins_over_the_default_when_shorter():
    # recupera a 1,5 s: con la espera de la calibración (1 s) no; con la de por defecto (3 s) sí
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
    # rep [0,10); calibración con espera de 1 s: excluye [0,20); la cola desde la 20 está a 7 mm
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
    # dos reps iguales con pico ``peak``; antes de ``offset`` la elongación es la mitad del pico (no recuperada)
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
