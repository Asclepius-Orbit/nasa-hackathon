"""Pure, incremental repetition detector over IMU chunks (gyroscope only).

Why the gyroscope: in microgravity the accelerometer reads no gravity vector, so it gives no angle, and the
app is used in flight. The sensor mount is arbitrary, so the movement axis is whichever gyro axis rotated the
most at the start of each rep. There is no wall clock and no I/O but one log line: time comes from `t0_device_ms` alone.

State machine, sample by sample (so that the way samples are split into chunks cannot change the outcome):
resting -> pulling when the smoothed rate stays above `on_dps`; pulling -> letting_up when the rate stays
reversed; letting_up -> resting when the angle is back near 0 with a low rate, or when the rate just stays low
(a sensor fault can leave the integrated angle far from 0 for good). The integrated angle is discarded at the
end of each rep, which bounds the drift to the length of one rep. A reboot or a long gap cuts the rep in progress.

The bias only comes from stillness. With no estimate yet the absolute rate means nothing, so stillness is a low dispersion
over a whole window of `bias_settle_s`, which a constant bias does not change. Until such a window has passed nothing is
counted, so a boot that starts moving loses that rep and no other. A rep longer than `max_rep_s` means the bias went wrong:
the rep is cut and the bias is estimated again, so a bad estimate can never hold the counter for good.
"""

import logging
import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Self, TypedDict

_log = logging.getLogger(__name__)

Phase = Literal["pulling", "letting_up", "resting"]

_GYRO_CHANNELS = ("gyro_x", "gyro_y", "gyro_z")
_ZERO = (0.0, 0.0, 0.0)


class LastRep(TypedDict):
    rep: int
    duration_s: float


class RepSnapshot(TypedDict):
    rep: int  # completed reps plus the one in progress
    phase: Phase
    progress_pct: float | None
    last: LastRep | None


@dataclass(frozen=True, slots=True)
class RepDetectorParams:
    """Detector thresholds. Every field is mandatory; `default()` is the documented set of values."""

    on_dps: float  # smoothed rate that starts a rep
    low_dps: float  # smoothed rate below which the trunk counts as still; also the reversal threshold
    on_hold_s: float  # how long the rate must stay above on_dps
    reversal_hold_s: float  # how long the rate must stay reversed for pulling -> letting_up
    settle_s: float  # how long the end-of-rep condition must hold
    calm_end_s: float  # how long a low rate while letting up ends the rep wherever the angle is
    max_gap_s: float  # a longer gap between two samples of a boot cuts the rep in progress
    return_tolerance_deg: float  # |angle| that counts as back at the start
    smoothing_tau_s: float  # time constant of the rate low-pass
    bias_tau_s: float  # time constant of the at-rest bias estimate
    bias_settle_s: float  # length of the still window that gives the first bias estimate, before which no rep may start
    still_std_dps: float  # dispersion of the raw rate per axis, over that window, under which the trunk counts as still
    max_rep_s: float  # a longer rep is cut and the bias is estimated again

    @classmethod
    def default(cls) -> Self:
        """Values justified by `physiology/configs/example-mission.yaml` (placeholders, not clinical references).

        The simulated rep is a minimum-jerk 0 -> rom_deg (30) -> 0 in rep_duration_s (4), so the rate peaks at
        1.875 * 30 / 2 = 28 dps. At rest there is a bias (up to 0.3 dps), gyro_noise_dps (0.05) and white tremor
        with an observed standard deviation of up to 0.6 dps, about 0.18 dps once smoothed with the tau below.

        - on_dps 6: about a fifth of the peak, and 10 times the raw tremor.
        - low_dps 1.5: 8 standard deviations of the smoothed tremor, about 5 % of the peak.
        - on_hold_s, reversal_hold_s, settle_s 0.1: two smoothing time constants, so that a tremor spike cannot pass.
        - calm_end_s 1: 10 times settle_s, and half of the 2 s half rep, whose return never pauses in the simulator.
        - max_gap_s 2: as long as a half rep, so the phase is unknown after it; live packet loss loses single 0.1 s chunks.
        - return_tolerance_deg 3: 10 % of rom_deg; several times the expected drift of a 4 s integration.
        - smoothing_tau_s 0.05: short against a 2 s half rep; it adds negligible delay.
        - bias_tau_s 2, bias_settle_s 1: well inside rest_between_reps_s (4) and lead_in_s (10).
        - still_std_dps 1.5: over 1 s windows of 16 simulated sessions, at rest it reached 1.12 at most, inside a rep 2.03 at least.
        - max_rep_s 12: 3 times rep_duration_s, so an honest slow rep survives, and a stuck counter costs under 2 cycles of 8 s.
        """
        return cls(
            on_dps=6.0,
            low_dps=1.5,
            on_hold_s=0.1,
            reversal_hold_s=0.1,
            settle_s=0.1,
            calm_end_s=1.0,
            max_gap_s=2.0,
            return_tolerance_deg=3.0,
            smoothing_tau_s=0.05,
            bias_tau_s=2.0,
            bias_settle_s=1.0,
            still_std_dps=1.5,
            max_rep_s=12.0,
        )


class RepDetector:
    """Feed it the chunks of one IMU stream of one session; read the state with `snapshot()`."""

    def __init__(self, params: RepDetectorParams) -> None:
        self._params = params
        self._completed = 0
        self._last: LastRep | None = None
        self._previous_peak: float | None = None
        self._boot: int | None = None
        self._retired_boots: set[int] = set()
        self._non_finite_logged = False
        self._restart_integration()

    def _restart_integration(self) -> None:
        self._last_ms: float | None = None
        self._estimate_bias_again()

    def _estimate_bias_again(self) -> None:
        self._bias: tuple[float, float, float] | None = None
        self._still_window: deque[tuple[float, tuple[float, float, float]]] = deque()
        self._rate = _ZERO
        self._smooth = _ZERO
        self._go_resting()

    def _go_resting(self) -> None:
        self._phase: Phase = "resting"
        self._angle = 0.0
        self._peak = 0.0
        self._axis = 0
        self._direction = 1.0
        self._candidate = _ZERO  # angle per axis since the trunk was last still: it picks the movement axis
        self._start_ms = self._last_ms
        self._since_ms: float | None = None  # start of the condition being held, whichever the phase
        self._calm_since_ms: float | None = None  # start of the low rate while letting up

    def snapshot(self) -> RepSnapshot:
        in_rep = self._phase != "resting"
        progress: float | None = None
        if in_rep and self._previous_peak:
            progress = min(abs(self._angle) / self._previous_peak * 100.0, 100.0)
        return {"rep": self._completed + int(in_rep), "phase": self._phase, "progress_pct": progress, "last": self._last}

    def feed(self, chunk: Mapping[str, Any]) -> bool:
        """Process a chunk and tell whether `snapshot()` changed. Stale chunks and other stream kinds are ignored."""
        if chunk["stream_kind"] != "imu" or chunk["boot_id"] in self._retired_boots:
            return False
        channels: Sequence[Sequence[float]] = [chunk["data"][name] for name in _GYRO_CHANNELS]
        if not all(math.isfinite(value) for channel in channels for value in channel):
            if not self._non_finite_logged:
                self._non_finite_logged = True
                # Only the session: the values are health data.
                _log.warning("session %s: chunks with a non-finite gyro rate are dropped", chunk["session_id"])
            return False
        before = self.snapshot()
        boot: int = chunk["boot_id"]
        if boot != self._boot:
            self._change_boot(boot)
        t0_ms: float = chunk["t0_device_ms"]
        if self._last_ms is not None and t0_ms <= self._last_ms:
            return False
        period_ms = 1000.0 / chunk["sample_rate_hz"]
        for i, (x, y, z) in enumerate(zip(*channels, strict=True)):
            self._step(t0_ms + i * period_ms, (x, y, z))
        return self.snapshot() != before

    def _change_boot(self, boot: int) -> None:
        if self._boot is not None:
            self._retired_boots.add(self._boot)
        self._cut_rep()
        self._boot = boot
        self._restart_integration()

    def _cut_rep(self) -> None:
        """A cut rep stays counted, so that the counter never goes back, but it has no duration or peak."""
        if self._phase != "resting":
            self._completed += 1
        self._go_resting()

    def _step(self, t_ms: float, gyro: tuple[float, float, float]) -> None:
        params = self._params
        previous_ms = self._last_ms
        self._last_ms = t_ms
        if self._bias is None or previous_ms is None:
            self._step_without_bias(t_ms, gyro)
            return
        dt_s = (t_ms - previous_ms) / 1000.0
        rate = (gyro[0] - self._bias[0], gyro[1] - self._bias[1], gyro[2] - self._bias[2])
        if dt_s > params.max_gap_s:
            self._cut_rep()
            self._rate = rate
            self._smooth = rate
            return
        alpha = dt_s / (params.smoothing_tau_s + dt_s)
        self._smooth = (
            self._smooth[0] + alpha * (rate[0] - self._smooth[0]),
            self._smooth[1] + alpha * (rate[1] - self._smooth[1]),
            self._smooth[2] + alpha * (rate[2] - self._smooth[2]),
        )
        # Trapezoids over the real dt: a lost packet is bridged instead of losing its share of the angle.
        increment = (
            (self._rate[0] + rate[0]) * dt_s / 2.0,
            (self._rate[1] + rate[1]) * dt_s / 2.0,
            (self._rate[2] + rate[2]) * dt_s / 2.0,
        )
        self._rate = rate
        if self._phase == "resting":
            self._step_resting(t_ms, dt_s, gyro, increment)
            return
        start_ms = self._start_ms
        assert start_ms is not None  # set while resting, before any rep can start
        if t_ms - start_ms > params.max_rep_s * 1000.0:
            self._cut_rep()
            self._estimate_bias_again()
            return
        self._angle += increment[self._axis]
        self._peak = max(self._peak, abs(self._angle))
        if self._phase == "pulling":
            self._step_pulling(t_ms)
        else:
            self._step_letting_up(t_ms)

    def _step_without_bias(self, t_ms: float, gyro: tuple[float, float, float]) -> None:
        window = self._still_window
        settle_ms = self._params.bias_settle_s * 1000.0
        if window and t_ms - window[-1][0] >= settle_ms:
            window.clear()  # a gap is not rest: too little of the window would have been seen
        window.append((t_ms, gyro))
        while len(window) > 1 and t_ms - window[1][0] >= settle_ms:
            window.popleft()
        if t_ms - window[0][0] < settle_ms:
            return
        means: list[float] = []
        for axis in range(3):
            values = [sample[axis] for _, sample in window]
            mean = sum(values) / len(values)
            if sum((value - mean) ** 2 for value in values) / len(values) >= self._params.still_std_dps**2:
                return
            means.append(mean)
        self._bias = (means[0], means[1], means[2])
        self._start_ms = t_ms

    def _step_resting(self, t_ms: float, dt_s: float, gyro: tuple[float, float, float], increment: tuple[float, float, float]) -> None:
        params = self._params
        strongest = max(abs(self._smooth[0]), abs(self._smooth[1]), abs(self._smooth[2]))
        if strongest < params.low_dps:
            self._update_bias(dt_s, gyro)
            self._candidate = _ZERO
            self._start_ms = t_ms  # the rep starts at the last still sample, before the rate reaches on_dps
            self._since_ms = None
            return
        self._candidate = (self._candidate[0] + increment[0], self._candidate[1] + increment[1], self._candidate[2] + increment[2])
        if strongest <= params.on_dps:
            self._since_ms = None
            return
        if self._since_ms is None:
            self._since_ms = t_ms
        if t_ms - self._since_ms >= params.on_hold_s * 1000.0:
            self._start_rep()

    def _update_bias(self, dt_s: float, gyro: tuple[float, float, float]) -> None:
        bias = self._bias
        assert bias is not None  # checked by the caller of the resting step
        weight = dt_s / self._params.bias_tau_s
        self._bias = (bias[0] + weight * (gyro[0] - bias[0]), bias[1] + weight * (gyro[1] - bias[1]), bias[2] + weight * (gyro[2] - bias[2]))

    def _start_rep(self) -> None:
        magnitudes = [abs(value) for value in self._candidate]
        self._axis = magnitudes.index(max(magnitudes))
        self._angle = self._candidate[self._axis]
        self._peak = abs(self._angle)
        self._direction = 1.0 if self._angle >= 0.0 else -1.0
        self._phase = "pulling"
        self._since_ms = None

    def _step_pulling(self, t_ms: float) -> None:
        if self._smooth[self._axis] * self._direction >= -self._params.low_dps:
            self._since_ms = None
            return
        if self._since_ms is None:
            self._since_ms = t_ms
        if t_ms - self._since_ms >= self._params.reversal_hold_s * 1000.0:
            self._phase = "letting_up"
            self._since_ms = None

    def _step_letting_up(self, t_ms: float) -> None:
        params = self._params
        if abs(self._smooth[self._axis]) >= params.low_dps:
            self._calm_since_ms = None
            self._since_ms = None
            return
        if self._calm_since_ms is None:
            self._calm_since_ms = t_ms
        if t_ms - self._calm_since_ms >= params.calm_end_s * 1000.0:
            self._finish_rep(self._calm_since_ms)
            return
        if abs(self._angle) > params.return_tolerance_deg:
            self._since_ms = None
            return
        if self._since_ms is None:
            self._since_ms = t_ms
        if t_ms - self._since_ms >= params.settle_s * 1000.0:
            self._finish_rep(self._since_ms)

    def _finish_rep(self, end_ms: float) -> None:
        start_ms = self._start_ms
        assert start_ms is not None  # set while resting, before any rep can start
        self._completed += 1
        self._last = {"rep": self._completed, "duration_s": (end_ms - start_ms) / 1000.0}
        self._previous_peak = self._peak
        self._go_resting()
