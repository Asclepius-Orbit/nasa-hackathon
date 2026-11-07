"""Orden de chunks por (stream, boot_id, seq), duplicados, huecos de seq, muestras perdidas y reinicios.

Funciones puras: no filtran por sesión ni por streams declarados (eso lo hace quien llama). Cada
``boot_id`` es un segmento independiente; nunca se concatenan muestras de dos arranques distintos.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from spine_contracts import StreamChunk

type ChunkKey = tuple[str, int, int]


class IncidentKind(StrEnum):
    """Tipo de incidencia detectada al ordenar."""

    DUPLICATE = "duplicate"
    SEQ_GAP = "seq_gap"
    DROPPED_SAMPLES = "dropped_samples"
    DEVICE_RESTART = "device_restart"


@dataclass(frozen=True, slots=True)
class Incident:
    """Incidencia de un stream dentro de un ``boot_id``.

    - ``DUPLICATE``: ``seq`` repetido; ``chunks`` = copias descartadas (gana la primera).
    - ``SEQ_GAP``: ``seq`` = primer seq ausente; ``chunks`` = chunks ausentes; ``samples`` = muestras
      estimadas con el ``n_samples`` del chunk anterior al hueco.
    - ``DROPPED_SAMPLES``: ``seq`` del chunk; ``samples`` = ``dropped_samples`` declarado por el dispositivo.
    - ``DEVICE_RESTART``: ``boot_id`` = arranque nuevo; ``seq`` = su primer seq recibido.
    """

    kind: IncidentKind
    stream_id: str
    boot_id: int
    seq: int
    chunks: int
    samples: int


@dataclass(frozen=True, slots=True)
class Segment:
    """Chunks únicos de un stream en un ``boot_id``, ordenados por ``seq``.

    ``present_samples`` son las muestras recibidas; ``missing_samples`` suma las perdidas dentro de
    chunks (``dropped_samples``) y las estimadas en huecos de seq. Un hueco antes del primer seq
    recibido no cuenta: la ventana puede empezar a mitad del stream.
    """

    stream_id: str
    boot_id: int
    chunks: tuple[StreamChunk, ...]
    present_samples: int
    missing_samples: int

    @property
    def expected_samples(self) -> int:
        """Muestras que debería haber: presentes + perdidas."""
        return self.present_samples + self.missing_samples


@dataclass(frozen=True, slots=True)
class OrderedChunks:
    """Resultado de :func:`order_chunks`: segmentos ordenados por (stream, boot_id) e incidencias."""

    segments: tuple[Segment, ...]
    incidents: tuple[Incident, ...]

    def chunks(self) -> tuple[StreamChunk, ...]:
        """Todos los chunks únicos, en orden (stream, boot_id, seq)."""
        return tuple(c for segment in self.segments for c in segment.chunks)

    def segments_for(self, stream_id: str) -> tuple[Segment, ...]:
        """Segmentos de un stream, ordenados por ``boot_id``."""
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
    """Ordena, deduplica (gana la primera aparición) y segmenta por (stream, boot_id).

    Los reinicios se detectan por stream: cada ``boot_id`` que no es el primero en llegar (por el
    ``received_at`` de su primer chunk; empate por ``boot_id``) genera un ``DEVICE_RESTART``.
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
