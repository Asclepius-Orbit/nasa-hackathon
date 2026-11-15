"""Detectors of sustained change over series of deviations (z). They do not know which metric it is."""

from collections.abc import Sequence

from spine_engine.interfaces import ChangeDetector, ChangeSignal, Direction, VotePolicy


def _clipped(series: Sequence[float], clip: float) -> list[float]:
    """Clip each point so that a single extreme value is not enough to declare a change."""
    return [max(-clip, min(clip, z)) for z in series]


class CusumDetector:
    """Two-sided CUSUM: accumulates what exceeds `k` and signals when `h` is crossed."""

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
    """Local-level Kalman filter: sustained if the estimated level stays outside `threshold`.

    The level starts at 0 (the baseline) and must exceed the threshold, with the same sign, in the last
    `persist` steps.
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
    """AND: sustained only if all detectors see it and in the same direction."""

    def decide(self, signals: Sequence[ChangeSignal]) -> ChangeSignal:
        if not signals or not all(s.sustained for s in signals):
            return ChangeSignal()
        directions: set[Direction | None] = {s.direction for s in signals}
        if len(directions) != 1:
            return ChangeSignal()
        return ChangeSignal(sustained=True, direction=directions.pop())


class CompositeDetector:
    """Run several detectors and let a `VotePolicy` combine their verdicts."""

    def __init__(self, detectors: Sequence[ChangeDetector], vote: VotePolicy) -> None:
        self._detectors = list(detectors)
        self._vote = vote

    def detect(self, series: Sequence[float]) -> ChangeSignal:
        return self._vote.decide([d.detect(series) for d in self._detectors])
