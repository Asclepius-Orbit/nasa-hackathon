"""Familia ``imu`` (#44): rango de movimiento, velocidad angular pico y suavidad (SPARC) por plano, y RMS del acelerómetro.

Las señales son sintéticas y con valores exactos: el giróscopo trae el sesgo de la calibración del ejemplo
(``x 0.31, y -0.12, z 0.05`` deg/s) más la velocidad angular del plano, y el montaje del ejemplo es
``sensor_x anterior, sensor_y left, sensor_z cranial`` (sagital = ``gyro_y``, frontal = ``gyro_x``, transversal = ``gyro_z``).
"""

import json
import math
import warnings
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from spine_contracts import AnatomicalPlane, FeaturesRequest
from spine_contracts.catalog import expand_template
from spine_contracts.models.domain.calibration_schema import BodyAxis, Mounting
from spine_contracts.models.domain.feature_set_schema import Reason
from spine_engine.features.context import ExtractionContext, build_context
from spine_engine.features.exercise import ExerciseFamily
from spine_engine.features.imu import (
    SPARC_AMPLITUDE_THRESHOLD,
    SPARC_CUTOFF_HZ,
    SPARC_MIN_SAMPLE_RATE_HZ,
    SPARC_PAD_LEVEL,
    ImuFamily,
    accel_rms_g,
    peak_angular_velocity_dps,
    plane_channel,
    range_of_motion_deg,
    sparc,
)
from spine_engine.features.registry import FamilyResult, default_registry
from spine_engine.features.repetitions import RepetitionService, RepWindow, RepWindows

_EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "features-request.json"
T0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)
BOOT = 2893746120
RATE = 100.0
BIAS = {"gyro_x": 0.31, "gyro_y": -0.12, "gyro_z": 0.05}
PLANES = ("sagittal", "frontal", "transverse")
TEMPLATES = (
    "imu.{spine_level}.{anatomical_plane}.rom_deg",
    "imu.{spine_level}.{anatomical_plane}.peak_angular_velocity_dps",
    "imu.{spine_level}.{anatomical_plane}.smoothness_sparc",
    "imu.{spine_level}.accel_rms_g",
)


def ids_for(level: str) -> list[str]:
    """Ids de un nivel en el orden de salida: por plano rom, pico y SPARC; al final el RMS."""
    per_plane = [f"imu.{level}.{plane}.{name}" for plane in PLANES for name in ("rom_deg", "peak_angular_velocity_dps", "smoothness_sparc")]
    return [*per_plane, f"imu.{level}.accel_rms_g"]


def sparc_ids(level: str = "l3") -> list[str]:
    return [f"imu.{level}.{plane}.smoothness_sparc" for plane in PLANES]


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def rep(index: int, start: float, end: float) -> RepWindow:
    return RepWindow(index, at(start), at(end), None, None)


class FakeService(RepetitionService):
    """Frontera: devuelve unas ventanas fijas, sin mirar el contexto."""

    def __init__(self, *windows: RepWindow) -> None:
        super().__init__()
        self._result = RepWindows(tuple(windows), "events" if windows else "none")

    def windows(self, context: ExtractionContext) -> RepWindows:
        return self._result


def chunks_for(
    channels: Mapping[str, Sequence[float]],
    *,
    rate: float = RATE,
    chunk: int = 10,
    boot_id: int = BOOT,
    seq0: int = 0,
    t0_device_ms: int = 0,
) -> list[dict[str, Any]]:
    """Chunks IMU con los canales dados; los que faltan valen el sesgo (giro) o 0 (acelerómetro)."""
    n = len(next(iter(channels.values())))
    full = {name: list(channels.get(name, [BIAS.get(name, 0.0)] * n)) for name in ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z")}
    out: list[dict[str, Any]] = []
    for k, i0 in enumerate(range(0, n, chunk)):
        i1 = min(i0 + chunk, n)
        t0 = t0_device_ms + round(i0 * 1000 / rate)
        out.append(
            {
                "schema_version": "1.0.0",
                "astronaut_id": "AST-01",
                "device_id": "wb-01",
                "stream_id": "imu_l3",
                "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01",
                "source": "device",
                "boot_id": boot_id,
                "seq": seq0 + k,
                "t0_device_ms": t0,
                "received_at": (at(t0 / 1000) + timedelta(milliseconds=50)).isoformat(),
                "sample_rate_hz": rate,
                "n_samples": i1 - i0,
                "stream_kind": "imu",
                "units": {"accel_x": "g", "accel_y": "g", "accel_z": "g", "gyro_x": "deg/s", "gyro_y": "deg/s", "gyro_z": "deg/s"},
                "data": {name: values[i0:i1] for name, values in full.items()},
                "quality": {"accel": {"score": 1.0, "flags": [], "assessed_by": "gateway"}, "gyro": {"score": 1.0, "flags": [], "assessed_by": "gateway"}},
            }
        )
    return out


def sync(boot_id: int = BOOT, utc: datetime = T0) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "boot_id": boot_id,
        "device_ms": 0,
        "utc": utc.isoformat(),
        "uncertainty_ms": 5,
        "method": "round_trip",
    }


def make_context(
    chunks: list[dict[str, Any]],
    *,
    syncs: list[dict[str, Any]] | None = None,
    edit: Callable[[dict[str, Any]], None] | None = None,
) -> ExtractionContext:
    raw = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    # se conservan los chunks EMG y stretch del ejemplo: la petición exige al menos uno
    raw["chunks"] = [c for c in raw["chunks"] if c["stream_kind"] != "imu"] + chunks
    raw["clock_syncs"] = [sync()] if syncs is None else syncs
    raw["session"]["events"] = []
    if edit is not None:
        edit(raw)
    return build_context(FeaturesRequest.model_validate(raw))


def imu_calibration(raw: dict[str, Any]) -> dict[str, Any]:
    return next(c for c in raw["calibrations"] if c["calibration_type"] == "imu")


def bend(rate: float, seconds: float, speed: float) -> list[float]:
    """Ida y vuelta a velocidad constante: ``+speed`` la primera mitad y ``-speed`` la segunda."""
    n = round(rate * seconds)
    return [speed if i < n // 2 else -speed for i in range(n)]


def with_bias(axis: str, values: Sequence[float]) -> list[float]:
    return [BIAS[axis] + v for v in values]


def run(windows: Sequence[RepWindow], context: ExtractionContext) -> FamilyResult:
    return ImuFamily(FakeService(*windows)).extract(context)


def values(result: FamilyResult) -> dict[str, float]:
    return {f.feature_id: f.value for f in result.computed}


def skipped(result: FamilyResult) -> dict[str, Reason]:
    return {s.feature_id: s.reason for s in result.skipped}


# --- funciones puras -----------------------------------------------------------------------------


def mounting(x: str, y: str, z: str) -> Mounting:
    return Mounting(sensor_x=BodyAxis(x), sensor_y=BodyAxis(y), sensor_z=BodyAxis(z))


@pytest.mark.parametrize(
    ("mount", "plane", "expected"),
    [
        (mounting("anterior", "left", "cranial"), "sagittal", ("gyro_y", 1.0)),
        (mounting("anterior", "left", "cranial"), "frontal", ("gyro_x", 1.0)),
        (mounting("anterior", "left", "cranial"), "transverse", ("gyro_z", 1.0)),
        # montaje del simulador
        (mounting("cranial", "left", "posterior"), "sagittal", ("gyro_y", 1.0)),
        (mounting("cranial", "left", "posterior"), "frontal", ("gyro_z", -1.0)),
        (mounting("cranial", "left", "posterior"), "transverse", ("gyro_x", 1.0)),
        (mounting("right", "caudal", "anterior"), "sagittal", ("gyro_x", -1.0)),
        (mounting("right", "caudal", "anterior"), "transverse", ("gyro_y", -1.0)),
        (mounting("right", "caudal", "anterior"), "frontal", ("gyro_z", 1.0)),
    ],
)
def test_the_plane_channel_follows_the_mounting(mount: Mounting, plane: str, expected: tuple[str, float]):
    assert plane_channel(mount, AnatomicalPlane(plane)) == expected


def test_a_mounting_without_the_plane_axis_has_no_channel():
    assert plane_channel(mounting("anterior", "anterior", "cranial"), AnatomicalPlane.SAGITTAL) is None


def test_the_range_of_motion_is_the_span_of_the_integrated_angle():
    # ángulo 0, 1, 2, 1, 0 -> rango 2
    assert range_of_motion_deg(np.array([10.0, 10.0, -10.0, -10.0]), 10.0) == pytest.approx(2.0)


def test_the_range_of_motion_starts_from_zero_before_the_first_sample():
    # ángulo 0, 3: un único sentido sigue contando desde el inicio
    assert range_of_motion_deg(np.array([30.0]), 10.0) == pytest.approx(3.0)
    assert range_of_motion_deg(np.array([-30.0]), 10.0) == pytest.approx(3.0)


def test_the_range_of_motion_uses_the_sample_period():
    assert range_of_motion_deg(np.array([10.0, 10.0]), 4.0) == pytest.approx(5.0)


def test_the_peak_angular_velocity_is_the_largest_absolute_value():
    assert peak_angular_velocity_dps(np.array([1.0, -5.0, 3.0])) == 5.0


def reference_sparc(speed: np.ndarray, fs: float) -> float:
    """Implementación de referencia publicada (Balasubramanian et al. 2015), con FFT completa."""
    nfft = int(2 ** (np.ceil(np.log2(len(speed))) + SPARC_PAD_LEVEL))
    f = np.arange(0, fs, fs / nfft)
    mf = np.abs(np.fft.fft(speed, nfft))
    mf = mf / mf.max()
    keep = f <= SPARC_CUTOFF_HZ
    f_sel, mf_sel = f[keep], mf[keep]
    inx = np.nonzero(mf_sel >= SPARC_AMPLITUDE_THRESHOLD)[0]
    f_sel, mf_sel = f_sel[inx[0] : inx[-1] + 1], mf_sel[inx[0] : inx[-1] + 1]
    return -float(np.sum(np.sqrt((np.diff(f_sel) / (f_sel[-1] - f_sel[0])) ** 2 + np.diff(mf_sel) ** 2)))


def min_jerk_speed(n: int) -> np.ndarray:
    tau = np.linspace(0.0, 1.0, n)
    return 30.0 * tau**2 - 60.0 * tau**3 + 30.0 * tau**4


def test_the_sparc_parameters_are_the_published_defaults():
    assert (SPARC_PAD_LEVEL, SPARC_CUTOFF_HZ, SPARC_AMPLITUDE_THRESHOLD) == (4, 10.0, 0.05)
    assert SPARC_MIN_SAMPLE_RATE_HZ == 50.0


@pytest.mark.parametrize(("n", "fs"), [(200, 100.0), (101, 50.0), (37, 60.0), (400, 1000.0)])
def test_sparc_matches_the_reference_implementation(n: int, fs: float):
    speed = min_jerk_speed(n)

    assert sparc(speed, fs) == pytest.approx(reference_sparc(speed, fs), rel=1e-12)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_sparc_of_a_broadband_profile_matches_the_reference_up_to_the_cutoff(seed: int):
    # espectro sobre el umbral más allá de 10 Hz; a 128 Hz con 128 muestras hay un bin justo en 10 Hz (se incluye)
    speed = np.abs(np.random.default_rng(seed).normal(0.0, 1.0, 128))

    assert sparc(speed, 128.0) == pytest.approx(reference_sparc(speed, 128.0), rel=1e-12)


def test_a_minimum_jerk_profile_is_smoother_than_the_same_profile_with_a_tremor():
    speed = min_jerk_speed(200)
    shaky = speed + 0.3 * np.abs(np.sin(np.linspace(0.0, 2 * np.pi * 6, 200)))

    assert sparc(speed, 100.0) > sparc(shaky, 100.0)


def test_sparc_of_a_single_impulse_is_minus_one():
    # espectro plano: arco recto de longitud 1
    assert sparc(np.array([2.0]), 100.0) == pytest.approx(-1.0)


def test_sparc_of_a_still_profile_is_undefined():
    assert math.isnan(sparc(np.zeros(10), 100.0))


def test_accel_rms_removes_the_static_component_of_each_axis():
    ax = np.array([1.0, -1.0, 1.0, -1.0]) + 0.5
    still = np.full(4, 0.98)

    assert accel_rms_g(ax, still, still) == pytest.approx(1.0)


def test_accel_rms_combines_the_three_axes_as_a_magnitude():
    swing = np.array([0.0, 2.0])

    assert accel_rms_g(swing, swing, swing) == pytest.approx(math.sqrt(3.0))


def test_accel_rms_of_a_constant_acceleration_is_zero():
    assert accel_rms_g(np.full(5, 1.0), np.full(5, -0.3), np.full(5, 0.2)) == 0.0


# --- familia -------------------------------------------------------------------------------------


def test_the_family_declares_every_concrete_id_of_its_four_catalog_templates():
    family = ImuFamily(RepetitionService())

    assert family.family == "imu"
    assert list(family.feature_ids) == [i for t in TEMPLATES for i in expand_template(t)]


def test_the_default_registry_registers_the_imu_family_after_exercise_sharing_the_repetitions():
    families = list(default_registry())

    assert [f.family for f in families] == ["exercise", "emg", "imu", "stretch"]
    exercise, _, imu, _ = families
    assert isinstance(exercise, ExerciseFamily)
    assert isinstance(imu, ImuFamily)
    assert imu.repetitions is exercise._repetitions  # pyright: ignore[reportPrivateUsage] # el servicio compartido es interno de exercise


def sagittal_context(
    speed: float = 20.0, *, rate: float = RATE, seconds: float = 6.0, edit: Callable[[dict[str, Any]], None] | None = None
) -> ExtractionContext:
    """Quieto 2 s, una flexión de 2 s a ``speed`` en el plano sagital y quieto hasta ``seconds``."""
    lead = round(2.0 * rate)
    moving = bend(rate, 2.0, speed)
    tail = round(seconds * rate) - lead - len(moving)
    gyro_y = with_bias("gyro_y", [0.0] * lead + moving + [0.0] * tail)
    return make_context(chunks_for({"gyro_y": gyro_y}, rate=rate), edit=edit)


def test_one_sagittal_bend_gives_its_rom_and_peak_and_still_planes_give_zero():
    result = run([rep(1, 2.0, 4.0)], sagittal_context())

    got = values(result)
    assert got["imu.l3.sagittal.rom_deg"] == pytest.approx(20.0)
    assert got["imu.l3.sagittal.peak_angular_velocity_dps"] == pytest.approx(20.0)
    for plane in ("frontal", "transverse"):
        assert got[f"imu.l3.{plane}.rom_deg"] == pytest.approx(0.0, abs=1e-12)
        assert got[f"imu.l3.{plane}.peak_angular_velocity_dps"] == pytest.approx(0.0, abs=1e-12)
    assert got["imu.l3.accel_rms_g"] == 0.0
    assert result.skipped == ()


def test_the_output_order_streams_and_n_are_fixed():
    result = run([rep(1, 2.0, 4.0)], sagittal_context())

    assert [f.feature_id for f in result.computed] == ids_for("l3")
    assert {f.streams for f in result.computed} == {("imu_l3",)}
    assert {f.n for f in result.computed} == {1}
    assert all(f.quality is None for f in result.computed)


def test_sparc_is_computed_per_plane_from_the_angular_speed_of_the_repetition():
    result = run([rep(1, 2.0, 4.0)], sagittal_context())

    got = values(result)
    expected = sparc(np.abs(np.array(bend(RATE, 2.0, 20.0))), RATE)
    assert got["imu.l3.sagittal.smoothness_sparc"] == pytest.approx(expected)
    # sin movimiento la suavidad no está definida: el compuesto lo pasa a no_data
    assert math.isnan(got["imu.l3.frontal.smoothness_sparc"])


def test_a_plane_without_any_defined_sparc_is_nan_without_numpy_warnings():
    # sin ninguna repetición con SPARC finito no se promedia una lista vacía (numpy avisaría): NaN directo
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = run([rep(1, 2.0, 4.0)], sagittal_context())

    assert math.isnan(values(result)["imu.l3.transverse.smoothness_sparc"])


def test_the_window_is_half_open_start_sample_in_end_sample_out():
    # un pico justo en el fin de la ventana no cuenta; el del inicio sí
    gyro_y = with_bias("gyro_y", [0.0] * 600)
    gyro_y[200] += 7.0
    gyro_y[400] += 50.0
    result = run([rep(1, 2.0, 4.0)], make_context(chunks_for({"gyro_y": gyro_y})))

    assert values(result)["imu.l3.sagittal.peak_angular_velocity_dps"] == pytest.approx(7.0)
    assert values(result)["imu.l3.sagittal.rom_deg"] == pytest.approx(0.07)


def test_values_are_the_mean_over_repetitions_and_n_counts_them():
    gyro_y = with_bias("gyro_y", [0.0] * 200 + bend(RATE, 2.0, 20.0) + [0.0] * 200 + bend(RATE, 2.0, 10.0) + [0.0] * 200)
    result = run([rep(1, 2.0, 4.0), rep(2, 6.0, 8.0)], make_context(chunks_for({"gyro_y": gyro_y})))

    got = values(result)
    assert got["imu.l3.sagittal.rom_deg"] == pytest.approx(15.0)
    assert got["imu.l3.sagittal.peak_angular_velocity_dps"] == pytest.approx(15.0)
    assert {f.n for f in result.computed} == {2}


def test_sparc_averages_only_the_repetitions_where_it_is_defined():
    # la 2.ª repetición no se mueve: su SPARC no está definido y no anula el de las otras dos
    gyro_y = with_bias("gyro_y", [0.0] * 200 + bend(RATE, 2.0, 20.0) + [0.0] * 600 + bend(RATE, 2.0, 10.0) + [0.0] * 200)
    reps = [rep(1, 2.0, 4.0), rep(2, 6.0, 8.0), rep(3, 10.0, 12.0)]
    result = run(reps, make_context(chunks_for({"gyro_y": gyro_y})))

    by_id = {f.feature_id: f for f in result.computed}
    expected = (sparc(np.abs(np.array(bend(RATE, 2.0, 20.0))), RATE) + sparc(np.abs(np.array(bend(RATE, 2.0, 10.0))), RATE)) / 2
    assert (by_id["imu.l3.sagittal.smoothness_sparc"].value, by_id["imu.l3.sagittal.smoothness_sparc"].n) == (pytest.approx(expected), 2)
    assert by_id["imu.l3.sagittal.rom_deg"].n == 3


def test_a_repetition_without_samples_does_not_count():
    result = run([rep(1, 2.0, 4.0), rep(2, 100.0, 102.0)], sagittal_context())

    assert values(result)["imu.l3.sagittal.rom_deg"] == pytest.approx(20.0)
    assert {f.n for f in result.computed} == {1}


def test_repetitions_without_any_imu_sample_give_no_data():
    result = run([rep(1, 100.0, 102.0)], sagittal_context())

    assert result.computed == ()
    assert skipped(result) == dict.fromkeys(ids_for("l3"), Reason.NO_DATA)


def test_no_repetitions_skip_everything_as_too_few_repetitions():
    result = run([], sagittal_context())

    assert result.computed == ()
    assert [(s.feature_id, s.reason) for s in result.skipped] == [(i, Reason.TOO_FEW_REPETITIONS) for i in ids_for("l3")]


def test_the_gyro_bias_of_the_calibration_is_subtracted():
    def no_bias(raw: dict[str, Any]) -> None:
        imu_calibration(raw)["gyro_bias_dps"] = {"x": 0.0, "y": 0.0, "z": 0.0}

    # con sesgo 0 el sesgo real del giróscopo queda en la señal y se suma al pico de cada plano
    result = run([rep(1, 2.0, 4.0)], sagittal_context(edit=no_bias))

    assert values(result)["imu.l3.sagittal.peak_angular_velocity_dps"] == pytest.approx(20.12)
    assert values(result)["imu.l3.frontal.peak_angular_velocity_dps"] == pytest.approx(0.31)
    assert values(result)["imu.l3.transverse.peak_angular_velocity_dps"] == pytest.approx(0.05)


def test_the_mounting_of_the_calibration_picks_the_axis_and_its_sign():
    def swap(raw: dict[str, Any]) -> None:
        imu_calibration(raw)["mounting"] = {"sensor_x": "right", "sensor_y": "anterior", "sensor_z": "cranial"}

    # sagital = -gyro_x, frontal = gyro_y
    gyro_x = with_bias("gyro_x", [0.0] * 200 + bend(RATE, 2.0, 20.0) + [0.0] * 200)
    result = run([rep(1, 2.0, 4.0)], make_context(chunks_for({"gyro_x": gyro_x}), edit=swap))

    got = values(result)
    assert got["imu.l3.sagittal.rom_deg"] == pytest.approx(20.0)
    assert got["imu.l3.frontal.rom_deg"] == pytest.approx(0.0, abs=1e-12)


def test_a_mounting_without_two_planes_skips_both_and_a_slow_stream_keeps_the_sparc_reason():
    def broken(raw: dict[str, Any]) -> None:
        imu_calibration(raw)["mounting"] = {"sensor_x": "anterior", "sensor_y": "anterior", "sensor_z": "anterior"}

    result = run([rep(1, 2.0, 4.0)], sagittal_context(rate=49.0, edit=broken))

    expected = {f"imu.l3.{plane}.{name}": Reason.NO_CALIBRATION for plane in ("sagittal", "transverse") for name in ("rom_deg", "peak_angular_velocity_dps")}
    expected |= dict.fromkeys(sparc_ids(), Reason.INSUFFICIENT_SAMPLE_RATE)
    assert skipped(result) == expected
    assert len(result.skipped) == len(expected)
    assert list(values(result)) == ["imu.l3.frontal.rom_deg", "imu.l3.frontal.peak_angular_velocity_dps", "imu.l3.accel_rms_g"]


def test_a_repetition_with_a_single_sample_counts():
    result = run([rep(1, 2.0, 2.005)], sagittal_context())

    assert {f.n for f in result.computed} == {1}
    assert values(result)["imu.l3.sagittal.rom_deg"] == pytest.approx(0.2)


def test_a_non_imu_stream_with_a_spine_level_is_ignored():
    def emg_with_level(raw: dict[str, Any]) -> None:
        emg = next(s for s in raw["session"]["streams"] if s["stream_kind"] == "emg")
        emg["placement"] = {**emg["placement"], "spine_level": "t5"}

    result = run([rep(1, 2.0, 4.0)], sagittal_context(edit=emg_with_level))

    assert [f.feature_id for f in result.computed] == ids_for("l3")
    assert result.skipped == ()


def test_the_skips_of_every_stream_are_kept():
    def expired_and_second(raw: dict[str, Any]) -> None:
        imu_calibration(raw)["valid_until"] = "2026-11-13T09:00:00Z"
        decl = next(s for s in raw["session"]["streams"] if s["stream_id"] == "imu_l3")
        raw["session"]["streams"].append({**decl, "stream_id": "imu_t12", "placement": {"spine_level": "t12"}})

    result = run([rep(1, 2.0, 4.0)], sagittal_context(edit=expired_and_second))

    expected = [(i, Reason.CALIBRATION_EXPIRED) for i in ids_for("l3")] + [(i, Reason.STREAM_MISSING) for i in ids_for("t12")]
    assert [(s.feature_id, s.reason) for s in result.skipped] == expected


def test_a_mounting_without_a_plane_skips_that_plane_as_no_calibration():
    def broken(raw: dict[str, Any]) -> None:
        imu_calibration(raw)["mounting"] = {"sensor_x": "anterior", "sensor_y": "anterior", "sensor_z": "cranial"}

    result = run([rep(1, 2.0, 4.0)], sagittal_context(edit=broken))

    sagittal = [i for i in ids_for("l3") if ".sagittal." in i]
    assert skipped(result) == dict.fromkeys(sagittal, Reason.NO_CALIBRATION)
    assert set(values(result)) == set(ids_for("l3")) - set(sagittal)


def test_the_spine_level_comes_from_the_session_declaration():
    def t12(raw: dict[str, Any]) -> None:
        next(s for s in raw["session"]["streams"] if s["stream_id"] == "imu_l3")["placement"] = {"spine_level": "t12"}

    result = run([rep(1, 2.0, 4.0)], sagittal_context(edit=t12))

    assert [f.feature_id for f in result.computed] == ids_for("t12")


def test_without_calibration_everything_is_skipped_as_no_calibration():
    def drop(raw: dict[str, Any]) -> None:
        raw["calibrations"] = [c for c in raw["calibrations"] if c["calibration_type"] != "imu"]

    result = run([rep(1, 2.0, 4.0)], sagittal_context(edit=drop))

    assert result.computed == ()
    assert skipped(result) == dict.fromkeys(ids_for("l3"), Reason.NO_CALIBRATION)


def test_an_expired_calibration_skips_everything_as_calibration_expired():
    def expire(raw: dict[str, Any]) -> None:
        imu_calibration(raw)["valid_until"] = "2026-11-13T09:00:00Z"

    result = run([rep(1, 2.0, 4.0)], sagittal_context(edit=expire))

    assert result.computed == ()
    assert skipped(result) == dict.fromkeys(ids_for("l3"), Reason.CALIBRATION_EXPIRED)


def test_a_declared_imu_stream_without_chunks_is_stream_missing():
    result = run([rep(1, 2.0, 4.0)], make_context([]))

    assert result.computed == ()
    assert skipped(result) == dict.fromkeys(ids_for("l3"), Reason.STREAM_MISSING)


def test_chunks_without_a_clock_anchor_give_no_data():
    result = run([rep(1, 2.0, 4.0)], make_context(chunks_for({"gyro_y": [0.0] * 600}), syncs=[sync(boot_id=1)]))

    assert result.computed == ()
    assert skipped(result) == dict.fromkeys(ids_for("l3"), Reason.NO_DATA)


@pytest.mark.parametrize(("rate", "computes_sparc"), [(49.0, False), (50.0, True), (100.0, True)])
def test_sparc_needs_at_least_fifty_hertz(rate: float, computes_sparc: bool):
    result = run([rep(1, 2.0, 4.0)], sagittal_context(rate=rate))

    others = [i for i in ids_for("l3") if i not in sparc_ids()]
    assert set(others) <= set(values(result))
    if computes_sparc:
        assert set(sparc_ids()) <= set(values(result))
        assert result.skipped == ()
    else:
        assert skipped(result) == dict.fromkeys(sparc_ids(), Reason.INSUFFICIENT_SAMPLE_RATE)


def test_the_slowest_chunk_of_the_stream_decides_the_sample_rate():
    fast = chunks_for({"gyro_y": with_bias("gyro_y", [0.0] * 600)})
    slow = chunks_for({"gyro_y": with_bias("gyro_y", [0.0] * 10)}, rate=10.0, seq0=60, t0_device_ms=6000)
    result = run([rep(1, 2.0, 4.0)], make_context(fast + slow))

    assert skipped(result) == dict.fromkeys(sparc_ids(), Reason.INSUFFICIENT_SAMPLE_RATE)


def test_the_range_of_motion_integrates_each_sample_with_the_period_of_its_own_chunk():
    # mismo arranque con un chunk lento al final: la flexión a 100 Hz se integra a 10 ms, no al periodo del chunk lento
    fast = chunks_for({"gyro_y": with_bias("gyro_y", [0.0] * 200 + bend(RATE, 2.0, 20.0) + [0.0] * 200)})
    slow = chunks_for({"gyro_y": with_bias("gyro_y", [0.0] * 10)}, rate=10.0, seq0=60, t0_device_ms=6000)
    result = run([rep(1, 2.0, 4.0)], make_context(fast + slow))

    assert values(result)["imu.l3.sagittal.rom_deg"] == pytest.approx(20.0)


def test_a_slow_stream_without_repetitions_still_says_why_sparc_is_skipped():
    result = run([], sagittal_context(rate=10.0))

    expected = dict.fromkeys(ids_for("l3"), Reason.TOO_FEW_REPETITIONS) | dict.fromkeys(sparc_ids(), Reason.INSUFFICIENT_SAMPLE_RATE)
    assert skipped(result) == expected


def test_each_repetition_reads_the_segment_with_most_samples_in_its_window():
    # reinicio a mitad de la rep: el primer arranque aporta 50 muestras quietas y el segundo 150 en movimiento
    first = chunks_for({"gyro_y": with_bias("gyro_y", [0.0] * 250)}, chunk=50)
    moving = with_bias("gyro_y", [10.0] * 150 + [0.0] * 50)
    second = chunks_for({"gyro_y": moving}, boot_id=7, t0_device_ms=0, chunk=50)
    syncs = [sync(), sync(boot_id=7, utc=at(2.5))]
    result = run([rep(1, 2.0, 4.0)], make_context(first + second, syncs=syncs))

    assert values(result)["imu.l3.sagittal.rom_deg"] == pytest.approx(15.0)


def test_every_declared_imu_stream_gets_its_own_level():
    def second_stream(raw: dict[str, Any]) -> None:
        decl = next(s for s in raw["session"]["streams"] if s["stream_id"] == "imu_l3")
        raw["session"]["streams"].append({**decl, "stream_id": "imu_t12", "placement": {"spine_level": "t12"}})

    result = run([rep(1, 2.0, 4.0)], sagittal_context(edit=second_stream))

    assert [f.feature_id for f in result.computed] == ids_for("l3")
    assert skipped(result) == dict.fromkeys(ids_for("t12"), Reason.STREAM_MISSING)


def test_the_accelerometer_rms_is_the_mean_over_repetitions():
    accel_x = [0.0] * 200 + [0.5, -0.5] * 100 + [0.0] * 200 + [0.25, -0.25] * 100 + [0.0] * 200
    gyro_y = with_bias("gyro_y", [0.0] * 1000)
    result = run([rep(1, 2.0, 4.0), rep(2, 6.0, 8.0)], make_context(chunks_for({"accel_x": accel_x, "gyro_y": gyro_y})))

    assert values(result)["imu.l3.accel_rms_g"] == pytest.approx(0.375)


# --- propiedades ---------------------------------------------------------------------------------

speeds = st.lists(st.floats(min_value=-500.0, max_value=500.0, allow_nan=False, allow_infinity=False), min_size=1, max_size=200)


@pytest.mark.property
@given(omega=speeds, rate=st.floats(min_value=1.0, max_value=1000.0))
@example(omega=[0.0], rate=1.0)
@example(omega=[-500.0, 500.0], rate=1000.0)
def test_rom_is_non_negative_and_does_not_depend_on_the_sense_of_rotation(omega: list[float], rate: float):
    signal = np.array(omega)

    rom = range_of_motion_deg(signal, rate)

    assert rom >= 0.0
    assert range_of_motion_deg(-signal, rate) == pytest.approx(rom)


@pytest.mark.property
@given(
    speed=speeds,
    nonzero=st.floats(min_value=0.5, max_value=500.0),
    where=st.integers(min_value=0, max_value=200),
    rate=st.floats(min_value=20.0, max_value=1000.0),
)
@example(speed=[0.0], nonzero=0.5, where=0, rate=20.0)
@example(speed=[500.0] * 3, nonzero=500.0, where=3, rate=1000.0)
def test_sparc_is_never_positive(speed: list[float], nonzero: float, where: int, rate: float):
    profile = np.abs(np.array(speed))
    profile = np.insert(profile, min(where, len(profile)), nonzero)  # al menos una muestra con velocidad

    assert sparc(profile, rate) <= 0.0


axis_values = st.lists(st.floats(min_value=-16.0, max_value=16.0, allow_nan=False, allow_infinity=False), min_size=3, max_size=3)


@pytest.mark.property
@given(samples=st.lists(axis_values, min_size=1, max_size=100))
@example(samples=[[0.0, 0.0, 0.0]])
@example(samples=[[16.0, -16.0, 16.0], [-16.0, 16.0, -16.0]])
def test_accel_rms_is_non_negative(samples: list[list[float]]):
    xyz = np.array(samples)

    assert accel_rms_g(xyz[:, 0], xyz[:, 1], xyz[:, 2]) >= 0.0


@pytest.mark.property
@settings(max_examples=20, deadline=None)
@given(shift_s=st.integers(min_value=-86_400, max_value=86_400), speed=st.floats(min_value=-200.0, max_value=200.0, allow_nan=False))
@example(shift_s=0, speed=0.0)
def test_shifting_the_session_in_time_does_not_change_the_values(shift_s: int, speed: float):
    gyro_y = with_bias("gyro_y", [0.0] * 200 + bend(RATE, 2.0, speed) + [0.0] * 200)
    base = values(run([rep(1, 2.0, 4.0)], make_context(chunks_for({"gyro_y": gyro_y}))))
    offset = timedelta(seconds=shift_s)
    shifted_rep = RepWindow(1, at(2.0) + offset, at(4.0) + offset, None, None)

    moved = values(run([shifted_rep], make_context(chunks_for({"gyro_y": gyro_y}), syncs=[sync(utc=T0 + offset)])))

    assert moved.keys() == base.keys()
    for feature_id, value in base.items():
        if math.isnan(value):
            assert math.isnan(moved[feature_id])
        else:
            assert moved[feature_id] == pytest.approx(value)
