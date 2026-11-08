"""Ventanas de repetición (#41): de los eventos del protocolo o, sin ellos, de un detector IMU inyectable.

Los eventos ``rep_start``/``rep_end`` de la sesión mandan; el detector IMU (otro módulo) solo es el respaldo.
Nada aquí lanza por datos incoherentes: lo que no cuadra se descarta.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from spine_contracts.models.domain.session_schema import Event, EventType

from spine_engine.features.context import ExtractionContext

type ImuDetector = Callable[[ExtractionContext], tuple["RepWindow", ...]]


@dataclass(frozen=True, slots=True)
class RepWindow:
    """Una repetición: fase activa (``start``→``end``) y reposo posterior si se conoce."""

    index: int
    start: datetime
    end: datetime
    rest_start: datetime | None
    rest_end: datetime | None

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def active(self) -> tuple[datetime, datetime]:
        return (self.start, self.end)

    @property
    def rest(self) -> tuple[datetime, datetime] | None:
        if self.rest_start is None or self.rest_end is None:
            return None
        return (self.rest_start, self.rest_end)


@dataclass(frozen=True, slots=True)
class RepWindows:
    """Ventanas en orden temporal y de dónde salen."""

    windows: tuple[RepWindow, ...]
    source: Literal["events", "imu", "none"]

    def __len__(self) -> int:
        return len(self.windows)

    @property
    def count(self) -> int:
        return len(self.windows)


def _sort_key(event: Event) -> tuple[datetime, int, bool]:
    # el tiempo manda; rep_index solo desempata y, en empate total, el inicio va antes que el fin
    return (event.timestamp, event.rep_index or 0, event.event_type is EventType.REP_END)


def detect_from_events(events: Iterable[Event]) -> tuple[RepWindow, ...]:
    """Empareja cada ``rep_start`` con el siguiente ``rep_end``; descarta huérfanos, duplicados y fin <= inicio."""
    ordered = sorted(events, key=_sort_key)
    pairs: list[tuple[datetime, datetime]] = []
    open_start: datetime | None = None
    for event in ordered:
        if event.event_type is EventType.REP_START:
            if open_start is None:  # un segundo inicio seguido es un duplicado
                open_start = event.timestamp
        elif event.event_type is EventType.REP_END and open_start is not None:
            if event.timestamp > open_start:
                pairs.append((open_start, event.timestamp))
            open_start = None
    last_rest_end = _rest_end_after(ordered, pairs[-1][1]) if pairs else None
    return tuple(
        RepWindow(
            index=i,
            start=start,
            end=end,
            rest_start=end if (i < len(pairs) or last_rest_end is not None) else None,
            rest_end=pairs[i][0] if i < len(pairs) else last_rest_end,
        )
        for i, (start, end) in enumerate(pairs, start=1)
    )


def _rest_end_after(ordered: list[Event], moment: datetime) -> datetime | None:
    return next((e.timestamp for e in ordered if e.event_type is EventType.REST_END and e.timestamp > moment), None)


class RepetitionService:
    """Resuelve las ventanas de una petición: eventos, luego detector IMU, luego nada; memoizado por contexto."""

    def __init__(self, imu_detector: ImuDetector | None = None) -> None:
        self._imu_detector = imu_detector
        # referencia fuerte al contexto: evita que un id() liberado se reutilice con otro contexto
        self._last: tuple[ExtractionContext, RepWindows] | None = None

    def windows(self, context: ExtractionContext) -> RepWindows:
        if self._last is not None and self._last[0] is context:
            return self._last[1]
        result = self._resolve(context)
        self._last = (context, result)
        return result

    def _resolve(self, context: ExtractionContext) -> RepWindows:
        from_events = detect_from_events(context.session.events or ())
        if from_events:
            return RepWindows(from_events, "events")
        if self._imu_detector is not None:
            from_imu = self._imu_detector(context)
            if from_imu:
                return RepWindows(from_imu, "imu")
        return RepWindows((), "none")
