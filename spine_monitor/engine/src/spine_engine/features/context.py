"""Contexto de extracción: lo que los extractores de familia reciben ya resuelto (#40).

Reúne, para los streams declarados de la sesión, los chunks ordenados (``ordering``), el reloj
(``clock``), la calibración de cada stream (``calibrations``) y el ayudante de calidad común
(``quality``). Los chunks de streams no declarados o de otra ``session_id`` se ignoran aquí, una sola vez.
Todo se resuelve sin lanzar: lo que falta se expresa (``stream_missing``, calibración ``MISSING``,
segmentos sin UTC) para que cada extractor lo traduzca a un ``skipped`` con su motivo.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from spine_contracts import FeaturesRequest, StreamChunk, StretchChunk
from spine_contracts.models.domain.feature_set_schema import Window
from spine_contracts.models.domain.session_schema import Session

from spine_engine.features.calibrations import CalibrationResolution, CalibrationStatus, resolve_calibration
from spine_engine.features.clock import DeviceClock
from spine_engine.features.ordering import OrderedChunks, Segment, order_chunks
from spine_engine.features.quality import QualityAssessment, assess_quality


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    """Datos preparados de una petición; inmutable y compartido por todos los extractores."""

    request: FeaturesRequest
    session: Session
    ordered: OrderedChunks
    clock: DeviceClock
    calibrations: Mapping[str, CalibrationResolution]
    missing_streams: frozenset[str]
    window: Window | None
    clock_uncertain_ms: float | None = None

    def stream_missing(self, stream_id: str) -> bool:
        """``True`` si el stream está declarado en la sesión y no llegó ningún chunk utilizable."""
        return stream_id in self.missing_streams

    def segments(self, stream_id: str) -> tuple[Segment, ...]:
        """Segmentos del stream con UTC (``boot_id`` con alguna ancla de reloj), por ``boot_id``.

        Los arranques sin ancla no tienen UTC: no se devuelven y la feature queda ``no_data``.
        """
        return tuple(s for s in self.ordered.segments_for(stream_id) if self.clock.fit(s.boot_id) is not None)

    def calibration(self, stream_id: str) -> CalibrationResolution:
        """Calibración resuelta del stream (``MISSING`` si no hay o no es de ese stream)."""
        found = self.calibrations.get(stream_id)
        return found if found is not None else resolve_calibration(self.request, stream_id)

    def assess(self, stream_ids: Iterable[str]) -> QualityAssessment:
        """Calidad común de los segmentos con UTC de los streams que usa una feature."""
        streams = tuple(dict.fromkeys(stream_ids))
        segments = tuple(s for stream in streams for s in self.segments(stream))
        expired = any(self.calibration(stream).status is CalibrationStatus.EXPIRED for stream in streams)
        return assess_quality(segments, calibration_expired=expired, clock_uncertain=self._clock_uncertain(segments))

    def _clock_uncertain(self, segments: Iterable[Segment]) -> bool:
        if self.clock_uncertain_ms is None:
            return False
        limit = self.clock_uncertain_ms
        return any((u := self.clock.uncertainty_ms(s.boot_id)) is not None and u > limit for s in segments)


def _window(chunks: Iterable[StreamChunk], clock: DeviceClock) -> Window | None:
    starts: list[datetime] = []
    ends: list[datetime] = []
    for chunk in chunks:
        start = clock.to_utc(chunk.boot_id, chunk.t0_device_ms)
        if start is None:
            continue
        starts.append(start)
        try:
            ends.append(start + timedelta(seconds=chunk.n_samples / chunk.sample_rate_hz))
        except OverflowError:
            ends.append(start)
    if not starts:
        return None
    return Window(start=min(starts), end=max(ends))


def _chunk_calibration_id(chunks: Iterable[StreamChunk]) -> str | None:
    return next((c.calibration_id for c in chunks if isinstance(c, StretchChunk) and c.calibration_id), None)


def build_context(request: FeaturesRequest, *, clock_uncertain_ms: float | None = None) -> ExtractionContext:
    """Prepara el contexto: filtra por sesión y streams declarados, ordena, ajusta el reloj y resuelve calibraciones."""
    session = request.session
    declared = list(dict.fromkeys(s.stream_id for s in session.streams))
    chunks = [c for c in request.chunks if c.session_id == session.session_id and c.stream_id in declared]
    ordered = order_chunks(chunks)
    clock = DeviceClock.from_syncs(request.clock_syncs)
    window = _window(ordered.chunks(), clock)
    calibrations = {
        stream: resolve_calibration(
            request,
            stream,
            chunk_calibration_id=_chunk_calibration_id(c for segment in ordered.segments_for(stream) for c in segment.chunks),
            at=window.end if window else None,
        )
        for stream in declared
    }
    missing = frozenset(stream for stream in declared if not ordered.segments_for(stream))
    return ExtractionContext(request, session, ordered, clock, calibrations, missing, window, clock_uncertain_ms)
