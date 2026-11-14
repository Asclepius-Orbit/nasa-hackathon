"""Emulador del Arduino -> protocolo -> ensamblador de chunks, sin sockets ni reloj de pared (#131).

Recorre el camino que hace el gateway real con el emulador: ``EmulatedArduino.lines_until`` -> ``parse_line`` ->
``ChunkAssembler.session_chunk`` / ``background_chunk``. Comprueba que los chunks son válidos contra su schema, que los tamaños y
frecuencias son los del firmware (100/10/2 muestras a 1000/100/20 Hz en lotes de 100 ms; 10 Hz en 1 s para el fondo) y que las
repeticiones que se cuentan en gyro_x ya convertido a °/s y el EMG y el cordón comparten fase. Los umbrales de fase son del
modelo del emulador (señal sintética de ``spine_physiology``, sin EMG real con el que compararla).
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from scipy.signal import find_peaks
from spine_contracts import EmgChunk, ImuChunk, StretchCalibration, StretchChunk, dump, validation
from spine_gateway.assembler import ChunkAssembler, Discarded, Pending
from spine_gateway.emulator import EmulatedArduino
from spine_gateway.protocol import Frame, FrameKind, Hello, parse_line
from spine_gateway.settings import GatewaySettings

SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"
CAL_ID = "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f11"
AT = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
SECONDS = 24  # 6 repeticiones de 3 s cada 4 s (la primera a 0,5 s)
REP_PERIOD_S = 4.0
RATES = {FrameKind.EMG: 1000.0, FrameKind.IMU: 100.0, FrameKind.CORD: 20.0}
SAMPLES = {FrameKind.EMG: 100, FrameKind.IMU: 10, FrameKind.CORD: 2}


def calibration(settings: GatewaySettings) -> StretchCalibration:
    points = list(settings.stretch_curve)
    return StretchCalibration.model_validate(
        {
            "schema_version": "1.0.0", "calibration_id": CAL_ID, "astronaut_id": settings.astronaut_id, "device_id": settings.device_id,
            "stream_id": settings.stretch_stream_id, "created_at": "2026-11-13T07:05:00Z", "source": "device", "calibration_type": "stretch",
            "rest_resistance_ohm": points[0][0], "rest_length_mm": points[0][1],
            "curve": {"model": "piecewise_linear", "points": [{"resistance_ohm": r, "length_mm": length} for r, length in points]},
        }
    )  # fmt: skip


def device_frames(seed: int, seconds: float = SECONDS) -> tuple[Hello, list[Frame]]:
    arduino = EmulatedArduino(seed=seed, boot_id=4242)
    messages = [m for m in (parse_line(line) for line in arduino.lines_until(round(seconds * 1000)) if not line.startswith("#"))]
    hello = next(m for m in messages if isinstance(m, Hello))
    return hello, [m for m in messages if isinstance(m, Frame)]


def session_chunks(settings: GatewaySettings, seed: int, seconds: float = SECONDS) -> list[EmgChunk | ImuChunk | StretchChunk]:
    hello, frames = device_frames(seed, seconds)
    assembler = ChunkAssembler(settings)
    assert assembler.on_new_boot(hello.boot_id)
    out: list[EmgChunk | ImuChunk | StretchChunk] = []
    for frame in frames:
        received = AT + timedelta(milliseconds=frame.t0_ms + 100)
        chunk = assembler.session_chunk(frame, session_id=SESSION, received_at=received, stretch_calibration=calibration(settings))
        assert not isinstance(chunk, Discarded), chunk
        out.append(chunk)
    return out


def of(chunks: list, stream_kind: str) -> list:
    return [c for c in chunks if c.stream_kind == stream_kind]


@pytest.mark.integration
def test_every_assembled_chunk_is_schema_valid_with_the_firmware_rates_and_sizes(settings: GatewaySettings) -> None:
    chunks = session_chunks(settings, seed=5)
    kinds = {"emg": FrameKind.EMG, "imu": FrameKind.IMU, "stretch": FrameKind.CORD}
    assert {c.stream_kind for c in chunks} == set(kinds)
    for chunk in chunks:
        payload = dump(chunk)
        assert validation.errors("telemetry/stream-chunk", payload) == [], payload["stream_id"]
        kind = kinds[chunk.stream_kind]
        assert payload["sample_rate_hz"] == RATES[kind] and payload["n_samples"] == SAMPLES[kind]
        assert all(len(values) == SAMPLES[kind] for values in payload["data"].values())
        assert payload["source"] == "device" and payload["session_id"] == SESSION
    for stream_kind in kinds:
        series = of(chunks, stream_kind)
        assert len(series) == SECONDS * 10
        assert [c.seq for c in series] == list(range(len(series)))
        assert np.all(np.diff([c.t0_device_ms for c in series]) == 100)


@pytest.mark.integration
def test_the_rep_count_in_gyro_x_matches_the_emulated_rep_period_and_the_emg_and_cord_share_its_phase(settings: GatewaySettings) -> None:
    chunks = session_chunks(settings, seed=5)
    # Mapeo del montaje del ejemplo: el giro de flexión sale por gyro_x del Arduino. Picos positivos > 15 dps (el pico del
    # modelo es 1,25 x ROM = 37-56 dps) con una separación mínima de 2 s dentro de un periodo de 4 s: una por repetición.
    gyro_x = np.concatenate([dump(c)["data"]["gyro_x"] for c in of(chunks, "imu")])
    peaks, _ = find_peaks(gyro_x, height=15.0, distance=200)
    assert len(peaks) == SECONDS // int(REP_PERIOD_S)
    assert np.all(np.diff(peaks) / RATES[FrameKind.IMU] == pytest.approx(REP_PERIOD_S, abs=0.2))
    # Fase compartida por chunk de 100 ms: nivel medio del EMG (envolvente, mV), |gyro_x| medio y longitud del cordón (mm,
    # con la curva por defecto). Observado en 4 semillas: EMG-cordón 0,986-0,989 (umbral 0,9), EMG-gyro suavizado 1 s
    # 0,54-0,55 (umbral 0,4: el gyro es la derivada, nulo a mitad de rep), recorrido del cordón 86-105 mm (umbral 40).
    emg = np.array([np.mean(dump(c)["data"]["emg"]) for c in of(chunks, "emg")])
    gyro = np.array([np.mean(np.abs(dump(c)["data"]["gyro_x"])) for c in of(chunks, "imu")])
    cord = np.array([np.mean(dump(c)["data"]["length_mm"]) for c in of(chunks, "stretch")])
    smooth = np.ones(10) / 10.0
    edge = slice(10, -10)
    assert np.corrcoef(emg, cord)[0, 1] > 0.9
    assert np.corrcoef(np.convolve(emg, smooth, "same")[edge], np.convolve(gyro, smooth, "same")[edge])[0, 1] > 0.4
    assert cord.max() - cord.min() > 40.0  # mm: el cordón se estira con la flexión


@pytest.mark.integration
def test_the_background_is_one_second_windows_at_10_hz_for_imu_and_cord_and_never_emg(settings: GatewaySettings) -> None:
    hello, frames = device_frames(seed=5, seconds=12)
    assembler = ChunkAssembler(settings)
    assembler.on_new_boot(hello.boot_id)
    emitted = {FrameKind.EMG: 0, FrameKind.IMU: [], FrameKind.CORD: []}
    discarded = 0
    for frame in frames:
        result = assembler.background_chunk(frame, session_id=SESSION, received_at=AT, stretch_calibration=calibration(settings))
        if isinstance(result, Discarded):
            discarded += 1
            assert frame.kind is FrameKind.EMG
        elif not isinstance(result, Pending):
            assert validation.errors("telemetry/stream-chunk", dump(result)) == []
            assert result.sample_rate_hz == 10.0 and result.n_samples == 10
            emitted[frame.kind].append(result)  # type: ignore[union-attr]
    assert discarded == 120  # los 120 lotes de EMG de 12 s
    for kind in (FrameKind.IMU, FrameKind.CORD):
        chunks = emitted[kind]
        assert len(chunks) == 12
        assert [c.seq for c in chunks] == list(range(12))  # type: ignore[union-attr]
        assert np.all(np.diff([c.t0_device_ms for c in chunks]) == 1000)  # type: ignore[union-attr]


@pytest.mark.integration
def test_the_same_seed_assembles_the_same_chunks_and_another_seed_different_ones(settings: GatewaySettings) -> None:
    a = [dump(c) for c in session_chunks(settings, seed=5, seconds=6)]
    b = [dump(c) for c in session_chunks(settings, seed=5, seconds=6)]
    c = [dump(c) for c in session_chunks(settings, seed=6, seconds=6)]
    assert a == b
    assert a != c
