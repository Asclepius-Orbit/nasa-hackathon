"""Propiedades del ensamblado: monotonía de las conversiones, ida y vuelta del divisor, chunks siempre válidos y fondo."""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import EmgChunk, ImuChunk, StretchCalibration, StretchChunk, dump, validation
from spine_gateway.assembler import (
    BACKGROUND_FRAMES,
    SAMPLES_PER_BATCH,
    BackgroundAccumulator,
    ChunkAssembler,
    counts_to_mv,
    counts_to_resistance,
    resistance_to_length,
)
from spine_gateway.protocol import Frame, FrameKind, ImuSample
from spine_gateway.settings import GatewaySettings

pytestmark = pytest.mark.property

SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"
AT = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
U16 = st.integers(0, 0xFFFF)
I16 = st.integers(-0x8000, 0x7FFF)
BITS = st.integers(8, 16)
IMU_SAMPLE: st.SearchStrategy[ImuSample] = st.tuples(I16, I16, I16, I16, I16, I16)


def frames(kind: FrameKind) -> st.SearchStrategy[Frame]:
    n = SAMPLES_PER_BATCH[kind]
    values: st.SearchStrategy[tuple[int, ...]] | st.SearchStrategy[tuple[ImuSample, ...]]
    if kind is FrameKind.IMU:
        values = st.lists(IMU_SAMPLE, min_size=n, max_size=n).map(tuple)
    else:
        values = st.lists(st.one_of(U16, st.integers(0, 1023), st.sampled_from([0, 1023])), min_size=n, max_size=n).map(tuple)
    return st.builds(Frame, kind=st.just(kind), frame_seq=U16, t0_ms=st.integers(0, 0xFFFFFFFF), samples=values)


@st.composite
def curves(draw: st.DrawFn) -> list[tuple[float, float]]:
    """Curvas resistencia -> longitud crecientes en ambas coordenadas, como las de una calibración real."""
    rs = draw(st.lists(st.floats(1.0, 1e6), min_size=2, max_size=6, unique=True).map(sorted))
    ls = draw(st.lists(st.floats(1.0, 1e4), min_size=len(rs), max_size=len(rs), unique=True).map(sorted))
    return list(zip(rs, ls, strict=True))


@given(bits=BITS, a=st.integers(0, 0xFFFF), b=st.integers(0, 0xFFFF), gain=st.floats(0.1, 1000))
def test_counts_to_mv_is_monotonic(bits: int, a: int, b: int, gain: float) -> None:
    lo, hi = sorted((a, b))
    assert counts_to_mv(lo, adc_bits=bits, vref_v=5.0, gain=gain) <= counts_to_mv(hi, adc_bits=bits, vref_v=5.0, gain=gain)


@given(bits=BITS, data=st.data(), rs=st.floats(10.0, 1e6))
def test_resistance_is_strictly_increasing_inside_the_rails(bits: int, data: st.DataObject, rs: float) -> None:
    top = (1 << bits) - 2
    a = data.draw(st.integers(1, top - 1))
    b = data.draw(st.integers(a + 1, top))
    ra = counts_to_resistance(a, adc_bits=bits, series_resistor_ohm=rs)
    rb = counts_to_resistance(b, adc_bits=bits, series_resistor_ohm=rs)
    assert ra is not None and rb is not None
    assert 0 < ra < rb


@given(bits=BITS, data=st.data(), rs=st.floats(10.0, 1e6))
def test_resistance_round_trips_to_counts(bits: int, data: st.DataObject, rs: float) -> None:
    full = (1 << bits) - 1
    counts = data.draw(st.integers(1, full - 1))
    r = counts_to_resistance(counts, adc_bits=bits, series_resistor_ohm=rs)
    assert r is not None
    # Inversa del divisor: counts = full · R / (Rs + R).
    assert full * r / (rs + r) == pytest.approx(counts, rel=1e-9)


@given(curve=curves(), a=st.floats(0.0, 2e6), b=st.floats(0.0, 2e6))
def test_length_is_monotonic_and_bounded(curve: list[tuple[float, float]], a: float, b: float) -> None:
    lo, hi = sorted((a, b))
    l_lo, in_lo = resistance_to_length(lo, curve)
    l_hi, in_hi = resistance_to_length(hi, curve)
    assert curve[0][1] <= l_lo <= l_hi <= curve[-1][1]
    assert in_lo == (curve[0][0] <= lo <= curve[-1][0])
    assert in_hi == (curve[0][0] <= hi <= curve[-1][0])


@given(curve=curves(), index=st.integers(0, 5))
def test_length_hits_every_point(curve: list[tuple[float, float]], index: int) -> None:
    r, length = curve[index % len(curve)]
    assert resistance_to_length(r, curve) == (pytest.approx(length, rel=1e-9), True)


@given(
    frame=st.sampled_from(list(FrameKind)).flatmap(frames),
    calibrated=st.booleans(),
    background=st.booleans(),
    boot=st.integers(0, 0xFFFFFFFF),
)
def test_any_valid_frame_yields_a_schema_valid_chunk(settings: GatewaySettings, frame: Frame, calibrated: bool, background: bool, boot: int) -> None:
    calibration = (
        StretchCalibration.model_validate(
            {
                "schema_version": "1.0.0",
                "calibration_id": "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f11",
                "astronaut_id": "AST-01",
                "device_id": "wb-01",
                "stream_id": "stretch_lumbar",
                "created_at": "2026-11-13T07:05:00Z",
                "source": "device",
                "calibration_type": "stretch",
                "rest_resistance_ohm": 5000.0,
                "rest_length_mm": 100.0,
                "curve": {
                    "model": "piecewise_linear",
                    "points": [{"resistance_ohm": 5000.0, "length_mm": 100.0}, {"resistance_ohm": 10000.0, "length_mm": 200.0}],
                },
            }
        )
        if calibrated
        else None
    )
    assembler = ChunkAssembler(settings)
    assembler.on_new_boot(boot)
    if background and frame.kind is not FrameKind.EMG:
        # La ventana se cierra con 10 frames contiguos: repite el mismo contenido avanzando frame_seq.
        frames = [Frame(frame.kind, (frame.frame_seq + i) % 0x10000, frame.t0_ms, frame.samples) for i in range(BACKGROUND_FRAMES)]
        results = [assembler.background_chunk(f, session_id=SESSION, received_at=AT, stretch_calibration=calibration) for f in frames]
        chunk = results[-1]
    else:
        chunk = assembler.session_chunk(frame, session_id=SESSION, received_at=AT, stretch_calibration=calibration)
    assert isinstance(chunk, EmgChunk | ImuChunk | StretchChunk)
    assert validation.errors("telemetry/stream-chunk", dump(chunk)) == []


@given(gaps=st.lists(st.integers(0, 3), min_size=1, max_size=60), kind=st.sampled_from([FrameKind.IMU, FrameKind.CORD]))
def test_background_emits_one_window_per_ten_contiguous_frames(gaps: list[int], kind: FrameKind) -> None:
    """Cada ventana sale justo cuando se cierran 10 frames contiguos desde el último hueco o la última ventana."""
    acc = BackgroundAccumulator(kind)
    sample: int | ImuSample = (0, 0, 0, 0, 0, 0) if kind is FrameKind.IMU else 500
    samples = (sample,) * SAMPLES_PER_BATCH[kind]
    seq = -1
    run = 0
    for gap in gaps:
        seq += 1 + gap
        run = 1 if gap else run + 1
        step = acc.add(Frame(kind, seq, 0, samples), gap)  # pyright: ignore[reportArgumentType]  # samples es int o ImuSample según kind
        if run == BACKGROUND_FRAMES:
            assert step.window is not None
            assert [f.frame_seq for f in step.window.frames] == list(range(seq - BACKGROUND_FRAMES + 1, seq + 1))
            run = 0
        else:
            assert step.window is None
        assert (step.lost_windows > 0) == (gap > 0)
