"""Orden de chunks, duplicados, huecos de seq, muestras perdidas y reinicios (#40)."""

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import StretchChunk
from spine_engine.features.ordering import Incident, IncidentKind, order_chunks

_BASE = StretchChunk.model_validate(
    {
        "schema_version": "1.0.0",
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "stream_id": "stretch_spine",
        "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01",
        "source": "device",
        "boot_id": 7,
        "seq": 0,
        "t0_device_ms": 0,
        "received_at": "2026-11-13T10:03:05.020Z",
        "sample_rate_hz": 20,
        "n_samples": 4,
        "stream_kind": "stretch",
        "units": {"adc_counts": "count"},
        "data": {"adc_counts": [1850, 1852, 1861, 1870]},
        "quality": {"adc_counts": {"score": 1.0, "flags": [], "assessed_by": "gateway"}},
    }
)


def chunk(seq: int, *, stream: str = "stretch_spine", boot: int = 7, n: int = 4, dropped: int = 0, tag: int = 0, received_s: int = 0) -> StretchChunk:
    # `tag` distingue copias con el mismo (stream, boot, seq) para ver cuál gana.
    return _BASE.model_copy(
        update={
            "seq": seq,
            "stream_id": stream,
            "boot_id": boot,
            "n_samples": n,
            "dropped_samples": dropped,
            "t0_device_ms": seq * 200 + tag,
            "received_at": _BASE.received_at.replace(second=received_s % 60),
        }
    )


def keys(result) -> list[tuple[str, int, int]]:
    return [(c.stream_id, c.boot_id, c.seq) for c in result.chunks()]


def test_sorts_by_stream_then_boot_then_seq():
    chunks = [chunk(1, stream="b"), chunk(0, stream="b", boot=3), chunk(2), chunk(0), chunk(1)]

    result = order_chunks(chunks)

    assert keys(result) == [("b", 3, 0), ("b", 7, 1), ("stretch_spine", 7, 0), ("stretch_spine", 7, 1), ("stretch_spine", 7, 2)]


def test_contiguous_stream_is_one_segment_without_incidents():
    result = order_chunks([chunk(2), chunk(0), chunk(1)])

    assert len(result.segments) == 1
    segment = result.segments[0]
    assert (segment.stream_id, segment.boot_id) == ("stretch_spine", 7)
    assert [c.seq for c in segment.chunks] == [0, 1, 2]
    assert segment.present_samples == 12
    assert segment.expected_samples == 12
    assert result.incidents == ()


def test_duplicate_keeps_first_occurrence_and_is_counted():
    first, second, third = chunk(0, tag=1), chunk(0, tag=2), chunk(0, tag=3)

    result = order_chunks([first, chunk(1), second, third])

    assert result.segments[0].chunks[0] is first
    assert [c.seq for c in result.segments[0].chunks] == [0, 1]
    assert result.incidents == (Incident(IncidentKind.DUPLICATE, "stretch_spine", 7, seq=0, chunks=2, samples=0),)


def test_seq_gap_counts_missing_chunks_and_estimates_samples_from_previous_chunk():
    result = order_chunks([chunk(0, n=5), chunk(3, n=4)])

    assert result.incidents == (Incident(IncidentKind.SEQ_GAP, "stretch_spine", 7, seq=1, chunks=2, samples=10),)
    segment = result.segments[0]
    assert segment.present_samples == 9
    assert segment.missing_samples == 10
    assert segment.expected_samples == 19


def test_a_window_starting_mid_stream_is_not_a_gap():
    result = order_chunks([chunk(40), chunk(41)])

    assert result.incidents == ()
    assert result.segments[0].expected_samples == 8


def test_dropped_samples_are_missing_but_not_present():
    result = order_chunks([chunk(0, n=4, dropped=2), chunk(1)])

    assert result.incidents == (Incident(IncidentKind.DROPPED_SAMPLES, "stretch_spine", 7, seq=0, chunks=0, samples=2),)
    segment = result.segments[0]
    assert segment.present_samples == 8
    assert segment.missing_samples == 2
    assert segment.expected_samples == 10


def test_null_dropped_samples_counts_as_zero():
    result = order_chunks([chunk(0).model_copy(update={"dropped_samples": None})])

    assert result.incidents == ()
    assert result.segments[0].expected_samples == 4


def test_each_boot_is_an_independent_segment_and_the_restart_is_reported():
    # El boot 99 arranca después (received_at) aunque su boot_id sea mayor o menor: manda la llegada.
    old = [chunk(5, boot=99, received_s=1), chunk(6, boot=99, received_s=2)]
    new = [chunk(0, boot=3, received_s=10), chunk(1, boot=3, received_s=11)]

    result = order_chunks(new + old)

    assert [(s.boot_id, [c.seq for c in s.chunks]) for s in result.segments] == [(3, [0, 1]), (99, [5, 6])]
    assert result.incidents == (Incident(IncidentKind.DEVICE_RESTART, "stretch_spine", 3, seq=0, chunks=0, samples=0),)


def test_restarts_are_per_stream():
    result = order_chunks([chunk(0, stream="a", boot=1), chunk(0, stream="b", boot=2)])

    assert [i.kind for i in result.incidents] == []
    assert len(result.segments) == 2


def test_segments_for_filters_by_stream():
    result = order_chunks([chunk(0, stream="a"), chunk(0, stream="b", boot=1), chunk(0, stream="b", boot=2, received_s=5)])

    assert [s.boot_id for s in result.segments_for("b")] == [1, 2]
    assert result.segments_for("zzz") == ()


def test_empty_input_gives_empty_result():
    result = order_chunks([])

    assert result.segments == ()
    assert result.incidents == ()
    assert result.chunks() == ()


# --- Propiedades ---------------------------------------------------------------------------------

_chunk_specs = st.lists(
    st.tuples(st.sampled_from(["aa", "bb"]), st.sampled_from([1, 2]), st.integers(0, 12), st.integers(1, 6), st.integers(0, 3)),
    max_size=30,
)


def build(specs) -> list[StretchChunk]:
    # Mismo (stream, boot, seq) => contenido idéntico, para que «gana el primero» no dependa del orden.
    return [chunk(seq, stream=s, boot=b, n=n, dropped=d, received_s=b) for s, b, seq, n, d in specs]


def canonical(specs):
    seen: dict[tuple[str, int, int], tuple] = {}
    for spec in specs:
        seen.setdefault((spec[0], spec[1], spec[2]), spec)
    return [seen[k] for k in [(s[0], s[1], s[2]) for s in specs]]


@pytest.mark.property
@given(_chunk_specs, st.randoms(use_true_random=False))
def test_result_is_permutation_invariant(specs, rnd: random.Random):
    specs = canonical(specs)
    shuffled = list(specs)
    rnd.shuffle(shuffled)

    assert order_chunks(build(specs)) == order_chunks(build(shuffled))


@pytest.mark.property
@given(_chunk_specs)
def test_output_is_sorted_and_free_of_duplicates(specs):
    result = order_chunks(build(specs))

    assert keys(result) == sorted(set(keys(result)))
    assert set(keys(result)) == {(s, b, seq) for s, b, seq, _, _ in specs}


@pytest.mark.property
@given(_chunk_specs)
def test_ordering_is_idempotent(specs):
    once = order_chunks(build(specs))
    twice = order_chunks(once.chunks())

    assert twice.segments == once.segments
    assert [i for i in twice.incidents if i.kind is not IncidentKind.DUPLICATE] == [i for i in once.incidents if i.kind is not IncidentKind.DUPLICATE]
    assert not any(i.kind is IncidentKind.DUPLICATE for i in twice.incidents)


@pytest.mark.property
@given(_chunk_specs)
def test_duplicates_dropped_equal_input_minus_output(specs):
    chunks = build(specs)
    result = order_chunks(chunks)

    dropped = sum(i.chunks for i in result.incidents if i.kind is IncidentKind.DUPLICATE)
    assert dropped == len(chunks) - len(result.chunks())


@pytest.mark.property
@given(_chunk_specs)
def test_sample_accounting_balances(specs):
    for segment in order_chunks(build(specs)).segments:
        assert segment.expected_samples == segment.present_samples + segment.missing_samples
        assert 0 < segment.present_samples <= segment.expected_samples
        assert len({c.boot_id for c in segment.chunks}) == 1
