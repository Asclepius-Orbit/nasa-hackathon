"""Extraction context: what the family extractors receive already resolved (#40).

Gathers, for the declared streams of the session, the ordered chunks (``ordering``), the clock
(``clock``), the calibration of each stream (``calibrations``) and the common quality helper
(``quality``). Chunks of undeclared streams or of another ``session_id`` are ignored here, only once.
Everything is resolved without raising: what is missing is expressed (``stream_missing``, ``MISSING`` calibration,
segments without UTC) so each extractor can translate it into a ``skipped`` with its reason.
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
    """Data prepared for a request; immutable and shared by all extractors."""

    request: FeaturesRequest
    session: Session
    ordered: OrderedChunks
    clock: DeviceClock
    calibrations: Mapping[str, CalibrationResolution]
    missing_streams: frozenset[str]
    window: Window | None
    clock_uncertain_ms: float | None = None

    def stream_missing(self, stream_id: str) -> bool:
        """``True`` if the stream is declared in the session and no usable chunk arrived."""
        return stream_id in self.missing_streams

    def segments(self, stream_id: str) -> tuple[Segment, ...]:
        """Segments of the stream with UTC (``boot_id`` with some clock anchor), by ``boot_id``.

        Boots without an anchor have no UTC: they are not returned and the feature ends up ``no_data``.
        """
        return tuple(s for s in self.ordered.segments_for(stream_id) if self.clock.fit(s.boot_id) is not None)

    def calibration(self, stream_id: str) -> CalibrationResolution:
        """Resolved calibration of the stream (``MISSING`` if there is none or it is not for that stream)."""
        found = self.calibrations.get(stream_id)
        return found if found is not None else resolve_calibration(self.request, stream_id)

    def assess(self, stream_ids: Iterable[str]) -> QualityAssessment:
        """Common quality of the UTC segments of the streams a feature uses."""
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


def _before(chunks: Iterable[StreamChunk], clock: DeviceClock, end: datetime) -> list[StreamChunk]:
    """Exclude samples at or after the requested end, including within a chunk."""
    result: list[StreamChunk] = []
    for chunk in chunks:
        lo, hi = 0, chunk.n_samples
        while lo < hi:
            mid = (lo + hi) // 2
            instant = clock.to_utc(chunk.boot_id, round(chunk.t0_device_ms + mid * 1000 / chunk.sample_rate_hz))
            if instant is not None and instant < end:
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            continue
        if lo == chunk.n_samples:
            result.append(chunk)
            continue
        columns = {}
        for name in type(chunk.data).model_fields:
            value = getattr(chunk.data, name)
            if isinstance(value, list):
                columns[name] = value[:lo]
            elif hasattr(value, "root") and isinstance(value.root, list):
                columns[name] = value.model_copy(update={"root": value.root[:lo]})
        result.append(chunk.model_copy(update={"n_samples": lo, "data": chunk.data.model_copy(update=columns)}))
    return result


def build_context(request: FeaturesRequest, *, clock_uncertain_ms: float | None = None) -> ExtractionContext:
    """Prepares the context: filters by session and declared streams, orders, adjusts the clock and resolves calibrations."""
    session = request.session
    declared = list(dict.fromkeys(s.stream_id for s in session.streams))
    chunks = [c for c in request.chunks if c.session_id == session.session_id and c.stream_id in declared]
    syncs = request.clock_syncs
    if request.requested_window is not None:
        syncs = [sync for sync in syncs or [] if sync.utc <= request.requested_window.end]
    clock = DeviceClock.from_syncs(syncs)
    if request.requested_window is not None:
        chunks = _before(chunks, clock, request.requested_window.end)
    ordered = order_chunks(chunks)
    window = _window(ordered.chunks(), clock)
    calibrations = {
        stream: resolve_calibration(
            request,
            stream,
            chunk_calibration_id=_chunk_calibration_id(c for segment in ordered.segments_for(stream) for c in segment.chunks),
            at=request.requested_window.end if request.requested_window else window.end if window else None,
        )
        for stream in declared
    }
    missing = frozenset(stream for stream in declared if not ordered.segments_for(stream))
    return ExtractionContext(request, session, ordered, clock, calibrations, missing, window, clock_uncertain_ms)
