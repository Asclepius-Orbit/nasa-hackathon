"""Recording of the ``stretch`` and ``emg_mvc`` calibrations requested by the ``calibrate`` command.

Pure and deterministic logic: the id (``new_id``) and the instant (``finish(now)``) are injected by the caller. The duration is
measured with the device clock (``t0_ms`` of each batch and the frequency of its kind), not the wall clock: the
recording starts at the first batch of the stream and ends when a batch covers ``duration_s``. Only samples
before that instant count. Batches of other kinds, duplicates or regressions (``frame_gap < 0``), those with an
unexpected ``n`` and those before the start (``millis()`` wrap) are ignored; a lost batch shortens the sample, not the duration.

- ``stretch``: mean of the counts -> ``R0`` with the assembler's divider -> ``rest_resistance_ohm``, with
  ``rest_length_mm`` from the settings and the ``stretch_curve`` curve from the settings (by default ``DEFAULT_STRETCH_CURVE``, the one from the
  example mission), scaled to the measured rest: R0 · R / ``stretch_curve_rest_resistance_ohm`` and L0 · L / ``stretch_curve_rest_length_mm``.
- ``emg_mvc``: RMS over sliding windows of ``emg_mvc_window_ms`` (one-sample step) and ``mvc_rms_mv`` = the maximum.
  The conversion to mV is linear, so the RMS is computed in counts and converted at the end; for a ``raw`` signal the
  assembler's mid-scale offset is subtracted first (``emg_offset_counts``, assumption not verified with hardware),
  never the RMS of absolute counts. ``signal_kind`` comes from ``emg_signal_kind``.

``imu`` is not recorded here (the gateway rejects it in the command).
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
    """``finish`` before covering ``duration_s`` of device time."""
    REST_RESISTANCE_NOT_COMPUTABLE = "rest_resistance_not_computable"
    """Mean of counts at 0 or at the maximum: cord shorted or open circuit."""
    TOO_FEW_SAMPLES = "too_few_samples"
    """Fewer EMG samples than one RMS window."""
    FLAT_SIGNAL = "flat_signal"
    """Maximum RMS 0: the contract requires ``mvc_rms_mv > 0``."""


@dataclass(frozen=True, slots=True)
class CalibrationFailure:
    reason: FailureReason
    detail: str


class CalibrationRecorder:
    """A calibration recording of a stream. ``feed`` each received batch and ``finish`` at the end (once)."""

    def __init__(
        self,
        calibration_type: CalibrationType,
        stream_id: str,
        duration_s: float,
        settings: GatewaySettings,
        new_id: Callable[[], str],
        *,
        session_id: str | None = None,
        reference_posture: str | None = None,
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
        self._reference_posture = reference_posture
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
        """The calibration (``created_at = now``, with timezone) or the reason it could not be computed."""
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
                **({"reference_posture": self._reference_posture} if self._reference_posture is not None else {}),
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
