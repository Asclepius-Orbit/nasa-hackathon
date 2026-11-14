"""Grabación de las calibraciones ``stretch`` y ``emg_mvc`` que pide el comando ``calibrate``.

Lógica pura y determinista: el id (``new_id``) y el instante (``finish(now)``) los inyecta quien llama. La duración se
mide con el reloj del dispositivo (``t0_ms`` de cada lote y la frecuencia de su kind), no con el reloj de pared: la
grabación empieza en el primer lote del stream y termina cuando un lote cubre ``duration_s``. Solo cuentan las muestras
anteriores a ese instante. Se ignoran los lotes de otros kinds, los duplicados o retrocesos (``frame_gap < 0``), los de
``n`` inesperado y los anteriores al inicio (vuelta de ``millis()``); un lote perdido acorta la muestra, no la duración.

- ``stretch``: media de las cuentas -> ``R0`` con el divisor del ensamblador -> ``rest_resistance_ohm``, con
  ``rest_length_mm`` de los ajustes y la curva ``stretch_curve`` de los ajustes (por defecto ``DEFAULT_STRETCH_CURVE``, la de la
  misión de ejemplo), escalada al reposo medido: R0 · R / ``stretch_curve_rest_resistance_ohm`` y L0 · L / ``stretch_curve_rest_length_mm``.
- ``emg_mvc``: RMS en ventanas deslizantes de ``emg_mvc_window_ms`` (paso de una muestra) y ``mvc_rms_mv`` = la máxima.
  La conversión a mV es lineal, así que el RMS se calcula en cuentas y se convierte al final; para una señal ``raw`` se
  resta antes el offset de media escala del ensamblador (``emg_offset_counts``, suposición no verificada con hardware),
  nunca el RMS de cuentas absolutas. ``signal_kind`` sale de ``emg_signal_kind``.

``imu`` no se graba aquí (el gateway lo rechaza en el comando).
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from spine_contracts import SCHEMA_VERSION, EmgMvcCalibration, StretchCalibration
from spine_contracts.models.telemetry.command_schema import CalibrationType

from spine_gateway.assembler import SAMPLE_RATE_HZ, SAMPLES_PER_BATCH, counts_to_mv, counts_to_resistance, emg_offset_counts, scalar_samples
from spine_gateway.protocol import Frame, FrameKind, frame_gap
from spine_gateway.settings import GatewaySettings

_KIND = {CalibrationType.STRETCH: FrameKind.CORD, CalibrationType.EMG_MVC: FrameKind.EMG}


class CalibrationStatus(StrEnum):
    COLLECTING = "collecting"
    DONE = "done"


class FailureReason(StrEnum):
    INCOMPLETE = "incomplete"
    """``finish`` antes de cubrir ``duration_s`` de tiempo de dispositivo."""
    REST_RESISTANCE_NOT_COMPUTABLE = "rest_resistance_not_computable"
    """Media de cuentas en 0 o en el máximo: cordón en corto o circuito abierto."""
    TOO_FEW_SAMPLES = "too_few_samples"
    """Menos muestras EMG que una ventana de RMS."""
    FLAT_SIGNAL = "flat_signal"
    """RMS máximo 0: el contrato exige ``mvc_rms_mv > 0``."""


@dataclass(frozen=True, slots=True)
class CalibrationFailure:
    reason: FailureReason
    detail: str


class CalibrationRecorder:
    """Una grabación de calibración de un stream. ``feed`` cada lote recibido y ``finish`` al acabar (una vez)."""

    def __init__(
        self,
        calibration_type: CalibrationType,
        stream_id: str,
        duration_s: float,
        settings: GatewaySettings,
        new_id: Callable[[], str],
        *,
        session_id: str | None = None,
    ) -> None:
        kind = _KIND.get(calibration_type)
        if kind is None:
            raise ValueError(f"{calibration_type.value} calibration is not recorded by the gateway")
        if not duration_s > 0:
            raise ValueError(f"duration_s must be > 0: {duration_s}")
        self._type = calibration_type
        self._kind = kind
        self._stream_id = stream_id
        self._duration_s = float(duration_s)
        self._settings = settings
        self._new_id = new_id
        self._session_id = session_id
        self._period_ms = 1000 / SAMPLE_RATE_HZ[kind]
        self._start_ms: int | None = None
        self._covered_ms = 0.0
        self._last_frame_seq: int | None = None
        self._counts: list[int] = []
        self._status = CalibrationStatus.COLLECTING

    @property
    def status(self) -> CalibrationStatus:
        return self._status

    def feed(self, frame: Frame) -> CalibrationStatus:
        if self._status is CalibrationStatus.DONE or frame.kind is not self._kind or len(frame.samples) != SAMPLES_PER_BATCH[self._kind]:
            return self._status
        if frame_gap(self._last_frame_seq, frame.frame_seq) < 0:
            return self._status
        self._last_frame_seq = frame.frame_seq
        if self._start_ms is None:
            self._start_ms = frame.t0_ms
        offset = frame.t0_ms - self._start_ms
        if offset < 0:
            return self._status
        duration_ms = self._duration_s * 1000
        samples = scalar_samples(self._kind, frame.samples)
        self._counts.extend(c for i, c in enumerate(samples) if offset + i * self._period_ms < duration_ms)
        self._covered_ms = offset + len(frame.samples) * self._period_ms
        if self._covered_ms >= duration_ms:
            self._status = CalibrationStatus.DONE
        return self._status

    def finish(self, now: datetime) -> StretchCalibration | EmgMvcCalibration | CalibrationFailure:
        """La calibración (``created_at = now``, con zona) o el motivo por el que no se pudo calcular."""
        if self._status is CalibrationStatus.COLLECTING:
            return CalibrationFailure(FailureReason.INCOMPLETE, f"collected {self._covered_ms / 1000} of {self._duration_s} s")
        if self._type is CalibrationType.STRETCH:
            return self._stretch(now)
        return self._emg_mvc(now)

    def _base(self, now: datetime) -> dict[str, Any]:
        s = self._settings
        return {
            "schema_version": SCHEMA_VERSION,
            "calibration_id": self._new_id(),
            "astronaut_id": s.astronaut_id,
            "device_id": s.device_id,
            "stream_id": self._stream_id,
            **({"session_id": self._session_id} if self._session_id is not None else {}),
            "created_at": now,
            "source": "device",
            "calibration_type": self._type.value,
        }

    def _stretch(self, now: datetime) -> StretchCalibration | CalibrationFailure:
        s = self._settings
        mean = sum(self._counts) / len(self._counts)
        r0 = counts_to_resistance(mean, adc_bits=s.adc_bits, series_resistor_ohm=s.stretch_series_resistor_ohm)
        if r0 is None:
            return CalibrationFailure(FailureReason.REST_RESISTANCE_NOT_COMPUTABLE, f"mean {mean} counts is at an ADC rail")
        l0 = s.stretch_rest_length_mm
        return StretchCalibration.model_validate(
            {
                **self._base(now),
                "rest_resistance_ohm": r0,
                "rest_length_mm": l0,
                "curve": {
                    "model": "piecewise_linear",
                    "points": [
                        {"resistance_ohm": r0 * r / s.stretch_curve_rest_resistance_ohm, "length_mm": l0 * length / s.stretch_curve_rest_length_mm}
                        for r, length in s.stretch_curve
                    ],
                },
            }
        )

    def _emg_mvc(self, now: datetime) -> EmgMvcCalibration | CalibrationFailure:
        s = self._settings
        window = max(1, round(s.emg_mvc_window_ms / self._period_ms))
        counts = self._counts
        if len(counts) < window:
            return CalibrationFailure(FailureReason.TOO_FEW_SAMPLES, f"{len(counts)} samples, the {s.emg_mvc_window_ms} ms window needs {window}")
        offset = emg_offset_counts(s)
        squares = [(c - offset) ** 2 for c in counts]
        acc = sum(squares[:window])
        best = acc
        for i in range(window, len(squares)):
            acc += squares[i] - squares[i - window]
            best = max(best, acc)
        mvc = counts_to_mv(math.sqrt(best / window), adc_bits=s.adc_bits, vref_v=s.vref_v, gain=s.emg_gain)
        if mvc == 0:
            return CalibrationFailure(FailureReason.FLAT_SIGNAL, "the maximum RMS is 0 mV")
        return EmgMvcCalibration.model_validate(
            {
                **self._base(now),
                "muscle": s.emg_muscle,
                "side": s.emg_side,
                "mvc_rms_mv": mvc,
                "window_ms": s.emg_mvc_window_ms,
                "signal_kind": s.emg_signal_kind.value,
            }
        )
