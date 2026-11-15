"""Deviation score: how many typical scales a value is away from its baseline."""

from spine_contracts import BaselineEntry

# MAD -> equivalent standard deviation for normal data.
MAD_TO_SIGMA = 1.4826
_ABSOLUTE_FLOOR = 1e-6


class RobustZScorer:
    """Robust z: (value - median) / (1.4826 * MAD).

    If the MAD is ~0 (few sessions, or all equal) it uses the standard deviation; if that is also ~0 it uses
    a floor proportional to the median (`relative_floor`), to avoid dividing by zero or firing an
    infinite z with a single-session baseline.
    """

    def __init__(self, relative_floor: float = 0.05) -> None:
        self._relative_floor = relative_floor

    def score(self, value: float, entry: BaselineEntry) -> float:
        return (value - entry.median) / self._scale(entry)

    def _scale(self, entry: BaselineEntry) -> float:
        for scale in (MAD_TO_SIGMA * entry.mad, entry.std):
            if scale > _ABSOLUTE_FLOOR:
                return scale
        return max(self._relative_floor * abs(entry.median), _ABSOLUTE_FLOOR)
