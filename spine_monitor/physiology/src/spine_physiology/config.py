"""Configuración de misión del simulador: YAML validado con Pydantic (`load_config`).

Regla del paquete: **ningún parámetro numérico (fisiológico o técnico) tiene valor por defecto en el
código**. Todo campo numérico es obligatorio y sale del YAML; los que el contrato permite omitir se
escriben explícitamente como `null`. Solo los campos descriptivos opcionales (detalles de colocación,
texto libre, secciones opcionales del perfil) pueden faltar, y entonces no se emiten.
Los valores clínicos reales los pondrá Aguado; `configs/example-mission.yaml` es un PLACEHOLDER.

Convenciones que fijan la semántica de los parámetros (las implementaciones las respetan):
- Día de misión: el índice de día `d` (0 = primer día simulado) corresponde a la fecha
  `start_date + d` y a `mission_day = start_mission_day + d`. Las horas `*_utc` son hora del día UTC.
- Latentes diarios: `spine_elongation` en mm sobre la longitud de reposo preflight del segmento
  instrumentado; `muscle_deconditioning`, `rom_motor_control` y `pain_stiffness` son índices
  adimensionales (0 = condición preflight, 1 = deterioro máximo modelado).
  Valor del día = multiplicador del astronauta × curva(mission_day) + deriva por dosis + N(0, daily_sd).
- Deriva por dosis: las curvas son la trayectoria típica *con* el ejercicio planificado. Cada día
  la deriva acumulada suma `dose_drift_per_day × (1 − minutos reales / minutos planificados)`.
- Fallos de calidad: `amplitude` es relativa a la señal limpia del canal en el chunk (RMS, o pico
  para `clipping`), así que es adimensional para todos los tipos de stream.
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

# --- Tipos base -------------------------------------------------------------------------------------
# Strict: un YAML con "1e-3" (PyYAML lo lee como texto) o con `true` donde va un número falla en vez
# de convertirse en silencio. Escribe los exponentes como 1.0e-3. Los enteros valen donde va un float.

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
    # Solo texto "HH:MM" o "HH:MM:SS" (entre comillas en el YAML): YAML 1.1 puede leer 07:00 como un
    # entero sexagesimal y un número aquí casi siempre es un error de escritura.
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
    """Intervalo cerrado [min, max] para muestreo uniforme."""

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
    """Distribución normal N(mean, sd)."""

    mean: Float
    sd: NonNegFloat


# --- Misión -----------------------------------------------------------------------------------------


class Segment(_Model):
    """Tramo de misión: `days` días consecutivos con la misma fase y gravedad."""

    days: PositiveInt
    phase: MissionPhase
    gravity_environment: GravityEnvironment


# --- Dispositivo ------------------------------------------------------------------------------------


class Span(_Model):
    from_level: SpineLevel
    to_level: SpineLevel


class Ground(_Model):
    """Electrodo de tierra (GND) del EMG."""

    location: GroundLocation
    side: Side | None = None


class Placement(_Model):
    """Colocación del sensor (la del contrato). EMG: muscle + side (+ separación de electrodos, tramo
    vertebral y tierra); IMU: spine_level; cordón: spine_region (+ tramo)."""

    muscle: Muscle | None = None
    side: Side | None = None
    spine_level: SpineLevel | None = None
    spine_region: SpineRegion | None = None
    electrode_spacing_cm: PositiveFloat | None = None
    ground: Ground | None = None
    span: Span | None = None
    description: Annotated[str, Field(max_length=200)] | None = None


class StreamConfig(_Model):
    """Stream del dispositivo. La frecuencia de muestreo es la de su tipo (`signals.<kind>.sample_rate_hz`)."""

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
    """Batería: carga nocturna; cada día arranca en `start_pct` al encender el dispositivo."""

    start_pct: Percent
    drain_pct_per_hour_streaming: NonNegFloat
    drain_pct_per_hour_idle: NonNegFloat
    # Por debajo de este nivel el DeviceStatus pasa a `degraded`.
    degraded_below_pct: Percent

    @model_validator(mode="after")
    def _drain(self) -> Self:
        if self.drain_pct_per_hour_streaming < self.drain_pct_per_hour_idle:
            raise ValueError("drain_pct_per_hour_streaming must be >= drain_pct_per_hour_idle")
        return self


class DeviceConfig(_Model):
    """Wearable simulado (uno por astronauta, mismo modelo y streams para todos)."""

    firmware_version: Annotated[str, Field(min_length=1, max_length=32)]
    link_type: Literal["ble", "usb_serial", "simulated"]
    # RSSI uniforme en [min, max] dBm (enteros <= 0); null si el enlace no lo tiene.
    rssi_dbm: IntRange | None
    # Ventana diaria encendido -> apagado. Cada encendido es un arranque (boot_id nuevo, seq y reloj a 0).
    power_on_time_utc: TimeOfDay
    power_off_time_utc: TimeOfDay
    battery: BatteryConfig
    temperature_c: Normal
    # Periodo de publicación del DeviceStatus (retained) y de los anclajes ClockSync.
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


# --- Tripulación ------------------------------------------------------------------------------------


class LatentMultipliers(_Model):
    """Multiplicador individual de cada curva latente (1 = trayectoria típica)."""

    spine_elongation: NonNegFloat
    muscle_deconditioning: NonNegFloat
    rom_motor_control: NonNegFloat
    pain_stiffness: NonNegFloat


class CrewBaseline(_Model):
    """Línea base fisiológica individual (preflight)."""

    # Longitud del cordón entre pinzas en reposo, de pie (o postura de referencia en micro).
    cord_rest_length_mm: PositiveFloat
    # RMS durante la MVC real de cada stream EMG (antes de la medición ruidosa de la calibración).
    emg_mvc_rms_mv: dict[StreamId, PositiveFloat]


class ProfileConfig(_Model):
    """Datos del AstronautProfile (contrato). Los campos opcionales del contrato pueden faltar."""

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
    # Factor sobre los minutos planificados de ejercicio general (1 = cumple el plan).
    exercise_adherence: NonNegFloat
    baseline: CrewBaseline
    profile: ProfileConfig


# --- Protocolos y agenda ----------------------------------------------------------------------------


class LoadConfig(_Model):
    """Carga declarada (load_declared del contrato de sesión)."""

    type: Literal["elastic_band", "cable", "free_weight", "none"]
    nominal_kg: NonNegFloat | None
    band_stretch_cm: NonNegFloat | None
    band_id: Annotated[str, Field(max_length=32)] | None = None
    description: Annotated[str, Field(max_length=200)] | None = None


class ExerciseProtocol(_Model):
    """Sesión `exercise`: lead_in, `planned_sets` series de `reps_per_set` × (rep + descanso entre reps)
    con `rest_between_sets_s` entre series, lead_out (recuperación del cordón)."""

    session_type: Literal["exercise"]
    streams: Annotated[list[StreamId], Field(min_length=1)]
    lead_in_s: NonNegFloat
    planned_sets: PositiveInt
    reps_per_set: PositiveInt
    rep_duration_s: PositiveFloat
    rest_between_reps_s: NonNegFloat
    rest_between_sets_s: NonNegFloat
    lead_out_s: NonNegFloat
    # Pico de %MVC por repetición en condición preflight, con la carga declarada.
    pct_mvc: PositiveFloat
    # Rango de movimiento por repetición en condición preflight, en el plano `plane`.
    rom_deg: PositiveFloat
    plane: AnatomicalPlane
    load: LoadConfig

    @property
    def planned_reps(self) -> int:
        """Repeticiones totales de la sesión (series × reps por serie)."""
        return self.planned_sets * self.reps_per_set


class SpineCheckProtocol(_Model):
    """Sesión `spine_check`: medición pasiva en reposo de `duration_s`."""

    session_type: Literal["spine_check"]
    streams: Annotated[list[StreamId], Field(min_length=1)]
    duration_s: PositiveFloat


SessionProtocol = Annotated[ExerciseProtocol | SpineCheckProtocol, Field(discriminator="session_type")]


class Bracket(_Model):
    """Sesión que envuelve a otra: justo antes (`pre`) o justo después (`post`) de la sesión `of`, a `gap_s` de ella."""

    phase: Literal["pre", "post"]
    of: Slug
    gap_s: NonNegFloat


class PlannedSessionConfig(_Model):
    """Sesión planificada. `name` es la clave estable del hueco (RNG, ids, SessionRef).

    Dos formas: a una hora fija (`time_utc` + `start_jitter_s`) o envolviendo a otra sesión (`bracket`).
    """

    name: Slug
    protocol_id: ProtocolId
    time_utc: TimeOfDay | None = None
    # Desviación típica del inicio real respecto a `time_utc`.
    start_jitter_s: NonNegFloat | None = None
    bracket: Bracket | None = None
    phases: Annotated[list[MissionPhase], Field(min_length=1)]
    # Se hace los días con índice % every_n_days == 0.
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
    """Streaming de fondo IMU + cordón (sin EMG) dentro de una sesión `spine_check` diaria.

    Durante una sesión planificada el fondo se pausa: mismo stream_id, el seq sigue y cambia session_id.
    """

    enabled: StrictBool
    name: Slug
    protocol_id: ProtocolId
    start_time_utc: TimeOfDay
    end_time_utc: TimeOfDay
    streams: Annotated[list[StreamId], Field(min_length=1)]
    sample_rates_hz: BackgroundRates
    # Duración de cada chunk del fondo (independiente de `signals.chunk_ms`: el fondo es de baja frecuencia
    # y con chunks de sesión sería mucho más pesado de lo necesario).
    chunk_ms: PositiveFloat
    # Flexiones espontáneas del tronco (proceso de Poisson) durante el fondo.
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


# --- Calibraciones ----------------------------------------------------------------------------------


class StretchCalibrationConfig(_Model):
    """Calibración del cordón: los días con índice % every_n_days == 0 y el primer día de cada tramo."""

    every_n_days: PositiveInt
    recovery_wait_s: NonNegFloat
    # Error (sd) de cada punto medido respecto a la curva real del cordón.
    measurement_noise_mm: NonNegFloat
    n_points: Annotated[StrictInt, Field(ge=2)]
    temperature_c: Float


class EmgMvcCalibrationConfig(_Model):
    """MVC al inicio de cada sesión `exercise` (los electrodos se recolocan cada vez)."""

    window_ms: PositiveInt
    n_trials: PositiveInt
    # sd relativa del mvc_rms_mv medido respecto al real del astronauta.
    measurement_noise_rel: NonNegFloat


class ImuCalibrationConfig(_Model):
    """Calibración estática de la IMU: mismos días que la del cordón si coincide `every_n_days`."""

    every_n_days: PositiveInt
    static_duration_s: PositiveFloat
    # Error (sd) del bias estimado respecto al bias real de `signals.imu`.
    gyro_bias_noise_dps: NonNegFloat
    accel_bias_noise_g: NonNegFloat


class CalibrationConfig(_Model):
    stretch: StretchCalibrationConfig
    emg_mvc: EmgMvcCalibrationConfig
    imu: ImuCalibrationConfig


# --- Curvas latentes --------------------------------------------------------------------------------


class CurvePoint(_Model):
    mission_day: StrictInt
    value: Float


class LatentCurve(_Model):
    """Curva piecewise-linear mission_day -> valor (constante fuera de los extremos)."""

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


# --- Señales ----------------------------------------------------------------------------------------


class EmgAcquisition(_Model):
    """Metadato `acquisition` publicado en cada EmgChunk."""

    adc_bits: Annotated[StrictInt, Field(ge=8, le=24)]
    vref_v: PositiveFloat
    gain: PositiveFloat
    input_pin: str


class EmgFilterMeta(_Model):
    """Metadato `filter` publicado en cada EmgChunk (null = ese filtro no existe)."""

    highpass_hz: NonNegFloat | None
    lowpass_hz: PositiveFloat | None
    notch_hz: Literal[50, 60] | None


class EmgSignalConfig(_Model):
    """EMG crudo: ruido gaussiano filtrado en banda y modulado por la envolvente de %MVC."""

    sample_rate_hz: PositiveFloat
    band_low_hz: PositiveFloat
    band_high_hz: PositiveFloat
    filter_order: PositiveInt
    # Ruido de electrodos/amplificador, sumado siempre (RMS en mV).
    noise_rms_mv: NonNegFloat
    # Actividad tónica entre repeticiones y en reposo.
    rest_pct_mvc: NonNegFloat
    # Índice de fatiga acumulado por repetición (0 = fresco); la banda baja fatigue_band_shift × fatiga.
    fatigue_per_rep: NonNegFloat
    fatigue_band_shift: UnitInterval
    # Efecto del desacondicionamiento: %MVC × (1 + gain × d) y fatiga por rep × (1 + gain × d).
    deconditioning_pct_mvc_gain: NonNegFloat
    deconditioning_fatigue_gain: NonNegFloat
    acquisition: EmgAcquisition
    filter: EmgFilterMeta

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
    """Eje corporal al que apunta cada eje del sensor en la postura de referencia."""

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
    """IMU de 6 ejes: repeticiones minimum-jerk; gyro = derivada; accel = gravedad rotada + movimiento."""

    sample_rate_hz: PositiveFloat
    accel_noise_g: NonNegFloat
    gyro_noise_dps: NonNegFloat
    accel_bias_g: Xyz
    gyro_bias_dps: Xyz
    accel_range_g: Literal[2, 4, 8, 16]
    gyro_range_dps: Literal[250, 500, 1000, 2000]
    dlpf_hz: PositiveFloat
    mounting: Mounting
    # Distancia del eje de flexión al sensor (aceleraciones tangencial y centrípeta).
    lever_arm_m: NonNegFloat
    # ROM efectivo = rom_deg × (1 − rom_loss_gain × rom_motor_control) × (1 − pain_rom_gain × pain_stiffness).
    rom_loss_gain: NonNegFloat
    pain_rom_gain: NonNegFloat
    # Temblor / mal control motor: deg/s RMS por unidad de rom_motor_control.
    tremor_dps_per_unit: NonNegFloat


class CordPoint(_Model):
    resistance_ohm: PositiveFloat
    length_mm: PositiveFloat


class StretchCircuit(_Model):
    """Divisor: Vout = vref × R_cord / (R_cord + series_resistor); counts = round(Vout / vref × (2^bits − 1))."""

    adc_bits: Annotated[StrictInt, Field(ge=8, le=24)]
    vref_v: PositiveFloat
    series_resistor_ohm: PositiveFloat
    input_pin: str


class StretchSignalConfig(_Model):
    """Cordón conductor: elongación -> curva real inversa -> resistencia -> ADC, con histéresis."""

    sample_rate_hz: PositiveFloat
    # Curva física real resistencia -> longitud (la calibración publicada es una medida ruidosa de ella).
    curve: Annotated[list[CordPoint], Field(min_length=2)]
    circuit: StretchCircuit
    adc_noise_counts: NonNegFloat
    # Histéresis: una fracción `hysteresis_gain` del estiramiento dinámico queda como residuo que se
    # recupera con constante de tiempo `hysteresis_tau_s` (el cordón tarda 1-2 min).
    hysteresis_tau_s: PositiveFloat
    hysteresis_gain: UnitInterval
    # Residuo a partir del cual length_mm se marca `band_recovering` con ese score.
    band_recovering_threshold_mm: PositiveFloat
    band_recovering_score: UnitInterval
    # Alargamiento del cordón por grado de flexión y por mm de elongación del segmento instrumentado.
    flexion_mm_per_deg: NonNegFloat
    elongation_coupling: NonNegFloat
    # Acortamiento a lo largo del día de vigilia (encendido -> apagado) con 1 g; escala con la gravedad.
    diurnal_loss_mm_at_1g: NonNegFloat

    @model_validator(mode="after")
    def _monotonic(self) -> Self:
        rs = [p.resistance_ohm for p in self.curve]
        ls = [p.length_mm for p in self.curve]
        if any(b <= a for a, b in zip(rs, rs[1:], strict=False)) or any(b <= a for a, b in zip(ls, ls[1:], strict=False)):
            raise ValueError("stretch curve must be strictly increasing in resistance_ohm and length_mm")
        return self

    def curve_pairs(self) -> tuple[tuple[float, float], ...]:
        """Curva como pares (resistance_ohm, length_mm) ordenados."""
        return tuple((p.resistance_ohm, p.length_mm) for p in self.curve)


class SignalsConfig(_Model):
    # Duración de cada chunk publicado, igual para todos los streams.
    chunk_ms: PositiveInt
    emg: EmgSignalConfig
    imu: ImuSignalConfig
    stretch: StretchSignalConfig

    def rate(self, kind: StreamKind) -> float:
        return {StreamKind.EMG: self.emg.sample_rate_hz, StreamKind.IMU: self.imu.sample_rate_hz,
                StreamKind.STRETCH: self.stretch.sample_rate_hz}[kind]  # fmt: skip

    def samples_per_chunk(self, rate_hz: float, *, chunk_ms: float | None = None) -> int:
        """Muestras por chunk a `rate_hz`; `chunk_ms` None usa el de las sesiones (`signals.chunk_ms`),
        el fondo pasa el suyo (`schedule.background.chunk_ms`)."""
        return round(rate_hz * (self.chunk_ms if chunk_ms is None else chunk_ms) / 1000)


# --- Fallos -----------------------------------------------------------------------------------------


class QualityFaultConfig(_Model):
    """Episodio de mala calidad: altera la señal y marca quality (flag + score, assessed_by gateway)."""

    # Probabilidad por chunk de que empiece un episodio (1 = siempre, 0 = nunca).
    probability: Probability
    duration_ms: NonNegRange
    score: UnitInterval
    # Magnitud relativa a la señal limpia del canal (ver docstring del módulo).
    amplitude: NonNegFloat
    kinds: Annotated[list[StreamKind], Field(min_length=1)]


class PowerlineFaultConfig(QualityFaultConfig):
    frequency_hz: Literal[50, 60]


class MotionArtifactFaultConfig(QualityFaultConfig):
    """Ráfaga de baja frecuencia: tres senos con frecuencias uniformes en [band_low_hz, techo]."""

    band_low_hz: PositiveFloat
    # Techo de la banda; el efectivo es min(band_high_hz, max_fraction_of_rate x fs) para no acercarse a Nyquist.
    band_high_hz: PositiveFloat
    max_fraction_of_rate: Annotated[float, Field(gt=0, lt=0.5)]

    @model_validator(mode="after")
    def _band(self) -> Self:
        if self.band_low_hz >= self.band_high_hz:
            raise ValueError("band_low_hz must be < band_high_hz")
        return self


class WarmupFaultConfig(QualityFaultConfig):
    """Deriva que decae exponencialmente: tau = decay_fraction x duración del episodio."""

    decay_fraction: PositiveFloat


class QualityFaultsConfig(_Model):
    """Fallos inyectables. packet_gap, band_recovering y clock_uncertain no se inyectan: se derivan."""

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
    # Probabilidad por chunk de empezar una ráfaga de `burst_chunks` chunks perdidos (salto de seq).
    probability: Probability
    burst_chunks: IntRange
    # Ráfagas de al menos este tamaño ponen el stream en `stalled` y el DeviceStatus en `degraded`.
    stalled_after_chunks: PositiveInt
    # Score del primer chunk tras una pérdida (flag packet_gap).
    packet_gap_score: UnitInterval

    @model_validator(mode="after")
    def _burst(self) -> Self:
        if self.burst_chunks.min < 1:
            raise ValueError("burst_chunks.min must be >= 1")
        return self


class RebootConfig(_Model):
    # Reinicios espontáneos (Poisson) mientras el dispositivo está encendido.
    probability_per_hour: NonNegFloat
    offline_s: NonNegRange


class ClockFaultConfig(_Model):
    # received_at = instante real del final del chunk + latencia uniforme en latency_ms.
    latency_ms: NonNegRange
    # Deriva del reloj del dispositivo por arranque, uniforme en el intervalo (ppm, con signo).
    drift_ppm: FloatRange
    # uncertainty_ms publicado en cada ClockSync.
    sync_uncertainty_ms: NonNegRange
    # Error de reloj acumulado desde el último anclaje a partir del cual se marca clock_uncertain.
    uncertain_threshold_ms: PositiveFloat
    clock_uncertain_score: UnitInterval


class DeviceFaultsConfig(_Model):
    packet_loss: PacketLossConfig
    reboot: RebootConfig
    clock: ClockFaultConfig


class FaultsConfig(_Model):
    quality: QualityFaultsConfig
    device: DeviceFaultsConfig


# --- Contexto diario y autoinforme ------------------------------------------------------------------


class ExercisePlanItem(_Model):
    """Ejercicio general planificado por día (DailyContext.exercise), no la sesión de protocolo."""

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
    """Se toma con `probability` los días en que pain_stiffness >= pain_threshold."""

    name: Annotated[str, Field(min_length=1, max_length=80)]
    category: Literal["analgesic", "anti_inflammatory", "muscle_relaxant", "sleep_aid", "other"]
    dose_mg: PositiveFloat | None
    pain_threshold: NonNegFloat
    probability: Probability
    time_utc: TimeOfDay


class DailyContextConfig(_Model):
    # Plan por fase; toda fase usada en `segments` necesita entrada (puede ser lista vacía).
    exercise: dict[MissionPhase, list[ExercisePlanItem]]
    countermeasures: dict[MissionPhase, list[CountermeasurePlanItem]]
    medication: list[MedicationRule]


class PainLocationConfig(_Model):
    region: SpineRegion
    side: Literal["left", "right", "midline", "bilateral"]


class SelfReportConfig(_Model):
    """DailySelfReport diario (siempre, sin huecos de cumplimiento)."""

    time_utc: TimeOfDay
    time_jitter_s: NonNegFloat
    # NRS = clip(round(per_unit × pain_stiffness + N(0, nrs_noise_sd)), 0, 10).
    pain_nrs_per_unit: NonNegFloat
    stiffness_nrs_per_unit: NonNegFloat
    nrs_noise_sd: NonNegFloat
    # pain_nrs_worst_24h = pain_nrs + entero uniforme en el intervalo (con tope 10).
    worst_24h_extra_nrs: IntRange
    # Con pain_nrs >= este valor se declaran las pain_locations.
    pain_location_min_nrs: Nrs
    pain_locations: Annotated[list[PainLocationConfig], Field(min_length=1)]
    sleep_hours: Normal
    sleep_quality_nrs: Normal
    # Horas de sueño perdidas por punto de pain_nrs.
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


# --- Raíz -------------------------------------------------------------------------------------------


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

        # Cada chunk debe contener un número entero de muestras: en sesión con `signals.chunk_ms`, en el fondo
        # con `schedule.background.chunk_ms`.
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
    """Lee y valida un YAML de misión. Lanza pydantic.ValidationError si algo falta o sobra."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return MissionConfig.model_validate(data)
