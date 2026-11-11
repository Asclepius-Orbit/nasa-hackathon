"""Ensamblado de chunks MQTT v1 a partir de los lotes del Arduino: conversiones, calidad, seq/huecos y fondo."""

import math
from datetime import UTC, datetime
from typing import Any

import pytest
from spine_contracts import EmgChunk, ImuChunk, StretchCalibration, StretchChunk, dump, validation
from spine_gateway.assembler import (
    BACKGROUND_FRAMES,
    BACKGROUND_SAMPLE_RATE_HZ,
    BATCH_MS,
    PACKET_GAP_SCORE,
    SAMPLE_RATE_HZ,
    SAMPLES_PER_BATCH,
    BackgroundAccumulator,
    ChunkAssembler,
    Discarded,
    DiscardReason,
    Pending,
    accel_to_g,
    counts_to_mv,
    counts_to_resistance,
    gyro_to_dps,
    max_counts,
    resistance_to_length,
)
from spine_gateway.protocol import Frame, FrameKind, ImuSample
from spine_gateway.settings import GatewaySettings

SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"
CAL_ID = "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f11"
AT = datetime(2026, 11, 13, 10, 0, 0, 250000, tzinfo=UTC)
BOOT = 2893746120
QUIET: ImuSample = (100, -200, 8192, 655, -655, 0)


def payload(chunk: EmgChunk | ImuChunk | StretchChunk) -> dict[str, Any]:
    """Serializa con ``dump`` y comprueba el schema completo (con sus reglas if/then), no solo Pydantic."""
    data = dump(chunk)
    assert validation.errors("telemetry/stream-chunk", data) == []
    return data


def emg(seq: int, samples: tuple[int, ...] | None = None, t0: int = 1000) -> Frame:
    return Frame(FrameKind.EMG, seq, t0, samples if samples is not None else (512,) * 100)


def imu(seq: int, samples: tuple[ImuSample, ...] | None = None, t0: int = 1000) -> Frame:
    return Frame(FrameKind.IMU, seq, t0, samples if samples is not None else (QUIET,) * 10)


def cord(seq: int, samples: tuple[int, ...] = (341, 682), t0: int = 1000) -> Frame:
    return Frame(FrameKind.CORD, seq, t0, samples)


def stretch_calibration(points: list[tuple[float, float]]) -> StretchCalibration:
    return StretchCalibration.model_validate(
        {
            "schema_version": "1.0.0",
            "calibration_id": CAL_ID,
            "astronaut_id": "AST-01",
            "device_id": "wb-01",
            "stream_id": "stretch_lumbar",
            "created_at": "2026-11-13T07:05:00Z",
            "source": "device",
            "calibration_type": "stretch",
            "rest_resistance_ohm": points[0][0],
            "rest_length_mm": points[0][1],
            "curve": {"model": "piecewise_linear", "points": [{"resistance_ohm": r, "length_mm": length} for r, length in points]},
        }
    )


@pytest.fixture
def assembler(settings: GatewaySettings) -> ChunkAssembler:
    a = ChunkAssembler(settings)
    a.on_new_boot(BOOT)
    return a


def session(a: ChunkAssembler, frame: Frame, calibration: StretchCalibration | None = None) -> EmgChunk | ImuChunk | StretchChunk:
    result = a.session_chunk(frame, session_id=SESSION, received_at=AT, stretch_calibration=calibration)
    assert isinstance(result, EmgChunk | ImuChunk | StretchChunk), result
    return result


# --- constantes ---


def test_batch_constants_follow_the_firmware() -> None:
    assert BATCH_MS == 100
    assert SAMPLES_PER_BATCH == {FrameKind.EMG: 100, FrameKind.IMU: 10, FrameKind.CORD: 2}
    assert SAMPLE_RATE_HZ == {FrameKind.EMG: 1000.0, FrameKind.IMU: 100.0, FrameKind.CORD: 20.0}
    assert (BACKGROUND_FRAMES, BACKGROUND_SAMPLE_RATE_HZ) == (10, 10.0)


# --- conversiones ---


@pytest.mark.parametrize(("bits", "expected"), [(8, 255), (10, 1023), (12, 4095)])
def test_max_counts(bits: int, expected: int) -> None:
    assert max_counts(bits) == expected


@pytest.mark.parametrize(
    ("counts", "bits", "vref", "gain", "expected"),
    [
        (0, 10, 5.0, 1.0, 0.0),
        (1023, 10, 5.0, 1.0, 5000.0),
        (512, 10, 5.0, 1.0, 512 / 1023 * 5000),  # 2502.44 mV
        (1023, 10, 5.0, 2.0, 2500.0),
        (4095, 12, 3.3, 1.0, 3300.0),
        (100.5, 10, 5.0, 4.0, 100.5 / 1023 * 5000 / 4),
    ],
)
def test_counts_to_mv(counts: float, bits: int, vref: float, gain: float, expected: float) -> None:
    assert counts_to_mv(counts, adc_bits=bits, vref_v=vref, gain=gain) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize(("range_g", "raw"), [(2, 16384), (4, 8192), (8, 4096), (16, 2048)])
def test_accel_nominal_sensitivity(range_g: int, raw: int) -> None:
    assert accel_to_g(raw, range_g) == 1.0
    assert accel_to_g(-raw // 2, range_g) == -0.5


@pytest.mark.parametrize(("range_dps", "raw", "expected"), [(250, 131, 1.0), (500, 655, 10.0), (1000, 328, 10.0), (2000, 164, 10.0)])
def test_gyro_nominal_sensitivity(range_dps: int, raw: int, expected: float) -> None:
    assert gyro_to_dps(raw, range_dps) == pytest.approx(expected, rel=1e-12)
    assert gyro_to_dps(-raw, range_dps) == pytest.approx(-expected, rel=1e-12)


@pytest.mark.parametrize("range_g", [0, 1, 3, 6, 32])
def test_accel_unsupported_range_rejected(range_g: int) -> None:
    with pytest.raises(ValueError, match=f"unsupported accel range: {range_g}"):
        accel_to_g(0, range_g)


@pytest.mark.parametrize("range_dps", [0, 125, 300, 4000])
def test_gyro_unsupported_range_rejected(range_dps: int) -> None:
    with pytest.raises(ValueError, match=f"unsupported gyro range: {range_dps}"):
        gyro_to_dps(0, range_dps)


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        (341, 5000.0),  # 10 kΩ · 341 / 682
        (682, 20000.0),  # 10 kΩ · 682 / 341
        (1, 10000 / 1022),
        (1022, 10_220_000.0),
        (511.5, 10000.0),
    ],
)
def test_counts_to_resistance_divider(counts: float, expected: float) -> None:
    assert counts_to_resistance(counts, adc_bits=10, series_resistor_ohm=10000.0) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("counts", [0, -1, 1023, 1024, 65535])
def test_counts_to_resistance_not_computable_at_the_rails(counts: int) -> None:
    # 0: cordón en corto; máximo (o más, el protocolo no limita a 10 bits): circuito abierto.
    assert counts_to_resistance(counts, adc_bits=10, series_resistor_ohm=10000.0) is None


CURVE = [(5000.0, 100.0), (10000.0, 200.0), (20000.0, 250.0)]


@pytest.mark.parametrize(
    ("resistance", "expected"),
    [
        (5000.0, (100.0, True)),
        (7500.0, (150.0, True)),
        (10000.0, (200.0, True)),
        (15000.0, (225.0, True)),
        (20000.0, (250.0, True)),
        (4999.0, (100.0, False)),  # recorte al extremo inferior
        (1.0, (100.0, False)),
        (20001.0, (250.0, False)),  # recorte al extremo superior
    ],
)
def test_resistance_to_length_piecewise_linear_with_clamp(resistance: float, expected: tuple[float, bool]) -> None:
    length, inside = resistance_to_length(resistance, CURVE)
    assert (length, inside) == (pytest.approx(expected[0], rel=1e-12), expected[1])


def test_resistance_to_length_sorts_the_points() -> None:
    assert resistance_to_length(7500.0, list(reversed(CURVE))) == (pytest.approx(150.0), True)


# --- chunks de sesión ---


def test_frame_before_any_boot_is_discarded(settings: GatewaySettings) -> None:
    a = ChunkAssembler(settings)
    assert a.boot_id is None
    result = a.session_chunk(emg(0), session_id=SESSION, received_at=AT)
    assert result == Discarded(FrameKind.EMG, 0, DiscardReason.NO_BOOT)


@pytest.mark.contract
def test_emg_chunk(assembler: ChunkAssembler) -> None:
    samples = tuple(range(100))
    data = payload(session(assembler, emg(7, samples, t0=184200)))
    assert data == {
        "schema_version": "1.0.0",
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "stream_id": "emg_lat_left",
        "session_id": SESSION,
        "source": "device",
        "boot_id": BOOT,
        "seq": 0,
        "t0_device_ms": 184200,
        "received_at": "2026-11-13T10:00:00.250000Z",
        "sample_rate_hz": 1000.0,
        "n_samples": 100,
        "stream_kind": "emg",
        "signal_kind": "envelope",
        "units": {"emg": "mV"},
        "data": {"emg": pytest.approx([c / 1023 * 5000 for c in samples], rel=1e-12)},
        "quality": {"emg": {"score": 1.0, "flags": [], "assessed_by": "gateway"}},  # 0 es reposo en una envolvente, no clipping
        "acquisition": {"adc_bits": 10, "vref_v": 5.0, "gain": 1.0, "input_pin": "A0"},
    }


def test_emg_clean_and_clipped_quality(assembler: ChunkAssembler) -> None:
    clean = payload(session(assembler, emg(0, (1,) * 99 + (1022,))))
    assert clean["quality"]["emg"] == {"score": 1.0, "flags": [], "assessed_by": "gateway"}
    clipped = payload(session(assembler, emg(1, (0,) * 3 + (1023,) * 2 + (1024,) + (500,) * 94)))
    assert clipped["quality"]["emg"] == {"score": pytest.approx(0.97), "flags": ["clipping"], "assessed_by": "gateway"}


def test_emg_envelope_at_rest_is_not_clipping(assembler: ChunkAssembler) -> None:
    # El EMG es una envolvente: el músculo en reposo da cuentas cercanas a 0, un valor sano. Solo satura en el máximo.
    rest = payload(session(assembler, emg(0, (0,) * 100)))
    assert rest["quality"]["emg"] == {"score": 1.0, "flags": [], "assessed_by": "gateway"}
    saturated = payload(session(assembler, emg(1, (1023,) * 100)))
    assert saturated["quality"]["emg"] == {"score": 0.0, "flags": ["clipping"], "assessed_by": "gateway"}


def test_emg_uses_gain_and_pin_from_settings(settings: GatewaySettings) -> None:
    a = ChunkAssembler(settings.model_copy(update={"emg_gain": 2.0, "emg_input_pin": "A3", "vref_v": 3.3}))
    a.on_new_boot(1)
    data = payload(session(a, emg(0, (1023,) * 100)))
    assert data["data"]["emg"] == [pytest.approx(1650.0)] * 100
    assert data["acquisition"] == {"adc_bits": 10, "vref_v": 3.3, "gain": 2.0, "input_pin": "A3"}


@pytest.mark.contract
def test_imu_chunk(assembler: ChunkAssembler) -> None:
    samples = ((8192, -4096, 0, 655, -65, 0),) * 9 + ((-8192, 4096, 16384, -655, 65, 131),)
    data = payload(session(assembler, imu(3, samples, t0=500)))
    assert data["stream_id"] == "imu_l3"
    assert (data["stream_kind"], data["sample_rate_hz"], data["n_samples"], data["t0_device_ms"], data["seq"]) == ("imu", 100.0, 10, 500, 0)
    assert data["units"] == {"accel_x": "g", "accel_y": "g", "accel_z": "g", "gyro_x": "deg/s", "gyro_y": "deg/s", "gyro_z": "deg/s"}
    assert data["data"]["accel_x"] == [1.0] * 9 + [-1.0]
    assert data["data"]["accel_y"] == [-0.5] * 9 + [0.5]
    assert data["data"]["accel_z"] == [0.0] * 9 + [2.0]
    assert data["data"]["gyro_x"] == pytest.approx([10.0] * 9 + [-10.0])
    assert data["data"]["gyro_y"] == pytest.approx([-65 / 65.5] * 9 + [65 / 65.5])
    assert data["data"]["gyro_z"] == pytest.approx([0.0] * 9 + [131 / 65.5])
    assert data["config"] == {"accel_range_g": 4, "gyro_range_dps": 500, "dlpf_hz": 44}
    assert data["quality"] == {
        "accel": {"score": 1.0, "flags": [], "assessed_by": "gateway"},
        "gyro": {"score": 1.0, "flags": [], "assessed_by": "gateway"},
    }


@pytest.mark.parametrize(
    ("sample", "accel", "gyro"),
    [
        ((32767, 0, 0, 0, 0, 0), 0.9, 1.0),
        ((0, -32768, 0, 0, 0, 0), 0.9, 1.0),
        ((0, 0, 32767, 0, 0, 0), 0.9, 1.0),
        ((0, 0, 0, -32768, 0, 0), 1.0, 0.9),
        ((0, 0, 0, 0, 32767, 0), 1.0, 0.9),
        ((0, 0, 0, 0, 0, -32768), 1.0, 0.9),
        ((32766, -32767, 0, -32767, 32766, 0), 1.0, 1.0),
    ],
)
def test_imu_clipping_per_sensor(assembler: ChunkAssembler, sample: ImuSample, accel: float, gyro: float) -> None:
    data = payload(session(assembler, imu(0, (sample,) + (QUIET,) * 9)))
    assert data["quality"]["accel"]["score"] == pytest.approx(accel)
    assert data["quality"]["gyro"]["score"] == pytest.approx(gyro)
    assert data["quality"]["accel"]["flags"] == ([] if accel == 1.0 else ["clipping"])
    assert data["quality"]["gyro"]["flags"] == ([] if gyro == 1.0 else ["clipping"])


def test_imu_clipping_counts_samples_not_axes(assembler: ChunkAssembler) -> None:
    both = (32767, 32767, -32768, 0, 0, 0)
    data = payload(session(assembler, imu(0, (both, both) + (QUIET,) * 8)))
    assert data["quality"]["accel"]["score"] == pytest.approx(0.8)


@pytest.mark.parametrize(
    ("update", "message"),
    [({"imu_accel_range_g": 3}, "unsupported accel range: 3"), ({"imu_gyro_range_dps": 300}, "unsupported gyro range: 300")],
)
def test_imu_unsupported_range_in_settings_raises_on_construction(settings: GatewaySettings, update: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ChunkAssembler(settings.model_copy(update=update))


@pytest.mark.parametrize(
    "frame",
    [
        Frame(FrameKind.IMU, 0, 0, (1,) * 10),
        Frame(FrameKind.EMG, 0, 0, (QUIET,) * 100),
        Frame(FrameKind.CORD, 0, 0, (QUIET, QUIET)),
    ],
)
def test_samples_of_the_wrong_shape_raise(assembler: ChunkAssembler, frame: Frame) -> None:
    # El parser nunca construye estos frames: es un error de programación, no un dato del dispositivo.
    with pytest.raises(TypeError, match=f"{frame.kind.name} frame with samples of the wrong shape"):
        assembler.session_chunk(frame, session_id=SESSION, received_at=AT)


@pytest.mark.contract
def test_stretch_chunk_without_calibration(assembler: ChunkAssembler) -> None:
    data = payload(session(assembler, cord(0, (341, 682), t0=184000)))
    assert (data["stream_id"], data["stream_kind"], data["sample_rate_hz"], data["n_samples"]) == ("stretch_lumbar", "stretch", 20.0, 2)
    assert data["units"] == {"adc_counts": "count", "resistance_ohm": "ohm"}
    assert data["data"] == {"adc_counts": [341, 682], "resistance_ohm": [5000.0, 20000.0]}
    assert data["quality"] == {"adc_counts": {"score": 1.0, "flags": [], "assessed_by": "gateway"}}
    assert data["circuit"] == {"adc_bits": 10, "vref_v": 5.0, "series_resistor_ohm": 10000.0, "input_pin": "A1"}
    assert "calibration_id" not in data
    assert "length_mm" not in data["data"]


@pytest.mark.parametrize(
    ("samples", "flags"),
    [((0, 341), ["clipping"]), ((341, 1023), ["clipping", "lead_off"]), ((2000, 341), ["clipping", "lead_off"])],
)
def test_stretch_rails_drop_resistance_for_the_whole_chunk(assembler: ChunkAssembler, samples: tuple[int, int], flags: list[str]) -> None:
    # number_array no admite null y las columnas deben tener n_samples entradas: sin R calculable en una muestra no se
    # publica resistance_ohm (ni length_mm, que depende de ella) en todo el chunk.
    data = payload(session(assembler, cord(0, samples), stretch_calibration(CURVE)))
    assert data["units"] == {"adc_counts": "count"}
    assert data["data"] == {"adc_counts": list(samples)}
    assert data["quality"] == {"adc_counts": {"score": 0.5, "flags": flags, "assessed_by": "gateway"}}
    assert "calibration_id" not in data


@pytest.mark.contract
def test_stretch_chunk_with_calibration_derives_length(assembler: ChunkAssembler) -> None:
    data = payload(session(assembler, cord(0, (341, 512)), stretch_calibration(CURVE)))
    r = 10000 * 512 / 511
    assert data["units"] == {"adc_counts": "count", "resistance_ohm": "ohm", "length_mm": "mm"}
    assert data["data"]["resistance_ohm"] == pytest.approx([5000.0, r])
    assert data["data"]["length_mm"] == pytest.approx([100.0, 200.0 + (r - 10000) / 10000 * 50])
    assert data["calibration_id"] == CAL_ID
    assert data["quality"]["length_mm"] == {"score": 1.0, "flags": [], "assessed_by": "gateway"}


def test_stretch_length_outside_the_curve_is_clamped_and_flagged(assembler: ChunkAssembler) -> None:
    data = payload(session(assembler, cord(0, (100, 1000)), stretch_calibration(CURVE)))
    assert data["data"]["length_mm"] == [100.0, 250.0]
    assert data["quality"]["length_mm"] == {"score": 0.0, "flags": ["out_of_calibration"], "assessed_by": "gateway"}
    half = payload(session(assembler, cord(1, (341, 1000)), stretch_calibration(CURVE)))
    assert half["quality"]["length_mm"] == {"score": 0.5, "flags": ["out_of_calibration"], "assessed_by": "gateway"}
    assert half["quality"]["adc_counts"] == {"score": 1.0, "flags": [], "assessed_by": "gateway"}


def test_stretch_length_quality_inherits_the_adc_quality(assembler: ChunkAssembler) -> None:
    session(assembler, cord(0))
    data = payload(session(assembler, cord(2, (341, 682)), stretch_calibration(CURVE)))
    assert data["quality"]["adc_counts"] == {"score": PACKET_GAP_SCORE, "flags": ["packet_gap"], "assessed_by": "gateway"}
    assert data["quality"]["length_mm"] == {"score": PACKET_GAP_SCORE, "flags": ["packet_gap"], "assessed_by": "gateway"}


# --- seq, huecos, duplicados y arranques ---


def test_seq_counts_per_stream(assembler: ChunkAssembler) -> None:
    assert [session(assembler, emg(s)).seq for s in (10, 11, 12)] == [0, 1, 2]
    assert [session(assembler, imu(s)).seq for s in (400, 401)] == [0, 1]
    assert session(assembler, cord(9)).seq == 0
    assert assembler.next_seq(FrameKind.EMG) == 3


def test_gap_consumes_seq_and_flags_the_next_chunk(assembler: ChunkAssembler) -> None:
    session(assembler, emg(0))
    after = payload(session(assembler, emg(3, (500,) * 100)))
    assert after["seq"] == 3
    assert after["dropped_samples"] == 200
    assert after["quality"]["emg"] == {"score": PACKET_GAP_SCORE, "flags": ["packet_gap"], "assessed_by": "gateway"}
    nxt = payload(session(assembler, emg(4, (500,) * 100)))
    assert nxt["seq"] == 4
    assert "dropped_samples" not in nxt
    assert nxt["quality"]["emg"]["flags"] == []


def test_gap_score_keeps_the_lower_of_both(assembler: ChunkAssembler) -> None:
    session(assembler, emg(0))
    data = payload(session(assembler, emg(2, (1023,) * 50 + (500,) * 50)))
    assert data["quality"]["emg"] == {"score": 0.5, "flags": ["clipping", "packet_gap"], "assessed_by": "gateway"}


def test_imu_gap_flags_both_sensors(assembler: ChunkAssembler) -> None:
    session(assembler, imu(0))
    data = payload(session(assembler, imu(2)))
    assert data["dropped_samples"] == 10
    assert data["quality"]["accel"]["flags"] == data["quality"]["gyro"]["flags"] == ["packet_gap"]


def test_seq_wraps_around_contiguously(assembler: ChunkAssembler) -> None:
    session(assembler, cord(65535))
    data = payload(session(assembler, cord(0)))
    assert (data["seq"], data["quality"]["adc_counts"]["flags"]) == (1, [])


@pytest.mark.parametrize(("second", "reason"), [(5, DiscardReason.DUPLICATE), (4, DiscardReason.OUT_OF_ORDER), (0, DiscardReason.OUT_OF_ORDER)])
def test_duplicates_and_rewinds_are_discarded(assembler: ChunkAssembler, second: int, reason: DiscardReason) -> None:
    session(assembler, emg(5))
    result = assembler.session_chunk(emg(second), session_id=SESSION, received_at=AT)
    assert result == Discarded(FrameKind.EMG, second, reason)
    assert assembler.next_seq(FrameKind.EMG) == 1
    assert payload(session(assembler, emg(6)))["seq"] == 1  # el descarte no movió el último frame_seq


@pytest.mark.parametrize("n", [1, 99, 101])
def test_unexpected_sample_count_is_discarded_and_counted_as_lost(assembler: ChunkAssembler, n: int) -> None:
    session(assembler, emg(0))
    result = assembler.session_chunk(emg(1, (500,) * n), session_id=SESSION, received_at=AT)
    assert result == Discarded(FrameKind.EMG, 1, DiscardReason.UNEXPECTED_SAMPLE_COUNT)
    data = payload(session(assembler, emg(2)))
    assert (data["seq"], data["dropped_samples"]) == (2, 100)


def test_new_boot_restarts_seq_and_frame_tracking(assembler: ChunkAssembler) -> None:
    session(assembler, emg(100))
    session(assembler, emg(101))
    assert assembler.on_new_boot(42) is True
    assert assembler.boot_id == 42
    data = payload(session(assembler, emg(0)))
    assert (data["boot_id"], data["seq"], data["quality"]["emg"]["flags"]) == (42, 0, [])
    assert "dropped_samples" not in data


def test_same_boot_id_again_keeps_the_counters(assembler: ChunkAssembler) -> None:
    session(assembler, emg(100))
    assert assembler.on_new_boot(BOOT) is False
    assert session(assembler, emg(101)).seq == 1


def test_emg_has_no_background(assembler: ChunkAssembler) -> None:
    result = assembler.background_chunk(emg(0), session_id=SESSION, received_at=AT)
    assert result == Discarded(FrameKind.EMG, 0, DiscardReason.NOT_BACKGROUND_STREAM)


def test_emg_in_background_still_tracks_frame_seq(assembler: ChunkAssembler) -> None:
    for s in range(5):
        assembler.background_chunk(emg(s), session_id=SESSION, received_at=AT)
    assert assembler.background_chunk(emg(4), session_id=SESSION, received_at=AT) == Discarded(FrameKind.EMG, 4, DiscardReason.DUPLICATE)
    data = payload(session(assembler, emg(5)))
    assert (data["seq"], data["quality"]["emg"]["flags"]) == (0, [])
    assert "dropped_samples" not in data


# --- fondo ---


def test_background_accumulator_rejects_emg() -> None:
    with pytest.raises(ValueError, match="no background for EMG"):
        BackgroundAccumulator(FrameKind.EMG)


def test_background_accumulator_averages_imu_with_half_to_even() -> None:
    acc = BackgroundAccumulator(FrameKind.IMU)
    # Sumas por eje 25, 35, -25, -35, 5 y 20 en 10 muestras: medias 2.5 -> 2, 3.5 -> 4, -2.5 -> -2, -3.5 -> -4 (empates
    # al par), 0.5 -> 0 y 2.0 -> 2.
    frame_samples = ((3, 4, -3, -4, 1, 2),) * 5 + ((2, 3, -2, -3, 0, 2),) * 5
    steps = [acc.add(Frame(FrameKind.IMU, s, 1000 + 100 * s, frame_samples), 0) for s in range(10)]
    assert [st.window for st in steps[:9]] == [None] * 9
    assert all(st.lost_windows == 0 for st in steps)
    window = steps[9].window
    assert window is not None
    assert window.t0_ms == 1000
    assert window.samples == ((2, 4, -2, -4, 0, 2),) * 10
    assert window.after_gap is False
    assert [f.frame_seq for f in window.frames] == list(range(10))


def test_background_accumulator_averages_cord_pairs() -> None:
    acc = BackgroundAccumulator(FrameKind.CORD)
    pairs = [(1, 2), (2, 3), (10, 10), (0, 1023), (5, 8), (100, 101), (7, 7), (8, 9), (9, 10), (1022, 1023)]
    steps = [acc.add(Frame(FrameKind.CORD, s, 50 * s, pairs[s]), 0) for s in range(10)]
    window = steps[-1].window
    assert window is not None
    assert window.samples == (2, 2, 10, 512, 6, 100, 7, 8, 10, 1022)


def test_background_gap_drops_the_partial_window_and_realigns() -> None:
    acc = BackgroundAccumulator(FrameKind.CORD)
    for s in range(4):
        assert acc.add(cord(s), 0).window is None
    # Faltan los frames 4 y 5: la ventana parcial (4 frames) y los 2 perdidos tocan ceil(6/10) = 1 ventana de 1 s.
    first = acc.add(cord(6, t0=1600), 2)
    assert (first.lost_windows, first.window) == (1, None)
    steps = [acc.add(cord(s), 0) for s in range(7, 16)]
    window = steps[-1].window
    assert window is not None
    assert (window.t0_ms, window.after_gap, window.frames[0].frame_seq) == (1600, True, 6)
    assert all(st.lost_windows == 0 for st in steps)


@pytest.mark.parametrize(("partial", "gap", "lost"), [(0, 1, 1), (0, 10, 1), (0, 11, 2), (9, 1, 1), (9, 2, 2), (3, 30, 4)])
def test_background_lost_windows_round_up(partial: int, gap: int, lost: int) -> None:
    acc = BackgroundAccumulator(FrameKind.IMU)
    for s in range(partial):
        acc.add(imu(s), 0)
    assert acc.add(imu(partial + gap), gap).lost_windows == lost


def test_background_reset_drops_the_window_and_the_gap_flag() -> None:
    acc = BackgroundAccumulator(FrameKind.CORD)
    acc.add(cord(0), 0)
    acc.add(cord(5), 4)
    acc.reset()
    steps = [acc.add(cord(s, t0=s), 0) for s in range(10, 20)]
    window = steps[-1].window
    assert window is not None
    assert (window.t0_ms, window.after_gap) == (10, False)


def test_background_chunk_imu(assembler: ChunkAssembler) -> None:
    results = [assembler.background_chunk(imu(s, t0=2000 + 100 * s), session_id=SESSION, received_at=AT) for s in range(10)]
    assert results[:9] == [Pending(FrameKind.IMU, s) for s in range(9)]
    chunk = results[9]
    assert isinstance(chunk, ImuChunk)
    data = payload(chunk)
    assert (data["seq"], data["t0_device_ms"], data["sample_rate_hz"], data["n_samples"]) == (0, 2000, 10.0, 10)
    assert data["data"]["accel_z"] == [1.0] * 10
    assert data["data"]["gyro_x"] == pytest.approx([10.0] * 10)
    assert data["quality"]["accel"] == {"score": 1.0, "flags": [], "assessed_by": "gateway"}


def test_background_quality_is_the_worst_frame(assembler: ChunkAssembler) -> None:
    calibration = stretch_calibration(CURVE)
    frames = [cord(s, (0, 341) if s == 3 else (341, 341)) for s in range(10)]
    results = [assembler.background_chunk(f, session_id=SESSION, received_at=AT, stretch_calibration=calibration) for f in frames]
    chunk = results[-1]
    assert isinstance(chunk, StretchChunk)
    data = payload(chunk)
    # Frame 3 tiene media round(170.5) = 170, calculable: R y longitud se publican.
    assert data["data"]["adc_counts"] == [341, 341, 341, 170, 341, 341, 341, 341, 341, 341]
    assert data["quality"]["adc_counts"] == {"score": 0.5, "flags": ["clipping"], "assessed_by": "gateway"}
    assert data["quality"]["length_mm"] == {"score": pytest.approx(0.5), "flags": ["clipping", "out_of_calibration"], "assessed_by": "gateway"}
    assert data["calibration_id"] == CAL_ID


def test_background_seq_after_a_gap(assembler: ChunkAssembler) -> None:
    for s in range(10):
        assembler.background_chunk(cord(s), session_id=SESSION, received_at=AT)
    assert assembler.next_seq(FrameKind.CORD) == 1
    for s in range(10, 13):
        assembler.background_chunk(cord(s), session_id=SESSION, received_at=AT)
    # Hueco de 25 frames (13..37): ventana parcial de 3 + 25 perdidos -> ceil(28/10) = 3 seq consumidos.
    assert assembler.background_chunk(cord(38), session_id=SESSION, received_at=AT) == Pending(FrameKind.CORD, 38)
    assert assembler.next_seq(FrameKind.CORD) == 4
    last = [assembler.background_chunk(cord(s), session_id=SESSION, received_at=AT) for s in range(39, 48)][-1]
    assert isinstance(last, StretchChunk)
    data = payload(last)
    assert data["seq"] == 4
    assert data["quality"]["adc_counts"] == {"score": PACKET_GAP_SCORE, "flags": ["packet_gap"], "assessed_by": "gateway"}
    assert "dropped_samples" not in data


def test_background_discards_duplicates_and_bad_counts(assembler: ChunkAssembler) -> None:
    assembler.background_chunk(cord(0), session_id=SESSION, received_at=AT)
    assert assembler.background_chunk(cord(0), session_id=SESSION, received_at=AT) == Discarded(FrameKind.CORD, 0, DiscardReason.DUPLICATE)
    bad = assembler.background_chunk(cord(1, (1, 2, 3)), session_id=SESSION, received_at=AT)
    assert bad == Discarded(FrameKind.CORD, 1, DiscardReason.UNEXPECTED_SAMPLE_COUNT)


def test_background_and_session_share_the_seq(assembler: ChunkAssembler) -> None:
    for s in range(10):
        assembler.background_chunk(imu(s), session_id=SESSION, received_at=AT)
    assembler.background_chunk(imu(10), session_id=SESSION, received_at=AT)
    assembler.reset_background()  # empieza la sesión: la ventana parcial se tira sin consumir seq
    assert session(assembler, imu(11)).seq == 1
    assert session(assembler, imu(12)).seq == 2
    assembler.reset_background()  # acaba la sesión
    results = [assembler.background_chunk(imu(s), session_id=SESSION, received_at=AT) for s in range(13, 23)]
    assert isinstance(results[-1], ImuChunk)
    assert results[-1].seq == 3
    assert results[-1].quality.accel.flags in (None, [])


def test_new_boot_resets_the_background(assembler: ChunkAssembler) -> None:
    for s in range(5):
        assembler.background_chunk(cord(s), session_id=SESSION, received_at=AT)
    assembler.on_new_boot(7)
    results = [assembler.background_chunk(cord(s, t0=100 * s), session_id=SESSION, received_at=AT) for s in range(10)]
    chunk = results[-1]
    assert isinstance(chunk, StretchChunk)
    assert (chunk.boot_id, chunk.seq, chunk.t0_device_ms) == (7, 0, 0)


def test_background_window_time_is_uniform() -> None:
    # 10 frames de 100 ms -> 10 muestras a 10 Hz que cubren exactamente 1 s.
    assert BACKGROUND_FRAMES * BATCH_MS / 1000 * BACKGROUND_SAMPLE_RATE_HZ == BACKGROUND_FRAMES
    assert math.isclose(1000 / BACKGROUND_SAMPLE_RATE_HZ, BATCH_MS)
