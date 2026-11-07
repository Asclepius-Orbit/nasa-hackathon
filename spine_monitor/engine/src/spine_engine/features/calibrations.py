"""Resolución de la calibración de un stream. Nunca lanza: lo no resoluble es ``MISSING``.

Reglas:

- El ``calibration_id`` del chunk manda sobre el de ``session.streams[]``.
- Se busca en ``request.calibrations`` por ``calibration_id``; si hay ids repetidos gana el primero.
- Solo aplica si su ``stream_id`` coincide y, cuando el stream está declarado, su tipo corresponde
  al ``stream_kind`` (stretch -> stretch, emg_mvc -> emg, imu -> imu). Si no: ``no_calibration``.
- ``valid_until`` anterior a ``at`` -> se resuelve pero queda ``EXPIRED`` (``calibration_expired``).
  Igual a ``at`` sigue vigente. Sin ``at`` no se juzga la caducidad.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from spine_contracts import Calibration, FeaturesRequest
from spine_contracts.models.domain.feature_set_schema import Reason

_KIND_OF_CALIBRATION = {"stretch": "stretch", "emg_mvc": "emg", "imu": "imu"}


class CalibrationStatus(StrEnum):
    """Estado de la resolución."""

    RESOLVED = "resolved"
    MISSING = "missing"
    EXPIRED = "expired"


_REASONS = {CalibrationStatus.MISSING: Reason.NO_CALIBRATION, CalibrationStatus.EXPIRED: Reason.CALIBRATION_EXPIRED}


@dataclass(frozen=True, slots=True)
class CalibrationResolution:
    """Calibración elegida para un stream (o su ausencia) y por qué."""

    stream_id: str
    calibration_id: str | None
    calibration: Calibration | None
    status: CalibrationStatus

    @property
    def reason(self) -> Reason | None:
        """Motivo de ``skipped`` del contrato, o ``None`` si está resuelta y vigente."""
        return _REASONS.get(self.status)


def resolve_calibration(
    request: FeaturesRequest,
    stream_id: str,
    *,
    chunk_calibration_id: str | None = None,
    at: datetime | None = None,
) -> CalibrationResolution:
    """Resuelve la calibración de ``stream_id`` para el instante ``at`` (p. ej. fin de la ventana)."""
    declared = next((s for s in request.session.streams if s.stream_id == stream_id), None)
    calibration_id = chunk_calibration_id or (declared.calibration_id if declared else None)

    def missing() -> CalibrationResolution:
        return CalibrationResolution(stream_id, calibration_id, None, CalibrationStatus.MISSING)

    if calibration_id is None:
        return missing()
    found = next((c for c in request.calibrations or () if c.calibration_id == calibration_id), None)
    if found is None or found.stream_id != stream_id:
        return missing()
    if declared is not None and _KIND_OF_CALIBRATION[found.calibration_type] != declared.stream_kind.value:
        return missing()
    expired = at is not None and found.valid_until is not None and found.valid_until < at
    return CalibrationResolution(stream_id, calibration_id, found, CalibrationStatus.EXPIRED if expired else CalibrationStatus.RESOLVED)
