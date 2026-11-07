"""Reloj del dispositivo -> UTC por ``boot_id`` a partir de anclas ``ClockSync``.

Modelo por arranque: ``utc = a + b·device_ms``.

- ≥2 anclas con ``device_ms`` distintos: mínimos cuadrados (``b`` captura la deriva del cristal).
- 1 ancla, todas con el mismo ``device_ms`` o pendiente ajustada ≤ 0 (anclas incoherentes): offset
  fijo con ``b = 1`` (media de ``utc - device_ms``). Un reloj que va hacia atrás no se acepta.
- 0 anclas: el arranque no tiene UTC (``None``); quien llama lo trata como ``no_data``.

Para no perder precisión con valores grandes, el ajuste se hace en coordenadas relativas (al menor
``device_ms`` y a la ancla UTC más temprana). Nada lanza excepciones: lo no representable es ``None``.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from spine_contracts import ClockSync

_MS = timedelta(milliseconds=1)


@dataclass(frozen=True, slots=True)
class ClockFit:
    """Ajuste de un ``boot_id``.

    ``offset_ms`` es UTC en ``device_ms = 0`` medido en ms desde ``reference``. ``uncertainty_ms`` es la
    peor ``uncertainty_ms`` de las anclas (no se inventa más); ``max_residual_ms`` es el mayor
    residuo del ajuste sobre las propias anclas.
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
        """``a`` del modelo, en ms desde ``reference``."""
        return self.intercept_ms - self.slope * self.reference_device_ms

    def to_utc(self, device_ms: int) -> datetime | None:
        """UTC de una lectura del reloj del dispositivo; ``None`` si no es representable."""
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
    """Ajustes por ``boot_id``. Se construye con :meth:`from_syncs`."""

    fits: dict[int, ClockFit]

    @classmethod
    def from_syncs(cls, syncs: Iterable[ClockSync] | None) -> DeviceClock:
        """Agrupa las anclas por ``boot_id`` y ajusta cada arranque (el orden de entrada no importa)."""
        by_boot: dict[int, list[ClockSync]] = {}
        for sync in syncs or ():
            by_boot.setdefault(sync.boot_id, []).append(sync)
        return cls({boot: _fit(boot, anchors) for boot, anchors in by_boot.items()})

    def fit(self, boot_id: int) -> ClockFit | None:
        """Ajuste del arranque, o ``None`` si no tiene anclas."""
        return self.fits.get(boot_id)

    def to_utc(self, boot_id: int, device_ms: int) -> datetime | None:
        """UTC de ``device_ms`` en ese arranque; ``None`` si no hay anclas o no es representable."""
        fit = self.fits.get(boot_id)
        return None if fit is None else fit.to_utc(device_ms)

    def uncertainty_ms(self, boot_id: int) -> float | None:
        """Peor ``uncertainty_ms`` de las anclas del arranque, o ``None`` si no tiene anclas."""
        fit = self.fits.get(boot_id)
        return None if fit is None else fit.uncertainty_ms
