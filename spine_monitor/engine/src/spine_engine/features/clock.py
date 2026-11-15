"""Device clock -> UTC per ``boot_id`` from ``ClockSync`` anchors.

Per-boot model: ``utc = a + b·device_ms``.

- ≥2 anchors with distinct ``device_ms``: least squares (``b`` captures the crystal drift).
- 1 anchor, all with the same ``device_ms`` or fitted slope ≤ 0 (inconsistent anchors): fixed
  offset with ``b = 1`` (mean of ``utc - device_ms``). A clock running backwards is not accepted.
- 0 anchors: the boot has no UTC (``None``); the caller treats it as ``no_data``.

To avoid losing precision with large values, the fit is done in relative coordinates (to the smallest
``device_ms`` and the earliest UTC anchor). Nothing raises exceptions: what cannot be represented is ``None``.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from spine_contracts import ClockSync

_MS = timedelta(milliseconds=1)


@dataclass(frozen=True, slots=True)
class ClockFit:
    """Fit of one ``boot_id``.

    ``offset_ms`` is UTC at ``device_ms = 0`` measured in ms from ``reference``. ``uncertainty_ms`` is the
    worst ``uncertainty_ms`` of the anchors (no more is invented); ``max_residual_ms`` is the largest
    residual of the fit over the anchors themselves.
    """

    boot_id: int
    reference: datetime
    reference_device_ms: int
    intercept_ms: float
    slope: float
    n_anchors: int
    uncertainty_ms: float
    max_residual_ms: float

    @property
    def offset_ms(self) -> float:
        """``a`` of the model, in ms from ``reference``."""
        return self.intercept_ms - self.slope * self.reference_device_ms

    def to_utc(self, device_ms: int) -> datetime | None:
        """UTC of a device clock reading; ``None`` if not representable."""
        try:
            relative = self.intercept_ms + self.slope * float(device_ms - self.reference_device_ms)
            return self.reference + timedelta(milliseconds=relative)
        except OverflowError, ValueError:
            return None


def _fit(boot_id: int, anchors: Sequence[ClockSync]) -> ClockFit:
    reference = min(a.utc for a in anchors)
    reference_device_ms = min(a.device_ms for a in anchors)
    xs = [float(a.device_ms - reference_device_ms) for a in anchors]
    ys = [(a.utc - reference) / _MS for a in anchors]
    n = len(anchors)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx if sxx > 0 else 0.0
    if slope <= 0:
        slope = 1.0
    intercept = mean_y - slope * mean_x
    residual = max(abs(y - (intercept + slope * x)) for x, y in zip(xs, ys, strict=True))
    uncertainty = max(a.uncertainty_ms for a in anchors)
    return ClockFit(boot_id, reference, reference_device_ms, intercept, slope, n, uncertainty, residual)


@dataclass(frozen=True, slots=True)
class DeviceClock:
    """Fits per ``boot_id``. Built with :meth:`from_syncs`."""

    fits: dict[int, ClockFit]

    @classmethod
    def from_syncs(cls, syncs: Iterable[ClockSync] | None) -> DeviceClock:
        """Group the anchors by ``boot_id`` and fit each boot (input order does not matter)."""
        by_boot: dict[int, list[ClockSync]] = {}
        for sync in syncs or ():
            by_boot.setdefault(sync.boot_id, []).append(sync)
        return cls({boot: _fit(boot, anchors) for boot, anchors in by_boot.items()})

    def fit(self, boot_id: int) -> ClockFit | None:
        """Fit of the boot, or ``None`` if it has no anchors."""
        return self.fits.get(boot_id)

    def to_utc(self, boot_id: int, device_ms: int) -> datetime | None:
        """UTC of ``device_ms`` in that boot; ``None`` if there are no anchors or it is not representable."""
        fit = self.fits.get(boot_id)
        return None if fit is None else fit.to_utc(device_ms)

    def uncertainty_ms(self, boot_id: int) -> float | None:
        """Worst ``uncertainty_ms`` of the boot's anchors, or ``None`` if it has no anchors."""
        fit = self.fits.get(boot_id)
        return None if fit is None else fit.uncertainty_ms
