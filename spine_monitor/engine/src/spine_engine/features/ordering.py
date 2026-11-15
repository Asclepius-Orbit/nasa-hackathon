"""Chunk ordering by (stream, boot_id, seq), duplicates, seq gaps, lost samples and restarts.

Pure functions: they do not filter by session or by declared streams (the caller does that). Each
``boot_id`` is an independent segment; samples of two different boots are never concatenated.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from spine_contracts import StreamChunk

type ChunkKey = tuple[str, int, int]


class IncidentKind(StrEnum):
    """Kind of incident detected while ordering."""

    DUPLICATE = "duplicate"
    SEQ_GAP = "seq_gap"
    DROPPED_SAMPLES = "dropped_samples"
    DEVICE_RESTART = "device_restart"


@dataclass(frozen=True, slots=True)
class Incident:
    """Incident of a stream within a ``boot_id``.

    - ``DUPLICATE``: repeated ``seq``; ``chunks`` = discarded copies (the first wins).
    - ``SEQ_GAP``: ``seq`` = first missing seq; ``chunks`` = missing chunks; ``samples`` = samples
      estimated with the ``n_samples`` of the chunk before the gap.
    - ``DROPPED_SAMPLES``: ``seq`` of the chunk; ``samples`` = ``dropped_samples`` declared by the device.
    - ``DEVICE_RESTART``: ``boot_id`` = new boot; ``seq`` = its first received seq.
    """

    kind: IncidentKind
    stream_id: str
    boot_id: int
    seq: int
    chunks: int
    samples: int


@dataclass(frozen=True, slots=True)
class Segment:
    """Unique chunks of a stream in a ``boot_id``, ordered by ``seq``.

    ``present_samples`` are the received samples; ``missing_samples`` adds those lost inside
    chunks (``dropped_samples``) and those estimated in seq gaps. A gap before the first received
    seq does not count: the window may start mid-stream.
    """

    stream_id: str
    boot_id: int
    chunks: tuple[StreamChunk, ...]
    present_samples: int
    missing_samples: int

    @property
    def expected_samples(self) -> int:
        """Samples there should be: present + missing."""
        return self.present_samples + self.missing_samples


@dataclass(frozen=True, slots=True)
class OrderedChunks:
    """Result of :func:`order_chunks`: segments ordered by (stream, boot_id) and incidents."""

    segments: tuple[Segment, ...]
    incidents: tuple[Incident, ...]

    def chunks(self) -> tuple[StreamChunk, ...]:
        """All the unique chunks, in order (stream, boot_id, seq)."""
        return tuple(c for segment in self.segments for c in segment.chunks)

    def segments_for(self, stream_id: str) -> tuple[Segment, ...]:
        """Segments of a stream, ordered by ``boot_id``."""
        return tuple(s for s in self.segments if s.stream_id == stream_id)


def _key(chunk: StreamChunk) -> ChunkKey:
    return (chunk.stream_id, chunk.boot_id, chunk.seq)


def _dropped(chunk: StreamChunk) -> int:
    return chunk.dropped_samples or 0


def _segment(stream_id: str, boot_id: int, chunks: list[StreamChunk], incidents: list[Incident]) -> Segment:
    present = 0
    missing = 0
    previous: StreamChunk | None = None
    for chunk in chunks:
        if previous is not None and chunk.seq > previous.seq + 1:
            lost = chunk.seq - previous.seq - 1
            samples = lost * previous.n_samples
            incidents.append(Incident(IncidentKind.SEQ_GAP, stream_id, boot_id, seq=previous.seq + 1, chunks=lost, samples=samples))
            missing += samples
        dropped = _dropped(chunk)
        if dropped:
            incidents.append(Incident(IncidentKind.DROPPED_SAMPLES, stream_id, boot_id, seq=chunk.seq, chunks=0, samples=dropped))
            missing += dropped
        present += chunk.n_samples
        previous = chunk
    return Segment(stream_id, boot_id, tuple(chunks), present, missing)


def order_chunks(chunks: Iterable[StreamChunk]) -> OrderedChunks:
    """Sort, deduplicate (the first occurrence wins) and segment by (stream, boot_id).

    Restarts are detected per stream: each ``boot_id`` that is not the first to arrive (by the
    ``received_at`` of its first chunk; ties broken by ``boot_id``) generates a ``DEVICE_RESTART``.
    """
    unique: dict[ChunkKey, StreamChunk] = {}
    duplicates: dict[ChunkKey, int] = {}
    for chunk in chunks:
        key = _key(chunk)
        if key in unique:
            duplicates[key] = duplicates.get(key, 0) + 1
        else:
            unique[key] = chunk

    incidents = [Incident(IncidentKind.DUPLICATE, s, b, seq=q, chunks=n, samples=0) for (s, b, q), n in sorted(duplicates.items())]

    groups: dict[tuple[str, int], list[StreamChunk]] = {}
    for key in sorted(unique):
        groups.setdefault((key[0], key[1]), []).append(unique[key])

    segments = tuple(_segment(stream_id, boot_id, group, incidents) for (stream_id, boot_id), group in groups.items())

    by_stream: dict[str, list[Segment]] = {}
    for segment in segments:
        by_stream.setdefault(segment.stream_id, []).append(segment)
    for stream_segments in by_stream.values():
        arrivals = sorted(stream_segments, key=lambda s: (min(c.received_at for c in s.chunks), s.boot_id))
        for later in arrivals[1:]:
            incidents.append(Incident(IncidentKind.DEVICE_RESTART, later.stream_id, later.boot_id, seq=later.chunks[0].seq, chunks=0, samples=0))

    return OrderedChunks(segments, tuple(incidents))
