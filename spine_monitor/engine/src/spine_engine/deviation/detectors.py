"""Detectores de cambio sostenido sobre series de desviaciones (z). No saben qué métrica es."""

from collections.abc import Sequence

from spine_engine.interfaces import ChangeDetector, ChangeSignal, Direction, VotePolicy


def _clipped(series: Sequence[float], clip: float) -> list[float]:
    """Acota cada punto para que un único valor extremo no baste para declarar un cambio."""
    return [max(-clip, min(clip, z)) for z in series]


class CusumDetector:
    """CUSUM bilateral: acumula lo que excede de `k` y avisa al pasar `h`."""

    def __init__(self, k: float = 0.5, h: float = 4.0, clip: float = 3.0, min_points: int = 3) -> None:
        self._k, self._h, self._clip, self._min_points = k, h, clip, min_points

    def detect(self, series: Sequence[float]) -> ChangeSignal:
        if len(series) < self._min_points:
            return ChangeSignal()
        high = low = 0.0
        for z in _clipped(series, self._clip):
            high = max(0.0, high + z - self._k)
            low = max(0.0, low - z - self._k)
        if high > self._h and high >= low:
            return ChangeSignal(sustained=True, direction="up")
        if low > self._h:
            return ChangeSignal(sustained=True, direction="down")
        return ChangeSignal()


class KalmanDetector:
    """Filtro de Kalman de nivel local: sostenido si el nivel estimado se queda fuera de `threshold`.

    El nivel parte de 0 (la baseline) y debe superar el umbral, con el mismo signo, en los últimos
    `persist` pasos.
    """

    def __init__(
        self,
        process_var: float = 0.05,
        measurement_var: float = 1.0,
        threshold: float = 1.0,
        persist: int = 3,
        clip: float = 3.0,
        min_points: int = 3,
    ) -> None:
        self._q, self._r = process_var, measurement_var
        self._threshold, self._persist, self._clip = threshold, persist, clip
        self._min_points = max(min_points, persist)

    def detect(self, series: Sequence[float]) -> ChangeSignal:
        if len(series) < self._min_points:
            return ChangeSignal()
        level, variance = 0.0, 1.0
        levels = []
        for z in _clipped(series, self._clip):
            variance += self._q
            gain = variance / (variance + self._r)
            level += gain * (z - level)
            variance *= 1.0 - gain
            levels.append(level)
        recent = levels[-self._persist :]
        if all(x > self._threshold for x in recent):
            return ChangeSignal(sustained=True, direction="up")
        if all(x < -self._threshold for x in recent):
            return ChangeSignal(sustained=True, direction="down")
        return ChangeSignal()


class AllAgreeVote:
    """AND: sostenido solo si todos los detectores lo ven y en el mismo sentido."""

    def decide(self, signals: Sequence[ChangeSignal]) -> ChangeSignal:
        if not signals or not all(s.sustained for s in signals):
            return ChangeSignal()
        directions: set[Direction | None] = {s.direction for s in signals}
        if len(directions) != 1:
            return ChangeSignal()
        return ChangeSignal(sustained=True, direction=directions.pop())


class CompositeDetector:
    """Ejecuta varios detectores y deja que una `VotePolicy` combine sus veredictos."""

    def __init__(self, detectors: Sequence[ChangeDetector], vote: VotePolicy) -> None:
        self._detectors = list(detectors)
        self._vote = vote

    def detect(self, series: Sequence[float]) -> ChangeSignal:
        return self._vote.decide([d.detect(series) for d in self._detectors])
