"""Grabación de calibraciones stretch y emg_mvc a partir de los lotes del Arduino."""

import math
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import count

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import EmgMvcCalibration, Muscle, Side, StretchCalibration, dump, validation
from spine_contracts.models.telemetry.command_schema import CalibrationType
from spine_gateway.calibration import (
    DEFAULT_STRETCH_CURVE_FACTORS,
    CalibrationFailure,
    CalibrationRecorder,
    CalibrationStatus,
    FailureReason,
)
from spine_gateway.protocol import Frame, FrameKind
from spine_gateway.settings import GatewaySettings

STRETCH = CalibrationType.STRETCH
EMG_MVC = CalibrationType.EMG_MVC
SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a02"
NOW = datetime(2026, 11, 13, 7, 5, tzinfo=UTC)


def ids() -> Iterator[str]:
    return (f"7a1c0d52-8b3e-4f6a-a1d4-{n:012d}" for n in count(1))


def recorder(settings: GatewaySettings, kind: CalibrationType, duration_s: float, session_id: str | None = None) -> CalibrationRecorder:
    new_id = ids()
    return CalibrationRecorder(kind, "stream_x", duration_s, settings, lambda: next(new_id), session_id=session_id)


def cord(seq: int, samples: tuple[int, int] = (341, 341), t0: int | None = None) -> Frame:
    return Frame(FrameKind.CORD, seq, 100 * seq if t0 is None else t0, samples)


def emg(seq: int, samples: tuple[int, ...]) -> Frame:
    return Frame(FrameKind.EMG, seq, 100 * seq, samples)


def calibration_payload(result: object) -> dict[str, object]:
    assert isinstance(result, StretchCalibration | EmgMvcCalibration), result
    data = dump(result)
    assert validation.errors("domain/calibration", data) == []
    return data


# --- construcción ---


def test_default_curve_factors() -> None:
    assert DEFAULT_STRETCH_CURVE_FACTORS == (1.0, 1.25, 1.5, 1.75, 2.0)


def test_imu_is_not_recorded_here(settings: GatewaySettings) -> None:
    with pytest.raises(ValueError, match="imu calibration is not recorded by the gateway"):
        recorder(settings, CalibrationType.IMU, 1.0)


@pytest.mark.parametrize("duration", [0.0, -1.0])
def test_duration_must_be_positive(settings: GatewaySettings, duration: float) -> None:
    with pytest.raises(ValueError, match="duration_s must be > 0"):
        recorder(settings, STRETCH, duration)


# --- stretch ---


@pytest.mark.contract
def test_stretch_calibration(settings: GatewaySettings) -> None:
    rec = recorder(settings, STRETCH, 1.0, session_id=SESSION)
    assert rec.status is CalibrationStatus.COLLECTING
    statuses = [rec.feed(cord(s, (300, 382))) for s in range(10)]  # media 341 -> R0 = 5000 Ω
    assert statuses == [CalibrationStatus.COLLECTING] * 9 + [CalibrationStatus.DONE]
    assert rec.status is CalibrationStatus.DONE
    assert calibration_payload(rec.finish(NOW)) == {
        "schema_version": "1.0.0",
        "calibration_id": "7a1c0d52-8b3e-4f6a-a1d4-000000000001",
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "stream_id": "stream_x",
        "session_id": SESSION,
        "created_at": "2026-11-13T07:05:00Z",
        "source": "device",
        "calibration_type": "stretch",
        "rest_resistance_ohm": 5000.0,
        "rest_length_mm": 100.0,
        "curve": {
            "model": "piecewise_linear",
            "points": [
                {"resistance_ohm": 5000.0, "length_mm": 100.0},
                {"resistance_ohm": 6250.0, "length_mm": 125.0},
                {"resistance_ohm": 7500.0, "length_mm": 150.0},
                {"resistance_ohm": 8750.0, "length_mm": 175.0},
                {"resistance_ohm": 10000.0, "length_mm": 200.0},
            ],
        },
    }


def test_session_id_is_optional(settings: GatewaySettings) -> None:
    rec = recorder(settings, STRETCH, 0.1)
    rec.feed(cord(0))
    assert "session_id" not in calibration_payload(rec.finish(NOW))


def test_rest_length_comes_from_settings(settings: GatewaySettings) -> None:
    rec = recorder(settings.model_copy(update={"stretch_rest_length_mm": 400.0}), STRETCH, 0.1)
    rec.feed(cord(0, (682, 682)))
    data = calibration_payload(rec.finish(NOW))
    assert (data["rest_resistance_ohm"], data["rest_length_mm"]) == (20000.0, 400.0)
    assert data["curve"] == {
        "model": "piecewise_linear",
        "points": [{"resistance_ohm": 20000.0 * f, "length_mm": 400.0 * f} for f in DEFAULT_STRETCH_CURVE_FACTORS],
    }


def test_duration_is_measured_on_the_device_clock(settings: GatewaySettings) -> None:
    # 125 ms a 20 Hz: muestras en 0, 50 y 100 ms; la de 150 ms (1000 cuentas) queda fuera.
    rec = recorder(settings, STRETCH, 0.125)
    assert rec.feed(cord(0, (341, 341), t0=5000)) is CalibrationStatus.COLLECTING
    assert rec.feed(cord(1, (341, 1000), t0=5100)) is CalibrationStatus.DONE
    assert calibration_payload(rec.finish(NOW))["rest_resistance_ohm"] == 5000.0


def test_lost_frames_shorten_the_sample_but_not_the_duration(settings: GatewaySettings) -> None:
    rec = recorder(settings, STRETCH, 1.0)
    assert rec.feed(cord(0, (300, 300))) is CalibrationStatus.COLLECTING
    assert rec.feed(cord(1, (382, 382))) is CalibrationStatus.COLLECTING
    assert rec.feed(cord(9, (341, 341))) is CalibrationStatus.DONE  # t0 = 900 ms: cubre hasta 1000 ms
    assert calibration_payload(rec.finish(NOW))["rest_resistance_ohm"] == 5000.0


def test_other_kinds_duplicates_and_late_frames_are_ignored(settings: GatewaySettings) -> None:
    rec = recorder(settings, STRETCH, 0.2)
    assert rec.feed(emg(0, (0,) * 100)) is CalibrationStatus.COLLECTING
    assert rec.feed(Frame(FrameKind.IMU, 0, 0, ((0, 0, 0, 0, 0, 0),) * 10)) is CalibrationStatus.COLLECTING
    rec.feed(cord(0, (341, 341)))
    assert rec.feed(cord(0, (1, 1))) is CalibrationStatus.COLLECTING  # duplicado
    assert rec.feed(cord(1, (341, 341))) is CalibrationStatus.DONE
    assert rec.feed(cord(2, (1, 1))) is CalibrationStatus.DONE  # ya terminada
    assert calibration_payload(rec.finish(NOW))["rest_resistance_ohm"] == 5000.0


def test_frames_with_an_unexpected_sample_count_are_ignored(settings: GatewaySettings) -> None:
    rec = recorder(settings, STRETCH, 0.1)
    assert rec.feed(Frame(FrameKind.CORD, 0, 0, (1, 1, 1))) is CalibrationStatus.COLLECTING
    assert rec.feed(cord(1, t0=0)) is CalibrationStatus.DONE
    assert calibration_payload(rec.finish(NOW))["rest_resistance_ohm"] == 5000.0


def test_frames_before_the_start_are_ignored(settings: GatewaySettings) -> None:
    # frame_seq avanza pero t0 retrocede (vuelta de millis()): no puede contar como tiempo de calibración.
    rec = recorder(settings, STRETCH, 0.2)
    rec.feed(cord(0, (341, 341), t0=1000))
    assert rec.feed(cord(1, (1, 1), t0=900)) is CalibrationStatus.COLLECTING
    assert rec.feed(cord(2, (341, 341), t0=1100)) is CalibrationStatus.DONE
    assert calibration_payload(rec.finish(NOW))["rest_resistance_ohm"] == 5000.0


@pytest.mark.parametrize("value", [0, 1023, 4000])
def test_stretch_fails_when_r0_is_not_computable(settings: GatewaySettings, value: int) -> None:
    rec = recorder(settings, STRETCH, 0.1)
    rec.feed(cord(0, (value, value)))
    result = rec.finish(NOW)
    assert isinstance(result, CalibrationFailure)
    assert result.reason is FailureReason.REST_RESISTANCE_NOT_COMPUTABLE
    assert f"mean {float(value)} counts" in result.detail


def test_finish_before_the_duration_fails(settings: GatewaySettings) -> None:
    rec = recorder(settings, STRETCH, 1.0)
    assert rec.finish(NOW) == CalibrationFailure(FailureReason.INCOMPLETE, "collected 0.0 of 1.0 s")
    rec.feed(cord(0))
    rec.feed(cord(1))
    assert rec.finish(NOW) == CalibrationFailure(FailureReason.INCOMPLETE, "collected 0.2 of 1.0 s")


def test_naive_created_at_is_rejected(settings: GatewaySettings) -> None:
    rec = recorder(settings, STRETCH, 0.1)
    rec.feed(cord(0))
    with pytest.raises(ValueError, match="timezone"):
        rec.finish(datetime(2026, 11, 13, 7, 5))  # noqa: DTZ001 -- es lo que se prueba


# --- emg_mvc ---


def emg_frames(values: list[int]) -> list[Frame]:
    return [emg(i, tuple(values[100 * i : 100 * (i + 1)])) for i in range(len(values) // 100)]


@pytest.mark.contract
def test_emg_mvc_calibration(settings: GatewaySettings) -> None:
    rec = recorder(settings, EMG_MVC, 1.0, session_id=SESSION)
    values = [0] * 300 + [1023] * 250 + [0] * 450
    statuses = [rec.feed(f) for f in emg_frames(values)]
    assert statuses[-1] is CalibrationStatus.DONE
    assert statuses[:-1] == [CalibrationStatus.COLLECTING] * 9
    assert calibration_payload(rec.finish(NOW)) == {
        "schema_version": "1.0.0",
        "calibration_id": "7a1c0d52-8b3e-4f6a-a1d4-000000000001",
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "stream_id": "stream_x",
        "session_id": SESSION,
        "created_at": "2026-11-13T07:05:00Z",
        "source": "device",
        "calibration_type": "emg_mvc",
        "muscle": "latissimus_dorsi",
        "side": "left",
        "mvc_rms_mv": 5000.0,
        "window_ms": 250,
        "signal_kind": "envelope",
    }


@pytest.mark.parametrize(
    ("values", "expected_counts"),
    [
        ([512] * 1000, 512.0),
        ([0] * 500 + [1023] * 100 + [0] * 400, 1023 * math.sqrt(100 / 250)),  # ráfaga más corta que la ventana
        ([0] * 750 + [10] * 250, 10.0),  # la ventana de máximo es la última
        ([20] * 250 + [0] * 750, 20.0),  # y la primera
        ([3, 4] * 500, math.sqrt((9 + 16) / 2)),
    ],
)
def test_emg_mvc_is_the_max_sliding_rms(settings: GatewaySettings, values: list[int], expected_counts: float) -> None:
    rec = recorder(settings, EMG_MVC, 1.0)
    for f in emg_frames(values):
        rec.feed(f)
    data = calibration_payload(rec.finish(NOW))
    assert data["mvc_rms_mv"] == pytest.approx(expected_counts / 1023 * 5000, rel=1e-12)


def test_emg_mvc_uses_settings(settings: GatewaySettings) -> None:
    custom = settings.model_copy(update={"emg_mvc_window_ms": 100, "emg_gain": 2.0, "emg_muscle": Muscle.TRAPEZIUS, "emg_side": Side.RIGHT})
    rec = recorder(custom, EMG_MVC, 0.5)
    for f in emg_frames([0] * 400 + [1023] * 100):
        rec.feed(f)
    data = calibration_payload(rec.finish(NOW))
    assert (data["mvc_rms_mv"], data["window_ms"], data["muscle"], data["side"]) == (2500.0, 100, "trapezius", "right")


def test_emg_mvc_needs_a_full_window(settings: GatewaySettings) -> None:
    rec = recorder(settings.model_copy(update={"emg_mvc_window_ms": 2000}), EMG_MVC, 1.0)
    for f in emg_frames([500] * 1000):
        rec.feed(f)
    assert rec.finish(NOW) == CalibrationFailure(FailureReason.TOO_FEW_SAMPLES, "1000 samples, the 2000 ms window needs 2000")


def test_emg_mvc_with_a_flat_signal_fails(settings: GatewaySettings) -> None:
    rec = recorder(settings, EMG_MVC, 0.3)
    for f in emg_frames([0] * 300):
        rec.feed(f)
    assert rec.finish(NOW) == CalibrationFailure(FailureReason.FLAT_SIGNAL, "the maximum RMS is 0 mV")


@pytest.mark.property
@given(
    values=st.lists(st.integers(0, 1023), min_size=300, max_size=300),
    window_ms=st.integers(1, 300),
)
def test_emg_mvc_matches_a_brute_force_oracle(settings: GatewaySettings, values: list[int], window_ms: int) -> None:
    rec = recorder(settings.model_copy(update={"emg_mvc_window_ms": window_ms}), EMG_MVC, 0.3)
    for f in emg_frames(values):
        rec.feed(f)
    result = rec.finish(NOW)
    best = max(sum(v * v for v in values[i : i + window_ms]) for i in range(len(values) - window_ms + 1))
    if best == 0:
        assert result == CalibrationFailure(FailureReason.FLAT_SIGNAL, "the maximum RMS is 0 mV")
        return
    data = calibration_payload(result)
    assert data["mvc_rms_mv"] == pytest.approx(math.sqrt(best / window_ms) / 1023 * 5000, rel=1e-9)


@pytest.mark.property
@given(counts=st.integers(1, 1022), rest=st.floats(1.0, 1000.0))
def test_stretch_curve_is_valid_and_increasing(settings: GatewaySettings, counts: int, rest: float) -> None:
    rec = recorder(settings.model_copy(update={"stretch_rest_length_mm": rest}), STRETCH, 0.1)
    rec.feed(cord(0, (counts, counts)))
    data = calibration_payload(rec.finish(NOW))
    r0 = 10000.0 * counts / (1023 - counts)
    assert data["rest_resistance_ohm"] == pytest.approx(r0, rel=1e-12)
    points = data["curve"]["points"]  # pyright: ignore[reportIndexIssue]
    assert points[0] == {"resistance_ohm": data["rest_resistance_ohm"], "length_mm": rest}
    assert all(a["resistance_ohm"] < b["resistance_ohm"] and a["length_mm"] < b["length_mm"] for a, b in zip(points, points[1:], strict=False))
