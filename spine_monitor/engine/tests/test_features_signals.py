"""Muestras de un segmento recortadas a una ventana UTC (#42, #44): tiempos UTC y valores por canal.

Entradas hechas a mano con el reloj real: cada muestra ``i`` de un chunk cae en ``t0_device_ms + i·1000/rate`` y su
UTC sale del ajuste del ``boot_id`` (redondeando los milisegundos del dispositivo, como hacía ``repetitions_imu``).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from spine_contracts import ClockSync, EmgChunk, ImuChunk
from spine_engine.features.clock import DeviceClock
from spine_engine.features.ordering import Segment
from spine_engine.features.signals import SegmentSamples, segment_samples

T0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)
BOOT = 7


def ms(value: float) -> datetime:
    return T0 + timedelta(milliseconds=value)


def _chunk(t0_device_ms: int, seq: int, rate: float, gyro: dict[str, list[float]], boot_id: int = BOOT) -> ImuChunk:
    n = len(next(iter(gyro.values())))
    channels = {name: gyro.get(name, [0.0] * n) for name in ("gyro_x", "gyro_y", "gyro_z")}
    raw: dict[str, Any] = {
        "schema_version": "1.0.0",
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "stream_id": "imu_l3",
        "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01",
        "source": "device",
        "boot_id": boot_id,
        "seq": seq,
        "t0_device_ms": t0_device_ms,
        "received_at": T0.isoformat(),
        "sample_rate_hz": rate,
        "n_samples": n,
        "stream_kind": "imu",
        "units": {"accel_x": "g", "accel_y": "g", "accel_z": "g", "gyro_x": "deg/s", "gyro_y": "deg/s", "gyro_z": "deg/s"},
        "data": {"accel_x": [0.0] * n, "accel_y": [0.0] * n, "accel_z": [0.0] * n, **channels},
        "quality": {"accel": {"score": 1.0, "flags": [], "assessed_by": "gateway"}, "gyro": {"score": 1.0, "flags": [], "assessed_by": "gateway"}},
    }
    return ImuChunk.model_validate(raw)


def _sync(boot_id: int, device_ms: int, utc: datetime) -> ClockSync:
    return ClockSync.model_validate(
        {
            "schema_version": "1.0.0",
            "astronaut_id": "AST-01",
            "device_id": "wb-01",
            "boot_id": boot_id,
            "device_ms": device_ms,
            "utc": utc.isoformat(),
            "uncertainty_ms": 5,
            "method": "round_trip",
        }
    )


CLOCK = DeviceClock.from_syncs([_sync(BOOT, 1000, T0)])


def _segment(*chunks: ImuChunk, boot_id: int = BOOT) -> Segment:
    return Segment("imu_l3", boot_id, tuple(chunks), sum(c.n_samples for c in chunks), 0)


def test_the_whole_segment_gives_utc_instants_sample_ends_and_values_per_channel():
    # 10 Hz, ancla device_ms=1000 <-> T0: la muestra 0 (device 1000 ms) cae en T0 y cada muestra dura 100 ms
    segment = _segment(_chunk(1000, 0, 10.0, {"gyro_x": [1.0, 2.0, 3.0], "gyro_z": [4.0, 5.0, 6.0]}))
    got = segment_samples(CLOCK, segment, ("gyro_x", "gyro_z"))
    assert isinstance(got, SegmentSamples)
    assert got.times == (ms(0), ms(100), ms(200))
    assert got.ends == (ms(100), ms(200), ms(300))
    assert list(got.channels) == ["gyro_x", "gyro_z"]
    assert got.channels["gyro_x"].dtype == np.float64
    assert got.channels["gyro_x"].tolist() == [1.0, 2.0, 3.0]
    assert got.channels["gyro_z"].tolist() == [4.0, 5.0, 6.0]


def test_the_window_is_half_open_start_included_end_excluded():
    segment = _segment(_chunk(1000, 0, 10.0, {"gyro_x": [0.0, 1.0, 2.0, 3.0, 4.0]}))
    got = segment_samples(CLOCK, segment, ("gyro_x",), (ms(100), ms(300)))
    assert got.times == (ms(100), ms(200))
    assert got.channels["gyro_x"].tolist() == [1.0, 2.0]
    # inicio justo tras una muestra la deja fuera; fin justo tras otra la incluye
    got = segment_samples(CLOCK, segment, ("gyro_x",), (ms(100.001), ms(300.001)))
    assert got.channels["gyro_x"].tolist() == [2.0, 3.0]


def test_the_window_keeps_ends_aligned_with_times_and_values():
    segment = _segment(_chunk(1000, 0, 10.0, {"gyro_x": [0.0, 1.0, 2.0, 3.0, 4.0]}))
    got = segment_samples(CLOCK, segment, ("gyro_x",), (ms(200), ms(500)))
    assert got.times == (ms(200), ms(300), ms(400))
    assert got.ends == (ms(300), ms(400), ms(500))
    assert got.channels["gyro_x"].tolist() == [2.0, 3.0, 4.0]


def test_an_empty_or_disjoint_window_gives_empty_samples_that_keep_the_channels():
    segment = _segment(_chunk(1000, 0, 10.0, {"gyro_x": [0.0, 1.0, 2.0]}))
    for window in ((ms(100), ms(100)), (ms(300), ms(100)), (ms(-5000), ms(-1000)), (ms(5000), ms(6000))):
        got = segment_samples(CLOCK, segment, ("gyro_x", "gyro_y"), window)
        assert got.times == ()
        assert got.ends == ()
        assert list(got.channels) == ["gyro_x", "gyro_y"]
        assert got.channels["gyro_x"].shape == (0,)
        assert got.channels["gyro_x"].dtype == np.float64


def test_a_segment_without_chunks_or_without_a_clock_anchor_gives_empty_samples():
    chunk = _chunk(1000, 0, 10.0, {"gyro_x": [0.0, 1.0]}, boot_id=BOOT + 1)
    for segment in (_segment(), _segment(chunk, boot_id=BOOT + 1)):
        got = segment_samples(CLOCK, segment, ("gyro_x",))
        assert got.times == ()
        assert got.channels["gyro_x"].shape == (0,)


def test_each_chunk_uses_its_own_start_time_across_gaps():
    # el segundo chunk empieza 1 s tarde: su UTC sale de su t0, no de la continuidad del índice
    segment = _segment(_chunk(1000, 0, 10.0, {"gyro_x": [1.0, 2.0]}), _chunk(3000, 1, 10.0, {"gyro_x": [3.0, 4.0]}))
    got = segment_samples(CLOCK, segment, ("gyro_x",))
    assert got.times == (ms(0), ms(100), ms(2000), ms(2100))
    assert got.channels["gyro_x"].tolist() == [1.0, 2.0, 3.0, 4.0]
    assert segment_samples(CLOCK, segment, ("gyro_x",), (ms(1000), ms(3000))).channels["gyro_x"].tolist() == [3.0, 4.0]


def test_device_milliseconds_are_rounded_before_converting_to_utc():
    # 6 Hz: paso 166,67 ms. Muestra 4: 1000 + 666,67 -> 1667 -> +667 ms; su fin: 1000 + 833,33 -> 1833 -> +833 ms
    segment = _segment(_chunk(1000, 0, 6.0, {"gyro_x": [0.0] * 5}))
    got = segment_samples(CLOCK, segment, ("gyro_x",))
    assert got.times[4] == ms(667)
    assert got.ends[4] == ms(833)


def test_different_boots_convert_with_their_own_clock_fit():
    clock = DeviceClock.from_syncs([_sync(BOOT, 1000, T0), _sync(BOOT + 1, 0, T0 + timedelta(seconds=60))])
    first_segment = _segment(_chunk(1000, 0, 10.0, {"gyro_x": [1.0, 2.0]}))
    second_segment = _segment(_chunk(0, 0, 10.0, {"gyro_x": [3.0, 4.0]}, boot_id=BOOT + 1), boot_id=BOOT + 1)
    first = segment_samples(clock, first_segment, ("gyro_x",))
    second = segment_samples(clock, second_segment, ("gyro_x",))
    assert first.times == (ms(0), ms(100))
    assert second.times == (ms(60_000), ms(60_100))
    # una ventana solo del segundo arranque deja vacío el primero
    window = (ms(59_000), ms(61_000))
    assert segment_samples(clock, first_segment, ("gyro_x",), window).times == ()
    assert segment_samples(clock, second_segment, ("gyro_x",), window).times == second.times


def test_a_chunk_without_a_requested_channel_is_skipped():
    segment = _segment(_chunk(1000, 0, 10.0, {"gyro_x": [1.0, 2.0]}))
    got = segment_samples(CLOCK, segment, ("gyro_x", "emg_ch0"))
    assert got.times == ()
    assert got.channels["gyro_x"].shape == (0,)
    assert got.channels["emg_ch0"].shape == (0,)


def test_non_finite_values_are_kept_as_they_come():
    segment = _segment(_chunk(1000, 0, 10.0, {"gyro_x": [1.0, float("nan"), 3.0]}))
    values = segment_samples(CLOCK, segment, ("gyro_x",)).channels["gyro_x"]
    assert np.isnan(values[1])
    assert values[[0, 2]].tolist() == [1.0, 3.0]


def test_an_emg_chunk_whose_channel_is_a_plain_list_gives_its_values():
    # el canal ``emg`` del contrato es una lista simple (los del IMU son RootModel): ambos deben funcionar
    raw: dict[str, Any] = {
        "schema_version": "1.0.0",
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "stream_id": "emg_lat_left",
        "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01",
        "source": "device",
        "boot_id": BOOT,
        "seq": 0,
        "t0_device_ms": 1000,
        "received_at": T0.isoformat(),
        "sample_rate_hz": 10.0,
        "n_samples": 3,
        "stream_kind": "emg",
        "signal_kind": "raw",
        "units": {"emg": "mV"},
        "data": {"emg": [1.0, -2.0, 3.0]},
        "quality": {"emg": {"score": 1.0, "flags": [], "assessed_by": "gateway"}},
    }
    segment = Segment("emg_lat_left", BOOT, (EmgChunk.model_validate(raw),), 3, 0)

    got = segment_samples(CLOCK, segment, ("emg",))

    assert got.times == (ms(0), ms(100), ms(200))
    assert got.channels["emg"].tolist() == [1.0, -2.0, 3.0]
