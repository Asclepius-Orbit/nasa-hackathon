"""Familia ``emg`` (#42): RMS de la fase activa y del reposo, %MVC, pico en ventana y simetría entre lados.

Rejilla de tiempo de los tests: 100 Hz (una muestra cada 10 ms) desde ``T0``; la muestra ``i`` cae en ``T0 + 10 ms·i``.
Las repeticiones se fijan con un ``FakeService`` (frontera) salvo en los tests que ejercitan el servicio real.
"""

import dataclasses
import json
import math
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
from spine_engine.features.clock import DeviceClock
from spine_engine.features.context import ExtractionContext, build_context
from spine_engine.features.emg import EmgFamily
from spine_engine.features.registry import ComputedFeature, FamilyResult, SkippedFeature, default_registry
from spine_engine.features.repetitions import RepetitionService, RepWindow, RepWindows

_EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "features-request.json"
T0 = datetime(2026, 11, 13, 10, 3, 0, 100000, tzinfo=UTC)  # instante UTC de la ancla de reloj del ejemplo (device 180000 ms)
ANCHOR_DEVICE_MS = 180_000
BOOT = 2893746120
RATE_HZ = 100.0
MUSCLE = "latissimus_dorsi"
LEFT, RIGHT = "emg_lat_left", "emg_lat_right"
PREFIX_LEFT = f"emg.{MUSCLE}.left"
PREFIX_RIGHT = f"emg.{MUSCLE}.right"
SYMMETRY = f"emg.{MUSCLE}.symmetry_index_pct"
CAL_IDS = {LEFT: "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f12", RIGHT: "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f22"}


def at(samples: int) -> datetime:
    """UTC de la muestra ``samples`` de la rejilla."""
    return T0 + timedelta(milliseconds=10 * samples)


def window(index: int, start: int, end: int, rest_end: int | None) -> RepWindow:
    """Repetición con la activa [start, end) y el reposo [end, rest_end), todo en muestras de la rejilla."""
    return RepWindow(index, at(start), at(end), at(end) if rest_end is not None else None, at(rest_end) if rest_end is not None else None)


class FakeService(RepetitionService):
    """Frontera: devuelve unas ventanas fijas, sin mirar el contexto."""

    def __init__(self, windows: tuple[RepWindow, ...]) -> None:
        super().__init__()
        self._result = RepWindows(windows, "events" if windows else "none")

    def windows(self, context: ExtractionContext) -> RepWindows:
        return self._result


def alternating(values: list[float]) -> list[float]:
    """Alterna el signo: el RMS no cambia, una media sí."""
    return [v if i % 2 == 0 else -v for i, v in enumerate(values)]


def calibration(
    stream_id: str, side: str, mvc: float, *, window_ms: int = 50, valid_until: datetime | None = None, signal_kind: str = "raw", muscle: str = MUSCLE
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "calibration_id": CAL_IDS[stream_id],
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "stream_id": stream_id,
        "created_at": "2026-11-13T07:00:00Z",
        "valid_until": valid_until.isoformat() if valid_until else None,
        "source": "device",
        "calibration_type": "emg_mvc",
        "muscle": muscle,
        "side": side,
        "mvc_rms_mv": mvc,
        "window_ms": window_ms,
        "signal_kind": signal_kind,
    }


def chunks_of(stream_id: str, samples: list[float], signal_kind: str, size: int = 40) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seq, start in enumerate(range(0, len(samples), size)):
        part = samples[start : start + size]
        out.append(
            {
                "schema_version": "1.0.0",
                "astronaut_id": "AST-01",
                "device_id": "wb-01",
                "stream_id": stream_id,
                "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01",
                "source": "device",
                "boot_id": BOOT,
                "seq": seq,
                "t0_device_ms": ANCHOR_DEVICE_MS + 10 * start,
                "received_at": T0.isoformat(),
                "sample_rate_hz": RATE_HZ,
                "n_samples": len(part),
                "stream_kind": "emg",
                "signal_kind": signal_kind,
                "units": {"emg": "mV"},
                "data": {"emg": part},
                "quality": {"emg": {"score": 1.0, "flags": [], "assessed_by": "gateway"}},
            }
        )
    return out


def make_context(
    samples: dict[str, list[float]],
    calibrations: list[dict[str, Any]],
    *,
    declared: tuple[str, ...] | None = None,
    signal_kind: str = "raw",
    last_chunk_kind: str | None = None,
    events: list[dict[str, Any]] | None = None,
) -> ExtractionContext:
    """Contexto con los streams EMG ``declared`` (por defecto los de ``samples``), sus chunks y calibraciones.

    ``last_chunk_kind`` cambia el ``signal_kind`` del último chunk de cada stream (un stream que mezcla tipos).
    """
    payload = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    sides = {LEFT: "left", RIGHT: "right"}
    session = payload["session"]
    session["streams"] = [
        {
            "stream_id": stream_id,
            "stream_kind": "emg",
            "placement": {"muscle": MUSCLE, "side": sides[stream_id]},
            "sample_rate_hz": RATE_HZ,
            "calibration_id": CAL_IDS[stream_id],
        }
        for stream_id in (declared if declared is not None else tuple(samples))
    ]
    if events is not None:
        session["events"] = events
    payload["chunks"] = []
    for stream_id, values in samples.items():
        chunks = chunks_of(stream_id, values, signal_kind)
        if last_chunk_kind is not None:
            chunks[-1]["signal_kind"] = last_chunk_kind
        payload["chunks"].extend(chunks)
    payload["calibrations"] = calibrations
    return build_context(FeaturesRequest.model_validate(payload))


def ev(kind: str, samples: int, rep: int) -> dict[str, Any]:
    return {"event_type": kind, "timestamp": at(samples).isoformat(), "device_ms": None, "boot_id": None, "rep_index": rep}


def family_for(*windows: RepWindow) -> EmgFamily:
    return EmgFamily(FakeService(windows))


def computed(result: FamilyResult) -> dict[str, ComputedFeature]:
    return {f.feature_id: f for f in result.computed}


def skipped(result: FamilyResult) -> dict[str, Reason]:
    return {s.feature_id: s.reason for s in result.skipped}


# Dos repeticiones: activa [0,30) y reposo [30,50); activa [50,80) y reposo [80,100).
TWO_REPS = (window(1, 0, 30, 50), window(2, 50, 80, 100))
# Izquierda: rep 1 = 25 muestras de 1 mV y 5 de 3 mV (el último RMS de 5 muestras vale 3); rep 2 = 30 de 1 mV; reposo 0,1 mV.
LEFT_SIGNAL = alternating([1.0] * 25 + [3.0] * 5) + [0.1, -0.1] * 10 + alternating([1.0] * 30) + [0.1, -0.1] * 10
# Derecha: 2 mV constantes en la activa; reposo 0,2 mV.
RIGHT_SIGNAL = alternating([2.0] * 30) + [0.2, -0.2] * 10 + alternating([2.0] * 30) + [0.2, -0.2] * 10
LEFT_RMS = math.sqrt((25 * 1 + 5 * 9 + 30 * 1) / 60)
LEFT_MVC, RIGHT_MVC = 2.0, 4.0
LEFT_PCT = 100.0 * LEFT_RMS / LEFT_MVC
RIGHT_PCT = 100.0 * 2.0 / RIGHT_MVC
SAMPLES = {LEFT: LEFT_SIGNAL, RIGHT: RIGHT_SIGNAL}


def both_calibrated(**kwargs: Any) -> list[dict[str, Any]]:
    return [calibration(LEFT, "left", LEFT_MVC, **kwargs), calibration(RIGHT, "right", RIGHT_MVC, **kwargs)]


def test_the_family_declares_every_concrete_catalog_id_of_the_amplitude_features():
    family = EmgFamily(RepetitionService())
    templates = (
        "emg.{muscle}.{side}.rms_mv",
        "emg.{muscle}.{side}.rest_rms_mv",
        "emg.{muscle}.{side}.rms_pct_mvc",
        "emg.{muscle}.{side}.peak_pct_mvc",
        "emg.{muscle}.symmetry_index_pct",
    )

    assert family.family == "emg"
    assert tuple(family.feature_ids) == tuple(i for t in templates for i in expand_template(t))
    assert set(family.feature_ids) <= set(all_feature_ids())
    assert f"{PREFIX_LEFT}.rms_mv" in family.feature_ids
    assert SYMMETRY in family.feature_ids


def test_known_signals_give_exact_amplitude_mvc_peak_and_rest_values():
    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, both_calibrated()))

    by_id = computed(result)
    assert by_id[f"{PREFIX_LEFT}.rms_mv"].value == pytest.approx(LEFT_RMS)
    assert by_id[f"{PREFIX_LEFT}.rest_rms_mv"].value == pytest.approx(0.1)
    assert by_id[f"{PREFIX_LEFT}.rms_pct_mvc"].value == pytest.approx(LEFT_PCT)
    assert by_id[f"{PREFIX_LEFT}.peak_pct_mvc"].value == pytest.approx(150.0)  # ventana de 5 muestras con 3 mV / MVC 2 mV
    assert by_id[f"{PREFIX_RIGHT}.rms_mv"].value == pytest.approx(2.0)
    assert by_id[f"{PREFIX_RIGHT}.rest_rms_mv"].value == pytest.approx(0.2)
    assert by_id[f"{PREFIX_RIGHT}.rms_pct_mvc"].value == pytest.approx(RIGHT_PCT)
    assert by_id[f"{PREFIX_RIGHT}.peak_pct_mvc"].value == pytest.approx(50.0)
    assert result.skipped == ()


def test_each_feature_names_its_stream_and_counts_the_repetitions_that_contributed():
    by_id = computed(family_for(*TWO_REPS).extract(make_context(SAMPLES, both_calibrated())))

    for name in ("rms_mv", "rest_rms_mv", "rms_pct_mvc", "peak_pct_mvc"):
        assert by_id[f"{PREFIX_LEFT}.{name}"].streams == (LEFT,)
        assert by_id[f"{PREFIX_RIGHT}.{name}"].streams == (RIGHT,)
        assert by_id[f"{PREFIX_LEFT}.{name}"].n == 2
    assert by_id[SYMMETRY].streams == (LEFT, RIGHT)
    assert by_id[SYMMETRY].n == 2


def test_the_symmetry_index_is_the_relative_difference_of_the_two_pct_mvc():
    by_id = computed(family_for(*TWO_REPS).extract(make_context(SAMPLES, both_calibrated())))

    assert by_id[SYMMETRY].value == pytest.approx(100.0 * abs(LEFT_PCT - RIGHT_PCT) / ((LEFT_PCT + RIGHT_PCT) / 2.0))


def test_the_output_order_is_deterministic_per_stream_then_symmetry():
    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, both_calibrated()))

    assert [f.feature_id for f in result.computed] == [
        *(f"{PREFIX_LEFT}.{n}" for n in ("rms_mv", "rest_rms_mv", "rms_pct_mvc", "peak_pct_mvc")),
        *(f"{PREFIX_RIGHT}.{n}" for n in ("rms_mv", "rest_rms_mv", "rms_pct_mvc", "peak_pct_mvc")),
        SYMMETRY,
    ]


@pytest.mark.parametrize("signal_kind", ["envelope", "rectified"])
def test_an_envelope_or_rectified_signal_gives_the_same_amplitude_features(signal_kind: str):
    raw = computed(family_for(*TWO_REPS).extract(make_context(SAMPLES, both_calibrated())))
    # con la MVC medida en el mismo tipo de señal (la envolvente solo es compatible consigo misma)
    other = computed(family_for(*TWO_REPS).extract(make_context(SAMPLES, both_calibrated(signal_kind=signal_kind), signal_kind=signal_kind)))

    assert {k: v.value for k, v in other.items()} == {k: v.value for k, v in raw.items()}


MVC_IDS = {f"{p}.{n}" for p in (PREFIX_LEFT, PREFIX_RIGHT) for n in ("rms_pct_mvc", "peak_pct_mvc")} | {SYMMETRY}
AMPLITUDE_IDS = {f"{p}.{n}" for p in (PREFIX_LEFT, PREFIX_RIGHT) for n in ("rms_mv", "rest_rms_mv")}


def test_an_mvc_measured_on_the_envelope_does_not_apply_to_raw_chunks():
    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, both_calibrated(signal_kind="envelope")))

    assert skipped(result) == dict.fromkeys(MVC_IDS, Reason.NO_CALIBRATION)
    assert computed(result)[f"{PREFIX_LEFT}.rms_mv"].value == pytest.approx(LEFT_RMS)
    assert set(computed(result)) == AMPLITUDE_IDS


def test_an_mvc_measured_on_the_raw_signal_does_not_apply_to_envelope_chunks():
    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, both_calibrated(), signal_kind="envelope"))

    assert skipped(result) == dict.fromkeys(MVC_IDS, Reason.NO_CALIBRATION)


@pytest.mark.parametrize(("calibrated", "chunks"), [("raw", "rectified"), ("rectified", "raw"), ("envelope", "envelope")])
def test_compatible_signal_kinds_give_the_mvc_features(calibrated: str, chunks: str):
    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, both_calibrated(signal_kind=calibrated), signal_kind=chunks))

    assert computed(result)[f"{PREFIX_LEFT}.rms_pct_mvc"].value == pytest.approx(LEFT_PCT)
    assert set(computed(result)) == AMPLITUDE_IDS | MVC_IDS


def test_a_single_incompatible_chunk_makes_the_mvc_unusable_for_its_stream():
    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, both_calibrated(), last_chunk_kind="envelope"))

    assert skipped(result) == dict.fromkeys(MVC_IDS, Reason.NO_CALIBRATION)


def test_an_mvc_of_another_muscle_is_skipped_as_no_calibration():
    calibrations = [calibration(LEFT, "left", LEFT_MVC, muscle="trapezius"), calibration(RIGHT, "right", RIGHT_MVC)]

    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, calibrations))

    assert skipped(result) == {
        f"{PREFIX_LEFT}.rms_pct_mvc": Reason.NO_CALIBRATION,
        f"{PREFIX_LEFT}.peak_pct_mvc": Reason.NO_CALIBRATION,
        SYMMETRY: Reason.NO_CALIBRATION,
    }


def test_an_mvc_of_the_other_side_is_skipped_as_no_calibration():
    calibrations = [calibration(LEFT, "right", LEFT_MVC), calibration(RIGHT, "right", RIGHT_MVC)]

    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, calibrations))

    assert skipped(result) == {
        f"{PREFIX_LEFT}.rms_pct_mvc": Reason.NO_CALIBRATION,
        f"{PREFIX_LEFT}.peak_pct_mvc": Reason.NO_CALIBRATION,
        SYMMETRY: Reason.NO_CALIBRATION,
    }


def test_the_active_window_is_half_open_start_included_end_excluded():
    # una activa [2, 4) con muestras 5, 7, 9, 11, 13, 15: solo cuentan la 2 y la 3 (9 y 11)
    context = make_context({LEFT: [5.0, 7.0, 9.0, 11.0, 13.0, 15.0]}, [calibration(LEFT, "left", 10.0)])

    result = family_for(window(1, 2, 4, None)).extract(context)

    assert computed(result)[f"{PREFIX_LEFT}.rms_mv"].value == pytest.approx(math.sqrt((81 + 121) / 2))


def test_the_peak_uses_the_calibration_window_in_samples():
    # 100 Hz y ventana de 20 ms = 2 muestras: ventanas (3,3)->3, (3,1)->sqrt(5), (1,1)->1
    context = make_context({LEFT: [3.0, 3.0, 1.0, 1.0]}, [calibration(LEFT, "left", 1.0, window_ms=20)])

    result = family_for(window(1, 0, 4, None)).extract(context)

    assert computed(result)[f"{PREFIX_LEFT}.peak_pct_mvc"].value == pytest.approx(300.0)
    assert computed(result)[f"{PREFIX_LEFT}.rms_pct_mvc"].value == pytest.approx(100.0 * math.sqrt(5.0))


def test_a_window_of_one_sample_makes_the_peak_the_largest_absolute_sample():
    context = make_context({LEFT: [1.0, -4.0, 2.0]}, [calibration(LEFT, "left", 2.0, window_ms=10)])

    result = family_for(window(1, 0, 3, None)).extract(context)

    assert computed(result)[f"{PREFIX_LEFT}.peak_pct_mvc"].value == pytest.approx(200.0)


def test_a_repetition_shorter_than_the_calibration_window_uses_all_its_samples_as_one_window():
    # 3 muestras y ventana de 50 ms (5 muestras): RMS de las 3
    context = make_context({LEFT: [1.0, 2.0, 2.0]}, [calibration(LEFT, "left", 1.0, window_ms=50)])

    result = family_for(window(1, 0, 3, None)).extract(context)

    assert computed(result)[f"{PREFIX_LEFT}.peak_pct_mvc"].value == pytest.approx(100.0 * math.sqrt(3.0))


def test_the_window_in_samples_is_the_calibration_window_rounded_to_the_nearest_sample():
    # 100 Hz y 35 ms = 3,5 muestras -> 4: la ventana (4,4,4,0) da sqrt(12); con 3 muestras daría 4
    context = make_context({LEFT: [4.0, 4.0, 4.0, 0.0, 0.0]}, [calibration(LEFT, "left", 1.0, window_ms=35)])

    result = family_for(window(1, 0, 5, None)).extract(context)

    assert computed(result)[f"{PREFIX_LEFT}.peak_pct_mvc"].value == pytest.approx(100.0 * math.sqrt(12.0))


def test_a_peak_below_one_millivolt_is_not_raised_to_one():
    context = make_context({LEFT: [0.5, -0.5, 0.5]}, [calibration(LEFT, "left", 1.0, window_ms=20)])

    result = family_for(window(1, 0, 3, None)).extract(context)

    assert computed(result)[f"{PREFIX_LEFT}.peak_pct_mvc"].value == pytest.approx(50.0)


def test_a_symmetry_between_small_pct_mvc_is_still_computed():
    # 0,5 % y 0,25 % de MVC: media 0,375 % (> 0), índice 100·0,25/0,375
    small = {LEFT: [0.01] * 100, RIGHT: [0.005] * 100}

    result = family_for(*TWO_REPS).extract(make_context(small, [calibration(LEFT, "left", 2.0), calibration(RIGHT, "right", 2.0)]))

    assert computed(result)[SYMMETRY].value == pytest.approx(100.0 * 0.25 / 0.375)


def test_the_peak_does_not_mix_samples_of_two_repetitions():
    # activas [0,2) y [4,6) separadas por 2 muestras de reposo enormes: ninguna ventana de 2 cruza de una a otra
    context = make_context({LEFT: [1.0, 1.0, 50.0, 50.0, 1.0, 1.0]}, [calibration(LEFT, "left", 1.0, window_ms=20)])

    result = family_for(window(1, 0, 2, 4), window(2, 4, 6, None)).extract(context)

    assert computed(result)[f"{PREFIX_LEFT}.peak_pct_mvc"].value == pytest.approx(100.0)


def test_without_a_calibration_the_amplitude_is_computed_and_the_mvc_features_are_skipped():
    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, []))

    assert set(computed(result)) == {f"{p}.{n}" for p in (PREFIX_LEFT, PREFIX_RIGHT) for n in ("rms_mv", "rest_rms_mv")}
    assert skipped(result) == {
        **{f"{p}.{n}": Reason.NO_CALIBRATION for p in (PREFIX_LEFT, PREFIX_RIGHT) for n in ("rms_pct_mvc", "peak_pct_mvc")},
        SYMMETRY: Reason.NO_CALIBRATION,
    }


def test_an_expired_calibration_skips_the_mvc_features_as_calibration_expired():
    expired = both_calibrated(valid_until=datetime(2026, 11, 13, 9, 0, tzinfo=UTC))

    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, expired))

    assert set(computed(result)) == {f"{p}.{n}" for p in (PREFIX_LEFT, PREFIX_RIGHT) for n in ("rms_mv", "rest_rms_mv")}
    assert set(skipped(result).values()) == {Reason.CALIBRATION_EXPIRED}
    assert SYMMETRY in skipped(result)


def test_with_the_calibration_of_one_side_only_that_side_has_mvc_and_the_symmetry_is_skipped():
    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, [calibration(LEFT, "left", LEFT_MVC)]))

    assert f"{PREFIX_LEFT}.rms_pct_mvc" in computed(result)
    assert skipped(result) == {
        f"{PREFIX_RIGHT}.rms_pct_mvc": Reason.NO_CALIBRATION,
        f"{PREFIX_RIGHT}.peak_pct_mvc": Reason.NO_CALIBRATION,
        SYMMETRY: Reason.NO_CALIBRATION,
    }


def test_a_missing_calibration_on_either_side_takes_priority_over_an_expired_one_in_the_symmetry():
    expired = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)

    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, [calibration(LEFT, "left", LEFT_MVC, valid_until=expired)]))

    assert skipped(result)[SYMMETRY] is Reason.NO_CALIBRATION
    assert skipped(result)[f"{PREFIX_LEFT}.rms_pct_mvc"] is Reason.CALIBRATION_EXPIRED


def test_without_repetitions_the_active_features_are_skipped_as_too_few_repetitions():
    result = family_for().extract(make_context(SAMPLES, both_calibrated()))

    assert result.computed == ()
    assert skipped(result) == {
        **{f"{p}.{n}": Reason.TOO_FEW_REPETITIONS for p in (PREFIX_LEFT, PREFIX_RIGHT) for n in ("rms_mv", "rms_pct_mvc", "peak_pct_mvc")},
        **{f"{p}.rest_rms_mv": Reason.NO_DATA for p in (PREFIX_LEFT, PREFIX_RIGHT)},
        SYMMETRY: Reason.TOO_FEW_REPETITIONS,
    }


def test_a_repetition_without_rest_still_gives_the_active_features_and_skips_the_rest_rms():
    result = family_for(window(1, 0, 30, None)).extract(make_context(SAMPLES, both_calibrated()))

    assert skipped(result) == {f"{PREFIX_LEFT}.rest_rms_mv": Reason.NO_DATA, f"{PREFIX_RIGHT}.rest_rms_mv": Reason.NO_DATA}
    assert computed(result)[f"{PREFIX_LEFT}.rms_mv"].n == 1


def test_the_rest_rms_uses_only_the_rest_windows_that_have_samples():
    # el segundo reposo [80, 100) queda fuera de los datos (solo hay 60 muestras): n cuenta solo el primero
    samples = {LEFT: LEFT_SIGNAL[:60]}

    result = family_for(*TWO_REPS).extract(make_context(samples, [calibration(LEFT, "left", LEFT_MVC)]))

    assert computed(result)[f"{PREFIX_LEFT}.rest_rms_mv"].n == 1
    assert computed(result)[f"{PREFIX_LEFT}.rest_rms_mv"].value == pytest.approx(0.1)
    assert computed(result)[f"{PREFIX_LEFT}.rms_mv"].n == 2  # la segunda activa [50, 80) tiene 10 muestras


def test_a_declared_stream_without_chunks_is_skipped_as_stream_missing_with_the_symmetry():
    result = family_for(*TWO_REPS).extract(make_context({LEFT: LEFT_SIGNAL}, both_calibrated(), declared=(LEFT, RIGHT)))

    assert {k for k in skipped(result) if k.startswith(PREFIX_RIGHT)} == {
        f"{PREFIX_RIGHT}.{n}" for n in ("rms_mv", "rest_rms_mv", "rms_pct_mvc", "peak_pct_mvc")
    }
    assert {skipped(result)[k] for k in skipped(result) if k.startswith(PREFIX_RIGHT)} == {Reason.STREAM_MISSING}
    assert skipped(result)[SYMMETRY] is Reason.STREAM_MISSING
    assert f"{PREFIX_LEFT}.rms_mv" in computed(result)


def test_a_muscle_with_a_single_declared_side_has_a_symmetry_skipped_as_stream_missing():
    result = family_for(*TWO_REPS).extract(make_context({LEFT: LEFT_SIGNAL}, [calibration(LEFT, "left", LEFT_MVC)]))

    assert skipped(result) == {SYMMETRY: Reason.STREAM_MISSING}


def test_no_declared_emg_stream_gives_an_empty_result():
    payload = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    payload["session"]["streams"] = [s for s in payload["session"]["streams"] if s["stream_kind"] != "emg"]
    context = build_context(FeaturesRequest.model_validate(payload))

    result = family_for(*TWO_REPS).extract(context)

    assert result == FamilyResult()


def test_a_silent_signal_has_no_defined_symmetry_and_is_skipped_as_no_data():
    silent = {LEFT: [0.0] * 100, RIGHT: [0.0] * 100}

    result = family_for(*TWO_REPS).extract(make_context(silent, both_calibrated()))

    assert skipped(result) == {SYMMETRY: Reason.NO_DATA}
    assert computed(result)[f"{PREFIX_LEFT}.rms_pct_mvc"].value == 0.0


def test_a_repetition_with_no_samples_in_its_active_window_gives_no_data():
    result = family_for(window(1, 500, 530, None)).extract(make_context(SAMPLES, both_calibrated()))

    assert result.computed == ()
    assert skipped(result)[f"{PREFIX_LEFT}.rms_mv"] is Reason.NO_DATA
    assert skipped(result)[f"{PREFIX_LEFT}.rms_pct_mvc"] is Reason.NO_DATA
    assert skipped(result)[f"{PREFIX_LEFT}.peak_pct_mvc"] is Reason.NO_DATA
    assert skipped(result)[SYMMETRY] is Reason.NO_DATA


def test_each_muscle_gets_its_own_symmetry():
    payload = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    session = payload["session"]
    pairs = [("emg_lat_left", "latissimus_dorsi", "left"), ("emg_lat_right", "latissimus_dorsi", "right"), ("emg_tra_left", "trapezius", "left")]
    session["streams"] = [{"stream_id": s, "stream_kind": "emg", "placement": {"muscle": m, "side": side}, "sample_rate_hz": RATE_HZ} for s, m, side in pairs]
    payload["calibrations"] = []
    context = build_context(FeaturesRequest.model_validate(payload))

    result = family_for(*TWO_REPS).extract(context)

    assert {s.feature_id for s in result.skipped if s.feature_id.endswith("symmetry_index_pct")} == {
        "emg.latissimus_dorsi.symmetry_index_pct",
        "emg.trapezius.symmetry_index_pct",
    }
    assert [s.reason for s in result.skipped if s.feature_id == "emg.trapezius.symmetry_index_pct"] == [Reason.STREAM_MISSING]


def test_non_emg_streams_are_ignored():
    # el ejemplo declara también un IMU y un stretch: ninguno genera features de EMG
    payload = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    context = build_context(FeaturesRequest.model_validate(payload))

    result = family_for(*TWO_REPS).extract(context)

    own = ("emg.latissimus_dorsi.left.", "emg.latissimus_dorsi.right.")
    assert all(s.feature_id.startswith(own) or s.feature_id.endswith("symmetry_index_pct") for s in result.skipped)


def test_a_non_emg_stream_is_ignored_even_if_its_placement_names_a_muscle_and_a_side():
    # el placement es común a todos los tipos: un IMU puede declarar lado (y músculo); solo el tipo decide
    payload = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    payload["session"]["streams"] = [s for s in payload["session"]["streams"] if s["stream_id"] != RIGHT]
    imu = next(s for s in payload["session"]["streams"] if s["stream_kind"] == "imu")
    imu["placement"].update(muscle=MUSCLE, side="right")
    context = build_context(FeaturesRequest.model_validate(payload))

    result = family_for(*TWO_REPS).extract(context)

    assert not any(f.feature_id.startswith(PREFIX_RIGHT) for f in (*result.computed, *result.skipped))


def test_the_real_service_uses_the_session_events_for_active_and_rest_windows():
    events = [ev("rep_start", 0, 1), ev("rep_end", 30, 1), ev("rep_start", 50, 2), ev("rep_end", 80, 2), ev("rest_end", 100, 2)]
    context = make_context(SAMPLES, both_calibrated(), events=events)

    result = EmgFamily(RepetitionService()).extract(context)

    assert computed(result)[f"{PREFIX_LEFT}.rms_mv"].value == pytest.approx(LEFT_RMS)
    assert computed(result)[f"{PREFIX_LEFT}.rest_rms_mv"].value == pytest.approx(0.1)
    assert computed(result)[f"{PREFIX_LEFT}.rest_rms_mv"].n == 2


def test_a_resolved_calibration_that_is_not_an_emg_mvc_counts_as_no_calibration():
    # no puede darse con streams declarados (la resolución comprueba el tipo), pero la familia no se fía
    context = make_context(SAMPLES, both_calibrated())
    calibrations = FeaturesRequest.model_validate(json.loads(_EXAMPLE.read_text(encoding="utf-8"))).calibrations
    assert calibrations
    stretch = next(c for c in calibrations if c.calibration_type == "stretch")
    wrong = CalibrationResolution(LEFT, CAL_IDS[LEFT], stretch, CalibrationStatus.RESOLVED)
    context = dataclasses.replace(context, calibrations={**context.calibrations, LEFT: wrong})

    result = family_for(*TWO_REPS).extract(context)

    assert skipped(result)[f"{PREFIX_LEFT}.rms_pct_mvc"] is Reason.NO_CALIBRATION
    assert f"{PREFIX_RIGHT}.rms_pct_mvc" in computed(result)


def test_the_default_registry_registers_the_emg_family_after_exercise():
    families = list(default_registry())

    assert [f.family for f in families] == ["exercise", "emg", "imu", "emg_spectral", "stretch"]


@dataclasses.dataclass(frozen=True, slots=True)
class CountingClock(DeviceClock):
    """El reloj del contexto, anotando cada conversión a UTC (el coste por muestra de leer un stream)."""

    calls: list[int] = dataclasses.field(default_factory=list[int])

    def to_utc(self, boot_id: int, device_ms: int) -> datetime | None:
        self.calls.append(device_ms)
        return DeviceClock.to_utc(self, boot_id, device_ms)


def test_the_samples_are_converted_to_utc_a_fixed_number_of_times_whatever_the_number_of_repetitions():
    # una sesión EMG a 1000 Hz tiene cientos de miles de muestras: recorrerlas una vez por repetición la hace lenta
    context = make_context({LEFT: LEFT_SIGNAL}, [calibration(LEFT, "left", LEFT_MVC)])

    def conversions(*windows: RepWindow) -> int:
        clock = CountingClock(context.clock.fits)
        family_for(*windows).extract(dataclasses.replace(context, clock=clock))
        return len(clock.calls)

    assert conversions(*TWO_REPS) == conversions(TWO_REPS[0])


def test_skipped_and_computed_items_are_the_registry_dataclasses():
    result = family_for(*TWO_REPS).extract(make_context(SAMPLES, []))

    assert all(isinstance(s, SkippedFeature) for s in result.skipped)
    assert all(isinstance(c, ComputedFeature) for c in result.computed)


# --- propiedades -----------------------------------------------------------------------------------------------

amplitudes = st.floats(min_value=0.01, max_value=50.0, allow_nan=False, allow_infinity=False)
signals = st.lists(amplitudes, min_size=8, max_size=8)
scales = st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False)
mvcs = st.floats(min_value=0.1, max_value=50.0, allow_nan=False, allow_infinity=False)
ONE_REP = (window(1, 0, 8, None),)


def features_of(samples: dict[str, list[float]], calibrations: list[dict[str, Any]]) -> dict[str, float]:
    result = family_for(*ONE_REP).extract(make_context(samples, calibrations))
    return {f.feature_id: f.value for f in result.computed}


@pytest.mark.property
@settings(deadline=None, max_examples=40)
@given(values=signals, factor=scales)
@example(values=[0.01] * 8, factor=10.0)
@example(values=[50.0] * 8, factor=0.1)
def test_the_rms_scales_with_the_signal(values: list[float], factor: float):
    base = features_of({LEFT: alternating(values)}, [])[f"{PREFIX_LEFT}.rms_mv"]
    scaled = features_of({LEFT: alternating([v * factor for v in values])}, [])[f"{PREFIX_LEFT}.rms_mv"]

    assert scaled == pytest.approx(base * factor, rel=1e-9)


@pytest.mark.property
@settings(deadline=None, max_examples=40)
@given(left=signals, right=signals, left_mvc=mvcs, right_mvc=mvcs)
@example(left=[0.01] * 8, right=[50.0] * 8, left_mvc=50.0, right_mvc=0.1)
@example(left=[1.0] * 8, right=[1.0] * 8, left_mvc=1.0, right_mvc=1.0)
def test_the_symmetry_index_is_between_zero_and_two_hundred_and_symmetric_in_the_sides(
    left: list[float], right: list[float], left_mvc: float, right_mvc: float
):
    straight = features_of({LEFT: left, RIGHT: right}, [calibration(LEFT, "left", left_mvc), calibration(RIGHT, "right", right_mvc)])[SYMMETRY]
    # lados intercambiados: lo que era izquierdo pasa a ser derecho con su propia MVC
    swapped = features_of({LEFT: right, RIGHT: left}, [calibration(LEFT, "left", right_mvc), calibration(RIGHT, "right", left_mvc)])[SYMMETRY]

    assert 0.0 <= straight <= 200.0
    assert swapped == pytest.approx(straight, rel=1e-9, abs=1e-9)


@pytest.mark.property
@settings(deadline=None, max_examples=40)
@given(values=signals, mvc=mvcs, factor=scales)
@example(values=[0.01] * 8, mvc=0.1, factor=10.0)
@example(values=[50.0] * 8, mvc=50.0, factor=0.1)
def test_pct_mvc_is_inversely_linear_in_the_mvc(values: list[float], mvc: float, factor: float):
    base = features_of({LEFT: values}, [calibration(LEFT, "left", mvc)])
    scaled = features_of({LEFT: values}, [calibration(LEFT, "left", mvc * factor)])

    for name in ("rms_pct_mvc", "peak_pct_mvc"):
        assert scaled[f"{PREFIX_LEFT}.{name}"] == pytest.approx(base[f"{PREFIX_LEFT}.{name}"] / factor, rel=1e-9)
