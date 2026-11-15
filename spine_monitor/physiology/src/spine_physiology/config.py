"""Mission configuration of the simulator: YAML validated with Pydantic (`load_config`).

Package rule: **no numeric parameter (physiological or technical) has a default value in the
code**. Every numeric field is required and comes from the YAML; those the contract allows omitting are
written explicitly as `null`. Only optional descriptive fields (placement details,
free text, optional profile sections) may be missing, and then they are not emitted.
The real clinical values will be set by the clinical owner; `configs/example-mission.yaml` is a PLACEHOLDER.

Conventions that fix the semantics of the parameters (the implementations honour them):
- Mission day: the day index `d` (0 = first simulated day) corresponds to the date
  `start_date + d` and to `mission_day = start_mission_day + d`. The `*_utc` times are UTC time of day.
- Daily latents: `spine_elongation` in mm over the preflight rest length of the instrumented
  segment; `muscle_deconditioning`, `rom_motor_control` and `pain_stiffness` are dimensionless
  indices (0 = preflight condition, 1 = maximum modelled deterioration).
  Value of the day = astronaut multiplier × curve(mission_day) + dose drift + N(0, daily_sd).
- Dose drift: the curves are the typical trajectory *with* the planned exercise. Each day
  the accumulated drift adds `dose_drift_per_day × (1 − actual minutes / planned minutes)`.
- Quality faults: `amplitude` is relative to the clean channel signal in the chunk (RMS, or peak
  for `clipping`), so it is dimensionless for all stream types.
"""

from datetime import date, time
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    model_validator,
)
from spine_contracts import (
    AnatomicalPlane,
    GravityEnvironment,
    MissionPhase,
    Muscle,
    QualityFlag,
    Side,
    SpineLevel,
    SpineRegion,
    StreamKind,
    topics,
)
from spine_contracts.models.common_schema import GroundLocation
from spine_contracts.models.domain.astronaut_profile_schema import (
    Anthropometry,
    Fitness,
    SexAtBirth,
    SpaceHistory,
    SpineHistory,
)
from spine_contracts.models.domain.calibration_schema import BodyAxis
from spine_contracts.models.domain.self_report_schema import RedFlag

# --- Base types -------------------------------------------------------------------------------------
# Strict: a YAML with "1e-3" (PyYAML reads it as text) or with `true` where a number goes fails instead
# of silently converting. Write exponents as 1.0e-3. Integers are valid where a float goes.

Float = StrictFloat
PositiveFloat = Annotated[StrictFloat, Field(gt=0)]
NonNegFloat = Annotated[StrictFloat, Field(ge=0)]
UnitInterval = Annotated[StrictFloat, Field(ge=0, le=1)]
Probability = UnitInterval
Percent = Annotated[StrictFloat, Field(ge=0, le=100)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonNegInt = Annotated[StrictInt, Field(ge=0)]
Nrs = Annotated[StrictInt, Field(ge=0, le=10)]

AstronautId = Annotated[str, Field(pattern=f"^{topics.ASTRONAUT_ID.pattern}$")]
DeviceId = Annotated[str, Field(pattern=f"^{topics.DEVICE_ID.pattern}$")]
StreamId = Annotated[str, Field(pattern=f"^{topics.STREAM_ID.pattern}$")]
ProtocolId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_]{1,63}$")]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_]{1,31}$")]


def _time_of_day(value: Any) -> Any:
    # Only "HH:MM" or "HH:MM:SS" text (quoted in the YAML): YAML 1.1 may read 07:00 as a
    # sexagesimal integer and a number here is almost always a typing mistake.
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        raise ValueError("time of day must be a quoted 'HH:MM' or 'HH:MM:SS' string")
    return time.fromisoformat(value)


TimeOfDay = Annotated[time, BeforeValidator(_time_of_day)]

LATENTS = ("spine_elongation", "muscle_deconditioning", "rom_motor_control", "pain_stiffness")
LatentName = Literal["spine_elongation", "muscle_deconditioning", "rom_motor_control", "pain_stiffness"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FloatRange(_Model):
    """Closed interval [min, max] for uniform sampling."""

    min: Float
    max: Float

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min > self.max:
            raise ValueError(f"min ({self.min}) must be <= max ({self.max})")
        return self


class NonNegRange(FloatRange):
    min: NonNegFloat
    max: NonNegFloat


class IntRange(_Model):
    min: StrictInt
    max: StrictInt

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min > self.max:
            raise ValueError(f"min ({self.min}) must be <= max ({self.max})")
        return self


class Normal(_Model):
    """Normal distribution N(mean, sd)."""

    mean: Float
    sd: NonNegFloat


# --- Mission ----------------------------------------------------------------------------------------


class Segment(_Model):
    """Mission segment: `days` consecutive days with the same phase and gravity."""

    days: PositiveInt
    phase: MissionPhase
    gravity_environment: GravityEnvironment


# --- Device -----------------------------------------------------------------------------------------


class Span(_Model):
    from_level: SpineLevel
    to_level: SpineLevel


class Ground(_Model):
    """Ground electrode (GND) of the EMG."""

    location: GroundLocation
    side: Side | None = None


class Placement(_Model):
    """Sensor placement (the contract's). EMG: muscle + side (+ electrode spacing, vertebral
    span and ground); IMU: spine_level; cord: spine_region (+ span)."""

    muscle: Muscle | None = None
    side: Side | None = None
    spine_level: SpineLevel | None = None
    spine_region: SpineRegion | None = None
    electrode_spacing_cm: PositiveFloat | None = None
    ground: Ground | None = None
    span: Span | None = None
    description: Annotated[str, Field(max_length=200)] | None = None


class StreamConfig(_Model):
    """Device stream. The sampling rate is that of its type (`signals.<kind>.sample_rate_hz`)."""

    stream_id: StreamId
    stream_kind: StreamKind
    placement: Placement

    @model_validator(mode="after")
    def _placement_matches_kind(self) -> Self:
        p = self.placement
        missing = {
            StreamKind.EMG: p.muscle is None or p.side is None,
            StreamKind.IMU: p.spine_level is None,
            StreamKind.STRETCH: p.spine_region is None,
        }[self.stream_kind]
        if missing:
            needs = {StreamKind.EMG: "muscle and side", StreamKind.IMU: "spine_level", StreamKind.STRETCH: "spine_region"}
            raise ValueError(f"{self.stream_kind.value} stream {self.stream_id} needs placement {needs[self.stream_kind]}")
        return self


class BatteryConfig(_Model):
    """Battery: overnight charge; each day starts at `start_pct` when the device is powered on."""

    start_pct: Percent
    drain_pct_per_hour_streaming: NonNegFloat
    drain_pct_per_hour_idle: NonNegFloat
    # Below this level the DeviceStatus becomes `degraded`.
    degraded_below_pct: Percent

    @model_validator(mode="after")
    def _drain(self) -> Self:
        if self.drain_pct_per_hour_streaming < self.drain_pct_per_hour_idle:
            raise ValueError("drain_pct_per_hour_streaming must be >= drain_pct_per_hour_idle")
        return self


class DeviceConfig(_Model):
    """Simulated wearable (one per astronaut, same model and streams for all)."""

    firmware_version: Annotated[str, Field(min_length=1, max_length=32)]
    link_type: Literal["ble", "usb_serial", "simulated"]
    # RSSI uniform in [min, max] dBm (integers <= 0); null if the link has none.
    rssi_dbm: IntRange | None
    # Daily power-on -> power-off window. Each power-on is a boot (new boot_id, seq and clock at 0).
    power_on_time_utc: TimeOfDay
    power_off_time_utc: TimeOfDay
    battery: BatteryConfig
    temperature_c: Normal
    # Publication period of the DeviceStatus (retained) and of the ClockSync anchors.
    status_interval_s: PositiveFloat
    clock_sync_interval_s: PositiveFloat
    streams: Annotated[list[StreamConfig], Field(min_length=1)]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.power_on_time_utc >= self.power_off_time_utc:
            raise ValueError("power_on_time_utc must be before power_off_time_utc")
        ids = [s.stream_id for s in self.streams]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicated stream_id in device.streams: {ids}")
        if self.rssi_dbm is not None and self.rssi_dbm.max > 0:
            raise ValueError("rssi_dbm must be <= 0")
        return self

    def stream(self, stream_id: str) -> StreamConfig:
        for s in self.streams:
            if s.stream_id == stream_id:
                return s
        raise KeyError(f"unknown stream_id: {stream_id!r}")


# --- Crew -------------------------------------------------------------------------------------------


class LatentMultipliers(_Model):
    """Individual multiplier of each latent curve (1 = typical trajectory)."""

    spine_elongation: NonNegFloat
    muscle_deconditioning: NonNegFloat
    rom_motor_control: NonNegFloat
    pain_stiffness: NonNegFloat


class CrewBaseline(_Model):
    """Individual physiological baseline (preflight)."""

    # Cord length between clamps at rest, standing (or the reference posture in micro).
    cord_rest_length_mm: PositiveFloat
    # RMS during the true MVC of each EMG stream (before the noisy measurement of the calibration).
    emg_mvc_rms_mv: dict[StreamId, PositiveFloat]


class ProfileConfig(_Model):
    """AstronautProfile data (contract). The contract's optional fields may be missing."""

    date_of_birth: date
    sex_at_birth: SexAtBirth
    gender_identity: Annotated[str, Field(max_length=50)] | None = None
    anthropometry: Anthropometry
    spine_history: SpineHistory | None = None
    space_history: SpaceHistory | None = None
    fitness: Fitness | None = None


class CrewMember(_Model):
    astronaut_id: AstronautId
    device_id: DeviceId
    latent_multipliers: LatentMultipliers
    # Factor over the planned minutes of general exercise (1 = meets the plan).
    exercise_adherence: NonNegFloat
    baseline: CrewBaseline
    profile: ProfileConfig


# --- Protocols and schedule -------------------------------------------------------------------------


class LoadConfig(_Model):
    """Declared load (load_declared of the session contract)."""

    type: Literal["elastic_band", "cable", "free_weight", "none"]
    nominal_kg: NonNegFloat | None
    band_stretch_cm: NonNegFloat | None
    band_id: Annotated[str, Field(max_length=32)] | None = None
    description: Annotated[str, Field(max_length=200)] | None = None


class ExerciseProtocol(_Model):
    """`exercise` session: lead_in, `planned_sets` sets of `reps_per_set` × (rep + rest between reps)
    with `rest_between_sets_s` between sets, lead_out (cord recovery)."""

    session_type: Literal["exercise"]
    streams: Annotated[list[StreamId], Field(min_length=1)]
    lead_in_s: NonNegFloat
    planned_sets: PositiveInt
    reps_per_set: PositiveInt
    rep_duration_s: PositiveFloat
    rest_between_reps_s: NonNegFloat
    rest_between_sets_s: NonNegFloat
    lead_out_s: NonNegFloat
    # Peak %MVC per repetition in preflight condition, with the declared load.
    pct_mvc: PositiveFloat
    # Range of motion per repetition in preflight condition, in plane `plane`.
    rom_deg: PositiveFloat
    plane: AnatomicalPlane
    load: LoadConfig
    # Model hypotheses, not clinical measurements: relative load, resistance,
    # recovery per second and relative variability between repetitions.
    effort_load: PositiveFloat
    fatigue_resistance: PositiveFloat
    recovery_per_s: NonNegFloat
    individual_variability: UnitInterval

    @property
    def planned_reps(self) -> int:
        """Total repetitions of the session (sets × reps per set)."""
        return self.planned_sets * self.reps_per_set


class SpineCheckProtocol(_Model):
    """`spine_check` session: passive measurement at rest of `duration_s`."""

    session_type: Literal["spine_check"]
    streams: Annotated[list[StreamId], Field(min_length=1)]
    duration_s: PositiveFloat


SessionProtocol = Annotated[ExerciseProtocol | SpineCheckProtocol, Field(discriminator="session_type")]


class Bracket(_Model):
    """Session that wraps another: right before (`pre`) or right after (`post`) the session `of`, at `gap_s` from it."""

    phase: Literal["pre", "post"]
    of: Slug
    gap_s: NonNegFloat


class PlannedSessionConfig(_Model):
    """Planned session. `name` is the stable key of the slot (RNG, ids, SessionRef).

    Two forms: at a fixed time (`time_utc` + `start_jitter_s`) or wrapping another session (`bracket`).
    """

    name: Slug
    protocol_id: ProtocolId
    time_utc: TimeOfDay | None = None
    # Standard deviation of the actual start with respect to `time_utc`.
    start_jitter_s: NonNegFloat | None = None
    bracket: Bracket | None = None
    phases: Annotated[list[MissionPhase], Field(min_length=1)]
    # Done on days with index % every_n_days == 0.
    every_n_days: PositiveInt

    @model_validator(mode="after")
    def _timed_xor_bracket(self) -> Self:
        timed = self.time_utc is not None and self.start_jitter_s is not None
        if self.bracket is None and not timed:
            raise ValueError(f"schedule session {self.name}: needs time_utc and start_jitter_s, or a bracket")
        if self.bracket is not None and (self.time_utc is not None or self.start_jitter_s is not None):
            raise ValueError(f"schedule session {self.name}: a bracket session takes no time_utc or start_jitter_s")
        return self


class BackgroundRates(_Model):
    imu: PositiveFloat
    stretch: PositiveFloat


class BackgroundConfig(_Model):
    """Background IMU + cord streaming (no EMG) within a daily `spine_check` session.

    During a planned session the background pauses: same stream_id, the seq continues and session_id changes.
    """

    enabled: StrictBool
    name: Slug
    protocol_id: ProtocolId
    start_time_utc: TimeOfDay
    end_time_utc: TimeOfDay
    streams: Annotated[list[StreamId], Field(min_length=1)]
    sample_rates_hz: BackgroundRates
    # Duration of each background chunk (independent of `signals.chunk_ms`: the background is low frequency
    # and with session chunks it would be much heavier than necessary).
    chunk_ms: PositiveFloat
    # Spontaneous trunk flexions (Poisson process) during the background.
    bends_per_hour: NonNegFloat
    bend_rom_deg: NonNegFloat
    bend_duration_s: PositiveFloat

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.start_time_utc >= self.end_time_utc:
            raise ValueError("background start_time_utc must be before end_time_utc")
        return self


class ScheduleConfig(_Model):
    sessions: list[PlannedSessionConfig]
    background: BackgroundConfig

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        names = [s.name for s in self.sessions] + [self.background.name]
        if len(names) != len(set(names)):
            raise ValueError(f"session names must be unique (background included): {names}")
        return self


# --- Calibrations -----------------------------------------------------------------------------------


class StretchCalibrationConfig(_Model):
    """Cord calibration: on days with index % every_n_days == 0 and the first day of each segment."""

    every_n_days: PositiveInt
    recovery_wait_s: NonNegFloat
    # Error (sd) of each measured point with respect to the true cord curve.
    measurement_noise_mm: NonNegFloat
    n_points: Annotated[StrictInt, Field(ge=2)]
    temperature_c: Float


class EmgMvcCalibrationConfig(_Model):
    """MVC at the start of each `exercise` session (the electrodes are repositioned every time)."""

    window_ms: PositiveInt
    n_trials: PositiveInt
    # Relative sd of the measured mvc_rms_mv with respect to the astronaut's true one.
    measurement_noise_rel: NonNegFloat


class ImuCalibrationConfig(_Model):
    """Static IMU calibration: same days as the cord one if `every_n_days` matches."""

    every_n_days: PositiveInt
    static_duration_s: PositiveFloat
    # Error (sd) of the estimated bias with respect to the true bias of `signals.imu`.
    gyro_bias_noise_dps: NonNegFloat
    accel_bias_noise_g: NonNegFloat


class CalibrationConfig(_Model):
    stretch: StretchCalibrationConfig
    emg_mvc: EmgMvcCalibrationConfig
    imu: ImuCalibrationConfig


# --- Latent curves ----------------------------------------------------------------------------------


class CurvePoint(_Model):
    mission_day: StrictInt
    value: Float


class LatentCurve(_Model):
    """Piecewise-linear curve mission_day -> value (constant outside the ends)."""

    points: Annotated[list[CurvePoint], Field(min_length=1)]
    daily_sd: NonNegFloat
    dose_drift_per_day: Float

    @model_validator(mode="after")
    def _sorted(self) -> Self:
        days = [p.mission_day for p in self.points]
        if any(b <= a for a, b in zip(days, days[1:], strict=False)):
            raise ValueError(f"curve points must be strictly sorted by mission_day: {days}")
        return self

    def pairs(self) -> tuple[tuple[float, float], ...]:
        return tuple((float(p.mission_day), p.value) for p in self.points)


class CurvesConfig(_Model):
    spine_elongation: LatentCurve
    muscle_deconditioning: LatentCurve
    rom_motor_control: LatentCurve
    pain_stiffness: LatentCurve

    def curve(self, latent: LatentName) -> LatentCurve:
        return getattr(self, latent)


# --- Signals ----------------------------------------------------------------------------------------


class EmgAcquisition(_Model):
    """`acquisition` metadata published in each EmgChunk."""

    adc_bits: Annotated[StrictInt, Field(ge=8, le=24)]
    vref_v: PositiveFloat
    gain: PositiveFloat
    input_pin: str


class EmgFilterMeta(_Model):
    """`filter` metadata published in each EmgChunk (null = that filter does not exist)."""

    highpass_hz: NonNegFloat | None
    lowpass_hz: PositiveFloat | None
    notch_hz: Literal[50, 60] | None


class EmgSignalConfig(_Model):
    """Raw EMG: band-filtered Gaussian noise modulated by the %MVC envelope."""

    sample_rate_hz: PositiveFloat
    band_low_hz: PositiveFloat
    band_high_hz: PositiveFloat
    filter_order: PositiveInt
    # Electrode/amplifier noise, always added (RMS in mV).
    noise_rms_mv: NonNegFloat
    # Tonic activity between repetitions and at rest.
    rest_pct_mvc: NonNegFloat
    # Fatigue index accumulated per repetition (0 = fresh); the band lowers by fatigue_band_shift × fatigue.
    fatigue_per_rep: NonNegFloat
    fatigue_band_shift: UnitInterval
    # Effect of deconditioning: %MVC × (1 + gain × d) and fatigue per rep × (1 + gain × d).
    deconditioning_pct_mvc_gain: NonNegFloat
    deconditioning_fatigue_gain: NonNegFloat
    acquisition: EmgAcquisition
    filter: EmgFilterMeta
    # Module realism (#131, docs/sensor-sources.md). Everything marked "hypothesis" (hyp.) is an assumption
    # without a verified figure, not a measurement: the interference values are 0 (off) unless the mission
    # asks for them.
    # Published signal type: raw | rectified | envelope (rectified and smoothed, like the Muscle Sensor V3).
    # Hypothesis: the purchased unit is not confirmed; "envelope" is the firmware/gateway default.
    signal_kind: Literal["raw", "rectified", "envelope"] = "envelope"
    # Cutoff (Hz) of the envelope smoothing. Hypothesis: no figure read from the module.
    envelope_lowpass_hz: PositiveFloat = 5.0
    # Slow baseline drift: RMS (mV) and cutoff frequency (Hz). Hypothesis, no verified figure.
    baseline_wander_mv: NonNegFloat = 0.0
    baseline_wander_hz: PositiveFloat = 0.5
    # Powerline interference: peak amplitude (mV) and frequency (50 Hz in Europe). Hypothesis, no magnitude figure.
    mains_amplitude_mv: NonNegFloat = 0.0
    mains_hz: PositiveFloat = 50.0
    # ECG-like contamination: peak amplitude (mV) and heart rate. Hypothesis, no verified figure.
    ecg_leak_mv: NonNegFloat = 0.0
    ecg_heart_rate_bpm: PositiveFloat = 70.0
    # Quantises to the `acquisition` ADC resolution (adc_bits, vref_v, gain), referred to the input.
    quantize_adc: bool = False

    @model_validator(mode="after")
    def _band(self) -> Self:
        if not self.band_low_hz < self.band_high_hz < self.sample_rate_hz / 2:
            raise ValueError("EMG band must satisfy band_low_hz < band_high_hz < sample_rate_hz / 2")
        return self


class Xyz(_Model):
    x: Float
    y: Float
    z: Float


class Mounting(_Model):
    """Body axis each sensor axis points to in the reference posture."""

    sensor_x: BodyAxis
    sensor_y: BodyAxis
    sensor_z: BodyAxis

    @model_validator(mode="after")
    def _orthogonal(self) -> Self:
        lines = {
            BodyAxis.CRANIAL: "cc", BodyAxis.CAUDAL: "cc",
            BodyAxis.ANTERIOR: "ap", BodyAxis.POSTERIOR: "ap",
            BodyAxis.LEFT: "lr", BodyAxis.RIGHT: "lr",
        }  # fmt: skip
        if len({lines[self.sensor_x], lines[self.sensor_y], lines[self.sensor_z]}) != 3:
            raise ValueError("mounting axes must point along three different body axes")
        return self


class ImuSignalConfig(_Model):
    """6-axis IMU: minimum-jerk repetitions; gyro = derivative; accel = rotated gravity + motion."""

    sample_rate_hz: PositiveFloat
    # White-noise spectral density of the chip (MPU-6050 spec rev. 3.4, docs/sensor-sources.md): accel in
    # ug/sqrt(Hz) (400, spec), gyro in dps/sqrt(Hz) (0.005, spec marked (*): check against the datasheet). The deviation
    # per sample is density x sqrt(effective bandwidth = min(dlpf_hz, sample_rate_hz / 2)).
    accel_noise_density_ug_rthz: NonNegFloat
    gyro_noise_density_dps_rthz: NonNegFloat
    # Initial bias (zero offset) of each axis; the slow drift (Ornstein-Uhlenbeck) is superimposed on it.
    accel_bias_g: Xyz
    gyro_bias_dps: Xyz
    # Maxima of the total bias per axis (spec, NOT typical): gyro 20 dps; accel 0.05 g X/Y and 0.08 g Z (*) (to be checked).
    accel_bias_max_g: Xyz
    gyro_bias_max_dps: NonNegFloat
    # Bias drift (HYPOTHESIS, no datasheet figure): time constant and stationary deviation of the OU.
    bias_drift_tau_s: PositiveFloat
    accel_bias_drift_sigma_g: NonNegFloat
    gyro_bias_drift_sigma_dps: NonNegFloat
    # Quantises to int16 counts (LSB = 32768 / range in g, 131 * 250 / range in dps; the output stays in g and deg/s).
    quantize_output: bool
    accel_range_g: Literal[2, 4, 8, 16]
    gyro_range_dps: Literal[250, 500, 1000, 2000]
    # Causal 1st-order low-pass at dlpf_hz (the real MPU-6050: 44 Hz with CONFIG=0x03; exact bandwidth and delay
    # unverified). No effect if dlpf_hz >= sample_rate_hz / 2.
    dlpf_hz: PositiveFloat
    mounting: Mounting
    # Distance from the flexion axis to the sensor (tangential and centripetal accelerations).
    lever_arm_m: NonNegFloat
    # Effective ROM = rom_deg × (1 − rom_loss_gain × rom_motor_control) × (1 − pain_rom_gain × pain_stiffness).
    rom_loss_gain: NonNegFloat
    pain_rom_gain: NonNegFloat
    # Tremor / poor motor control: deg/s RMS per unit of rom_motor_control.
    tremor_dps_per_unit: NonNegFloat


class CordPoint(_Model):
    resistance_ohm: PositiveFloat
    length_mm: PositiveFloat


class StretchCircuit(_Model):
    """Divider: Vout = vref × R_cord / (R_cord + series_resistor); counts = round(Vout / vref × (2^bits − 1))."""

    adc_bits: Annotated[StrictInt, Field(ge=8, le=24)]
    vref_v: PositiveFloat
    series_resistor_ohm: PositiveFloat
    input_pin: str


class StretchSignalConfig(_Model):
    """Conductive cord: elongation -> inverse true curve -> resistance -> ADC, with hysteresis."""

    sample_rate_hz: PositiveFloat
    # True physical resistance -> length curve (the published calibration is a noisy measurement of it).
    curve: Annotated[list[CordPoint], Field(min_length=2)]
    circuit: StretchCircuit
    adc_noise_counts: NonNegFloat
    # Hysteresis: a fraction `hysteresis_gain` of the dynamic stretch remains as a residual that
    # recovers with time constant `hysteresis_tau_s` (the cord takes 1-2 min).
    hysteresis_tau_s: PositiveFloat
    hysteresis_gain: UnitInterval
    # Residual above which length_mm is flagged `band_recovering` with that score.
    band_recovering_threshold_mm: PositiveFloat
    band_recovering_score: UnitInterval
    # Cord lengthening per degree of flexion and per mm of elongation of the instrumented segment.
    flexion_mm_per_deg: NonNegFloat
    elongation_coupling: NonNegFloat
    # Shortening over the waking day (power-on -> power-off) at 1 g; scales with gravity.
    diurnal_loss_mm_at_1g: NonNegFloat
    # PROVISIONAL hypotheses (docs/sensor-sources.md, no quantitative source; not measurements):
    # per-reading resistance noise (ohm, independent per sample).
    read_noise_ohm: NonNegFloat
    # Dispersion between repeated readings: one resistance offset (ohm) per session, N(0, sd). The bench shows
    # readings of the same length that differ by up to ~150 ohm; it is not pure per-sample noise.
    repeat_dispersion_ohm: NonNegFloat
    # Creep: extra slow elongation = creep_fraction x sustained stretch, with time constant creep_tau_s
    # (rises and recovers with it). No manufacturer figure.
    creep_fraction: UnitInterval
    creep_tau_s: PositiveFloat
    # Lot variation: resistance gain per astronaut ~ N(1, lot_variation_fraction), see `lot_gain`.
    lot_variation_fraction: UnitInterval

    @model_validator(mode="after")
    def _monotonic(self) -> Self:
        rs = [p.resistance_ohm for p in self.curve]
        ls = [p.length_mm for p in self.curve]
        if any(b <= a for a, b in zip(rs, rs[1:], strict=False)) or any(b <= a for a, b in zip(ls, ls[1:], strict=False)):
            raise ValueError("stretch curve must be strictly increasing in resistance_ohm and length_mm")
        return self

    def curve_pairs(self) -> tuple[tuple[float, float], ...]:
        """Curve as pairs (resistance_ohm, length_mm) ordered."""
        return tuple((p.resistance_ohm, p.length_mm) for p in self.curve)


class SignalsConfig(_Model):
    # Duration of each published chunk, the same for all streams.
    chunk_ms: PositiveInt
    emg: EmgSignalConfig
    imu: ImuSignalConfig
    stretch: StretchSignalConfig

    def rate(self, kind: StreamKind) -> float:
        return {StreamKind.EMG: self.emg.sample_rate_hz, StreamKind.IMU: self.imu.sample_rate_hz,
                StreamKind.STRETCH: self.stretch.sample_rate_hz}[kind]  # fmt: skip

    def samples_per_chunk(self, rate_hz: float, *, chunk_ms: float | None = None) -> int:
        """Samples per chunk at `rate_hz`; `chunk_ms` None uses the sessions' (`signals.chunk_ms`),
        the background passes its own (`schedule.background.chunk_ms`)."""
        return round(rate_hz * (self.chunk_ms if chunk_ms is None else chunk_ms) / 1000)


# --- Faults -----------------------------------------------------------------------------------------


class QualityFaultConfig(_Model):
    """Poor-quality episode: alters the signal and marks quality (flag + score, assessed_by gateway)."""

    # Per-chunk probability that an episode starts (1 = always, 0 = never).
    probability: Probability
    duration_ms: NonNegRange
    score: UnitInterval
    # Magnitude relative to the channel's clean signal (see the module docstring).
    amplitude: NonNegFloat
    kinds: Annotated[list[StreamKind], Field(min_length=1)]


class PowerlineFaultConfig(QualityFaultConfig):
    frequency_hz: Literal[50, 60]


class MotionArtifactFaultConfig(QualityFaultConfig):
    """Low-frequency burst: three sines with uniform frequencies in [band_low_hz, ceiling]."""

    band_low_hz: PositiveFloat
    # Band ceiling; the effective one is min(band_high_hz, max_fraction_of_rate x fs) to avoid approaching Nyquist.
    band_high_hz: PositiveFloat
    max_fraction_of_rate: Annotated[float, Field(gt=0, lt=0.5)]

    @model_validator(mode="after")
    def _band(self) -> Self:
        if self.band_low_hz >= self.band_high_hz:
            raise ValueError("band_low_hz must be < band_high_hz")
        return self


class WarmupFaultConfig(QualityFaultConfig):
    """Exponentially decaying drift: tau = decay_fraction x episode duration."""

    decay_fraction: PositiveFloat


class QualityFaultsConfig(_Model):
    """Injectable faults. packet_gap, band_recovering and clock_uncertain are not injected: they are derived."""

    clipping: QualityFaultConfig
    lead_off: QualityFaultConfig
    motion_artifact: MotionArtifactFaultConfig
    powerline_noise: PowerlineFaultConfig
    low_snr: QualityFaultConfig
    saturated_adc: QualityFaultConfig
    out_of_calibration: QualityFaultConfig
    sensor_warmup: WarmupFaultConfig

    def items(self) -> tuple[tuple[QualityFlag, QualityFaultConfig], ...]:
        return tuple((QualityFlag(name), getattr(self, name)) for name in type(self).model_fields)


class PacketLossConfig(_Model):
    # Per-chunk probability of starting a burst of `burst_chunks` lost chunks (seq jump).
    probability: Probability
    burst_chunks: IntRange
    # Bursts of at least this size put the stream in `stalled` and the DeviceStatus in `degraded`.
    stalled_after_chunks: PositiveInt
    # Score of the first chunk after a loss (packet_gap flag).
    packet_gap_score: UnitInterval

    @model_validator(mode="after")
    def _burst(self) -> Self:
        if self.burst_chunks.min < 1:
            raise ValueError("burst_chunks.min must be >= 1")
        return self


class RebootConfig(_Model):
    # Spontaneous restarts (Poisson) while the device is powered on.
    probability_per_hour: NonNegFloat
    offline_s: NonNegRange


class ClockFaultConfig(_Model):
    # received_at = real instant of the end of the chunk + uniform latency in latency_ms.
    latency_ms: NonNegRange
    # Device clock drift per boot, uniform in the interval (ppm, signed).
    drift_ppm: FloatRange
    # uncertainty_ms published in each ClockSync.
    sync_uncertainty_ms: NonNegRange
    # Clock error accumulated since the last anchor above which clock_uncertain is flagged.
    uncertain_threshold_ms: PositiveFloat
    clock_uncertain_score: UnitInterval


class DeviceFaultsConfig(_Model):
    packet_loss: PacketLossConfig
    reboot: RebootConfig
    clock: ClockFaultConfig


class FaultsConfig(_Model):
    quality: QualityFaultsConfig
    device: DeviceFaultsConfig


# --- Daily context and self-report ------------------------------------------------------------------


class ExercisePlanItem(_Model):
    """General exercise planned per day (DailyContext.exercise), not the protocol session."""

    modality: Literal["ared", "cevis", "t2", "other"]
    planned_min: PositiveFloat
    sd_min: NonNegFloat
    skip_probability: Probability
    load_kg: NonNegFloat | None
    rpe: Nrs | None


class CountermeasurePlanItem(_Model):
    type: Literal["axial_loading_suit", "stretching", "other"]
    planned_min: PositiveFloat
    sd_min: NonNegFloat
    skip_probability: Probability


class MedicationRule(_Model):
    """Taken with `probability` on days when pain_stiffness >= pain_threshold."""

    name: Annotated[str, Field(min_length=1, max_length=80)]
    category: Literal["analgesic", "anti_inflammatory", "muscle_relaxant", "sleep_aid", "other"]
    dose_mg: PositiveFloat | None
    pain_threshold: NonNegFloat
    probability: Probability
    time_utc: TimeOfDay


class DailyContextConfig(_Model):
    # Plan per phase; every phase used in `segments` needs an entry (may be an empty list).
    exercise: dict[MissionPhase, list[ExercisePlanItem]]
    countermeasures: dict[MissionPhase, list[CountermeasurePlanItem]]
    medication: list[MedicationRule]


class PainLocationConfig(_Model):
    region: SpineRegion
    side: Literal["left", "right", "midline", "bilateral"]


class SelfReportConfig(_Model):
    """Daily DailySelfReport (always, without compliance gaps)."""

    time_utc: TimeOfDay
    time_jitter_s: NonNegFloat
    # NRS = clip(round(per_unit × pain_stiffness + N(0, nrs_noise_sd)), 0, 10).
    pain_nrs_per_unit: NonNegFloat
    stiffness_nrs_per_unit: NonNegFloat
    nrs_noise_sd: NonNegFloat
    # pain_nrs_worst_24h = pain_nrs + uniform integer in the interval (capped at 10).
    worst_24h_extra_nrs: IntRange
    # With pain_nrs >= this value the pain_locations are declared.
    pain_location_min_nrs: Nrs
    pain_locations: Annotated[list[PainLocationConfig], Field(min_length=1)]
    sleep_hours: Normal
    sleep_quality_nrs: Normal
    # Sleep hours lost per pain_nrs point.
    sleep_pain_penalty_h_per_nrs: NonNegFloat
    red_flag_probability_per_day: Probability
    red_flags: list[RedFlag]

    @model_validator(mode="after")
    def _extra(self) -> Self:
        if self.worst_24h_extra_nrs.min < 0:
            raise ValueError("worst_24h_extra_nrs.min must be >= 0")
        if self.red_flag_probability_per_day > 0 and not self.red_flags:
            raise ValueError("red_flags must list candidates when red_flag_probability_per_day > 0")
        return self


# --- Root -------------------------------------------------------------------------------------------


class MissionConfig(_Model):
    seed: NonNegInt
    start_date: date
    start_mission_day: StrictInt
    segments: Annotated[list[Segment], Field(min_length=1)]
    crew: Annotated[list[CrewMember], Field(min_length=1)]
    device: DeviceConfig
    protocols: dict[ProtocolId, SessionProtocol]
    schedule: ScheduleConfig
    calibration: CalibrationConfig
    curves: CurvesConfig
    signals: SignalsConfig
    faults: FaultsConfig
    daily_context: DailyContextConfig
    self_report: SelfReportConfig

    @property
    def total_days(self) -> int:
        return sum(s.days for s in self.segments)

    def member(self, astronaut_id: str) -> CrewMember:
        for m in self.crew:
            if m.astronaut_id == astronaut_id:
                return m
        raise KeyError(f"unknown astronaut_id: {astronaut_id!r}")

    def protocol(self, protocol_id: str) -> ExerciseProtocol | SpineCheckProtocol:
        try:
            return self.protocols[protocol_id]
        except KeyError:
            raise KeyError(f"unknown protocol_id: {protocol_id!r}") from None

    @model_validator(mode="after")
    def _cross_references(self) -> Self:
        problems: list[str] = []
        streams = {s.stream_id: s for s in self.device.streams}
        emg_streams = {s for s, cfg in streams.items() if cfg.stream_kind is StreamKind.EMG}

        astronauts = [m.astronaut_id for m in self.crew]
        devices = [m.device_id for m in self.crew]
        if len(astronauts) != len(set(astronauts)):
            problems.append(f"duplicated astronaut_id in crew: {astronauts}")
        if len(devices) != len(set(devices)):
            problems.append(f"duplicated device_id in crew: {devices}")

        lengths = [p.length_mm for p in self.signals.stretch.curve]
        for m in self.crew:
            if set(m.baseline.emg_mvc_rms_mv) != emg_streams:
                problems.append(f"{m.astronaut_id}: baseline.emg_mvc_rms_mv must cover exactly the EMG streams {sorted(emg_streams)}")
            if not lengths[0] <= m.baseline.cord_rest_length_mm <= lengths[-1]:
                problems.append(f"{m.astronaut_id}: cord_rest_length_mm outside the stretch curve [{lengths[0]}, {lengths[-1]}]")

        for pid, protocol in self.protocols.items():
            unknown = [s for s in protocol.streams if s not in streams]
            if unknown:
                problems.append(f"protocol {pid}: unknown streams {unknown}")

        for planned in self.schedule.sessions:
            if planned.protocol_id not in self.protocols:
                problems.append(f"schedule session {planned.name}: unknown protocol_id {planned.protocol_id}")
            if planned.time_utc is not None and not self.device.power_on_time_utc <= planned.time_utc < self.device.power_off_time_utc:
                problems.append(f"schedule session {planned.name}: time_utc outside the device power window")
            if planned.bracket is not None:
                anchor = next((o for o in self.schedule.sessions if o.name == planned.bracket.of), None)
                if anchor is None or anchor.bracket is not None:
                    problems.append(f"schedule session {planned.name}: bracket.of must name a timed session of the schedule")
                elif not isinstance(self.protocols.get(anchor.protocol_id), ExerciseProtocol):
                    problems.append(f"schedule session {planned.name}: bracket.of must be an exercise session")

        background = self.schedule.background
        for s in background.streams:
            if s not in streams:
                problems.append(f"background: unknown stream {s}")
            elif streams[s].stream_kind is StreamKind.EMG:
                problems.append(f"background: EMG stream {s} not allowed (background is IMU + cord only)")
        if background.start_time_utc < self.device.power_on_time_utc or background.end_time_utc > self.device.power_off_time_utc:
            problems.append("background window must be inside the device power window")

        phases = {seg.phase for seg in self.segments}
        for section in ("exercise", "countermeasures"):
            missing = phases - set(getattr(self.daily_context, section))
            if missing:
                problems.append(f"daily_context.{section} missing phases {sorted(p.value for p in missing)}")

        # Each chunk must contain a whole number of samples: in a session with `signals.chunk_ms`, in the background
        # with `schedule.background.chunk_ms`.
        rates = {
            ("signals.emg", self.signals.chunk_ms): self.signals.emg.sample_rate_hz,
            ("signals.imu", self.signals.chunk_ms): self.signals.imu.sample_rate_hz,
            ("signals.stretch", self.signals.chunk_ms): self.signals.stretch.sample_rate_hz,
            ("schedule.background.sample_rates_hz.imu", background.chunk_ms): background.sample_rates_hz.imu,
            ("schedule.background.sample_rates_hz.stretch", background.chunk_ms): background.sample_rates_hz.stretch,
        }
        for (where, chunk_ms), rate in rates.items():
            per_chunk = rate * chunk_ms / 1000
            if per_chunk < 1 or abs(per_chunk - round(per_chunk)) > 1e-9:
                problems.append(f"{where}: {rate} Hz × chunk_ms {chunk_ms} must give a whole number (>= 1) of samples")

        if problems:
            raise ValueError("; ".join(problems))
        return self


def load_config(path: str | Path) -> MissionConfig:
    """Reads and validates a mission YAML. Raises pydantic.ValidationError if something is missing or extra."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return MissionConfig.model_validate(data)
