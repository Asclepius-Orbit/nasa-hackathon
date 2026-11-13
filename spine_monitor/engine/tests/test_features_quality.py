"""Calidad común: cobertura, saturación, score y flags (#40)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import EmgChunk, ImuChunk, QualityFlag, StretchChunk
from spine_contracts.models.telemetry.stream_chunk_schema import Config
from spine_engine.features.ordering import Segment, order_chunks
from spine_engine.features.quality import assess_quality, saturation_counts

_COMMON = {
    "schema_version": "1.0.0",
    "astronaut_id": "AST-01",
    "device_id": "wb-01",
    "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01",
    "source": "device",
    "boot_id": 7,
    "seq": 0,
    "t0_device_ms": 0,
    "received_at": "2026-11-13T10:03:05.020Z",
}
_GQ = {"score": 1.0, "flags": [], "assessed_by": "gateway"}


def stretch(counts: list[int], *, adc_bits: int | None = 12, seq: int = 0, dropped: int = 0, boot: int = 7) -> StretchChunk:
    payload = {
        **_COMMON,
        "stream_id": "stretch_spine",
        "seq": seq,
        "boot_id": boot,
        "sample_rate_hz": 20,
        "n_samples": len(counts),
        "dropped_samples": dropped,
        "stream_kind": "stretch",
        "units": {"adc_counts": "count"},
        "data": {"adc_counts": counts},
        "quality": {"adc_counts": _GQ},
    }
    if adc_bits is not None:
        payload["circuit"] = {"adc_bits": adc_bits}
    return StretchChunk.model_validate(payload)


def imu(accel_x: list[float], gyro_z: list[float] | None = None, *, accel_range: int | None = 2, gyro_range: int | None = None) -> ImuChunk:
    n = len(accel_x)
    zeros = [0.0] * n
    config = {k: v for k, v in {"accel_range_g": accel_range, "gyro_range_dps": gyro_range}.items() if v is not None}
    payload = {
        **_COMMON,
        "stream_id": "imu_t9",
        "sample_rate_hz": 100,
        "n_samples": n,
        "stream_kind": "imu",
        "units": {"accel_x": "g", "accel_y": "g", "accel_z": "g", "gyro_x": "deg/s", "gyro_y": "deg/s", "gyro_z": "deg/s"},
        "data": {"accel_x": accel_x, "accel_y": zeros, "accel_z": zeros, "gyro_x": zeros, "gyro_y": zeros, "gyro_z": gyro_z or zeros},
        "quality": {"accel": _GQ, "gyro": _GQ},
    }
    if config:
        payload["config"] = config
    return ImuChunk.model_validate(payload)


def emg(n: int) -> EmgChunk:
    return EmgChunk.model_validate(
        {
            **_COMMON,
            "stream_id": "emg_lat_left",
            "sample_rate_hz": 1000,
            "n_samples": n,
            "stream_kind": "emg",
            "signal_kind": "raw",
            "units": {"emg": "mV"},
            "data": {"emg": [0.1] * n},
            "quality": {"emg": _GQ},
            "acquisition": {"adc_bits": 12, "vref_v": 3.3, "gain": 1000},
        }
    )


def segments(*chunks) -> tuple[Segment, ...]:
    return order_chunks(chunks).segments


# --- saturation_counts ---------------------------------------------------------------------------


def test_stretch_counts_at_either_adc_rail_are_saturated():
    assert saturation_counts(stretch([0, 1, 2000, 4094, 4095])) == (2, 5)


def test_stretch_without_adc_bits_cannot_be_judged():
    assert saturation_counts(stretch([0, 4095], adc_bits=None)) is None


def test_imu_samples_at_the_configured_range_are_clipped():
    # Una muestra cuenta una vez aunque sature en varios canales.
    chunk = imu([0.5, 2.0, -2.0, 1.99], gyro_z=[250.0, 250.0, 0.0, 0.0], accel_range=2, gyro_range=250)

    assert saturation_counts(chunk) == (3, 4)


def test_imu_only_judges_channels_with_a_known_range():
    assert saturation_counts(imu([0.0, 0.0], gyro_z=[9999.0, 0.0], accel_range=2, gyro_range=None)) == (0, 2)
    assert saturation_counts(imu([0.0, 5.0], gyro_z=[300.0, 0.0], accel_range=None, gyro_range=250)) == (1, 2)


def test_imu_without_config_cannot_be_judged():
    assert saturation_counts(imu([5.0], accel_range=None, gyro_range=None)) is None


def test_imu_config_without_ranges_cannot_be_judged():
    chunk = imu([5.0], accel_range=None, gyro_range=None).model_copy(update={"config": Config(dlpf_hz=42.0)})

    assert saturation_counts(chunk) is None


def test_emg_saturation_is_never_invented():
    assert saturation_counts(emg(3)) is None


# --- assess_quality ------------------------------------------------------------------------------


def test_complete_clean_stream_scores_one():
    result = assess_quality(segments(stretch([100, 200]), stretch([300, 400], seq=1)))

    assert (result.coverage, result.saturated_fraction, result.score, result.flags) == (1.0, 0.0, 1.0, ())
    quality = result.to_quality()
    assert quality.score == 1.0
    assert quality.flags == []
    assert quality.assessed_by.value == "engine"


def test_gap_lowers_coverage_and_flags_packet_gap():
    # seq 1 perdido: 2 muestras estimadas; 1 muestra dropped en el último chunk. Presentes 4 de 7.
    result = assess_quality(segments(stretch([100, 200]), stretch([300, 400], seq=2, dropped=1)))

    assert result.coverage == pytest.approx(4 / 7)
    assert result.score == pytest.approx(4 / 7)
    assert result.flags == (QualityFlag.PACKET_GAP,)


def test_saturation_multiplies_the_score_and_flags_saturated_adc():
    result = assess_quality(segments(stretch([0, 100, 200, 4095])))

    assert result.saturated_fraction == 0.5
    assert result.score == 0.5
    assert result.flags == (QualityFlag.SATURATED_ADC,)


def test_imu_clipping_flag():
    result = assess_quality(segments(imu([2.0, 0.0])))

    assert result.flags == (QualityFlag.CLIPPING,)
    assert result.score == 0.5


def test_unjudgeable_saturation_is_omitted_not_zero():
    result = assess_quality(segments(emg(10)))

    assert result.saturated_fraction is None
    assert result.score == 1.0


def test_saturation_fraction_only_over_judged_chunks():
    # Un chunk juzgable (2 de 2 saturadas) y otro sin adc_bits: la fracción es 2/2, no 2/4.
    result = assess_quality(segments(stretch([0, 4095]), stretch([10, 20], seq=1, adc_bits=None)))

    assert result.saturated_fraction == 1.0
    assert result.score == 0.0


def test_restart_counts_as_packet_gap():
    result = assess_quality(segments(stretch([1, 2], boot=1), stretch([1, 2], boot=2)))

    assert result.coverage == 1.0
    assert QualityFlag.PACKET_GAP in result.flags


def test_caller_flags_for_calibration_and_clock():
    result = assess_quality(segments(stretch([1, 2])), calibration_expired=True, clock_uncertain=True)

    assert result.flags == (QualityFlag.OUT_OF_CALIBRATION, QualityFlag.CLOCK_UNCERTAIN)
    assert result.score == 1.0


def test_no_segments_means_zero_coverage():
    result = assess_quality(())

    assert (result.coverage, result.score, result.saturated_fraction) == (0.0, 0.0, None)


# --- Propiedades ---------------------------------------------------------------------------------

_stretch_chunks = st.lists(
    st.tuples(st.lists(st.integers(0, 4095), min_size=1, max_size=8), st.integers(0, 6), st.integers(0, 5), st.sampled_from([12, None])),
    min_size=1,
    max_size=10,
)


def build(specs):
    return segments(*(stretch(counts, seq=seq, dropped=d, adc_bits=bits) for counts, seq, d, bits in specs))


@pytest.mark.property
@given(_stretch_chunks, st.booleans(), st.booleans())
def test_score_and_coverage_stay_in_the_unit_interval(specs, expired, uncertain):
    result = assess_quality(build(specs), calibration_expired=expired, clock_uncertain=uncertain)

    assert 0.0 <= result.coverage <= 1.0
    assert 0.0 <= result.score <= result.coverage
    assert result.saturated_fraction is None or 0.0 <= result.saturated_fraction <= 1.0
    result.to_quality()  # el modelo del contrato valida score en [0, 1]


@pytest.mark.property
@given(_stretch_chunks)
def test_flags_are_unique_and_ordered_as_in_the_contract(specs):
    flags = assess_quality(build(specs)).flags
    order = list(QualityFlag)

    assert list(flags) == sorted(set(flags), key=order.index)


@pytest.mark.property
@given(_stretch_chunks)
def test_packet_gap_iff_coverage_below_one_for_a_single_boot(specs):
    result = assess_quality(build(specs))

    assert (QualityFlag.PACKET_GAP in result.flags) == (result.coverage < 1.0)
