"""Sesiones: agenda del día, registro Session, calibraciones y síntesis de los streams de una sesión.

- `plan_day`: sesiones planificadas del día (las de `schedule.sessions` cuya fase y `every_n_days`
  tocan) con jitter de inicio (RNG `(astronaut_id, day.index, slot, "plan")`, redondeado a ms) y sin
  solaparse (una que empezaría antes de que acabe la anterior se retrasa hasta su fin). Si
  `schedule.background.enabled`, la sesión de fondo `spine_check` (IMU + cordón a las frecuencias
  reducidas) cubre la ventana `start_time_utc..end_time_utc` con `pauses` donde hay sesiones
  planificadas. Ids: `derive_uuid(seed, astronaut_id, day.index, slot)`. Toda sesión dura un número
  entero de chunks (`signals.chunk_ms`), así que todos sus streams comparten la rejilla de chunks.
- `active_chunks`: tramos de chunks que de verdad se emiten (el fondo pierde los chunks que tocan una
  pausa: reanuda en el primer chunk de su rejilla que empieza tras el fin de la sesión).
- `synthesize`: arrays publicables de cada stream (señal + fallos de calidad + derivados del cordón),
  más los intermedios deterministas que usa el ground truth. RNG por `spec.keys` (en misión
  `(astronaut_id, day.index, slot)`): `(..., stream_id, "signal")`, `(..., stream_id, "faults")` y
  `(..., "motion")` para las flexiones del fondo (compartidas por IMU y cordón: es el mismo tronco).
  Nada se arrastra entre sesiones: el cordón empieza cada sesión recuperado (residuo 0) salvo en los
  bloques en vivo, que encadenan el residuo del bloque anterior.
- Calibraciones: stretch e imu se registran los días con índice % `every_n_days` == 0 y el primer día
  de cada tramo, en la primera sesión del día que usa ese stream (o al encender el dispositivo si
  ninguna lo usa) y valen hasta la siguiente; emg_mvc al inicio de cada sesión `exercise`. El
  `calibration_id` vigente va en `Session.streams` y en los chunks del cordón (length_mm lo exige).
- `build_session`: registro Session (mission_context, streams con colocación y calibración,
  load_declared y planned_reps en `exercise`, eventos rep_start/rep_end con device_ms y boot_id, y en
  el fondo notas de pausa/reanudación).
- `resimulate`: la misma sesión con otra gravedad (par A/B exacto).

Decisiones de modelado (documentadas porque el config no las fija):
- La referencia de la histéresis del cordón es su longitud estática al inicio de la sesión (reposo +
  elongación latente − pérdida diurna, sin flexión): solo el estiramiento dinámico deja residuo y
  marca `band_recovering`; una columna elongada en reposo no "recupera" eternamente.
- No se emiten eventos mvc_start/mvc_end: el protocolo no tiene fase MVC en la señal y unos eventos
  sin la contracción correspondiente en el EMG engañarían al motor. La MVC es la calibración emg_mvc.
- En spine_check y fondo el plano del movimiento es el sagital (flexión del tronco); en reposo no influye.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from spine_contracts import (
    SCHEMA_VERSION,
    AnatomicalPlane,
    EmgMvcCalibration,
    GravityEnvironment,
    ImuCalibration,
    QualityFlag,
    Session,
    StreamKind,
    StretchCalibration,
)

from spine_physiology.config import (
    CrewMember,
    ExerciseProtocol,
    MissionConfig,
    SpineCheckProtocol,
)
from spine_physiology.device import DevicePlan
from spine_physiology.envelope import Envelope
from spine_physiology.faults import (
    ChunkQuality,
    FaultEpisode,
    apply_quality_faults,
    mask_quality,
    merge_quality,
    plan_quality_faults,
)
from spine_physiology.rng import Key, RngTree
from spine_physiology.signals import (
    Rep,
    exercise_duration_s,
    rep_schedule,
    sample_times,
)
from spine_physiology.signals import emg as emg_signal
from spine_physiology.signals import imu as imu_signal
from spine_physiology.signals import stretch as stretch_signal
from spine_physiology.state import GRAVITY_G, DayInfo, LatentState, MissionTimeline

if TYPE_CHECKING:
    from spine_physiology.generator import MissionGenerator

SOURCE = "simulator"
_US = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class SessionRef:
    """Identifica una sesión de la misión de forma estable: `slot` es el `name` de la agenda (o del fondo)."""

    astronaut_id: str
    day: int
    slot: str


@dataclass(frozen=True, slots=True)
class SessionPlan:
    ref: SessionRef
    session_id: str
    session_type: Literal["exercise", "spine_check"]
    protocol_id: str
    start: datetime
    end: datetime
    stream_ids: tuple[str, ...]
    reps: tuple[Rep, ...]  # vacío en spine_check
    background: bool
    # Solo en el fondo: intervalos [inicio, fin) ocupados por sesiones planificadas (sin chunks del fondo).
    pauses: tuple[tuple[datetime, datetime], ...]


# --- Agenda -----------------------------------------------------------------------------------------


def chunk_delta(config: MissionConfig) -> timedelta:
    return timedelta(milliseconds=config.signals.chunk_ms)


def chunks_for(config: MissionConfig, duration_s: float) -> int:
    """Chunks enteros que cubren `duration_s` (al menos uno), igual que `signals.n_samples`."""
    return max(1, math.ceil(duration_s * 1000.0 / config.signals.chunk_ms - 1e-9))


def protocol_duration_s(protocol: ExerciseProtocol | SpineCheckProtocol) -> float:
    return exercise_duration_s(protocol) if isinstance(protocol, ExerciseProtocol) else protocol.duration_s


def n_chunks(config: MissionConfig, plan: SessionPlan) -> int:
    return (plan.end - plan.start) // chunk_delta(config)


def stream_rate(config: MissionConfig, stream_id: str, *, background: bool) -> float:
    """Frecuencia de un stream: la de su tipo, o la reducida del fondo."""
    kind = config.device.stream(stream_id).stream_kind
    if not background:
        return config.signals.rate(kind)
    rates = config.schedule.background.sample_rates_hz
    if kind is StreamKind.IMU:
        return rates.imu
    if kind is StreamKind.STRETCH:
        return rates.stretch
    raise ValueError(f"background streams are IMU and cord only, got {kind.value} stream {stream_id}")


def plan_day(config: MissionConfig, seed: int, astronaut_id: str, day: DayInfo) -> tuple[SessionPlan, ...]:
    """Sesiones del día ordenadas por inicio (el fondo, si existe, incluido)."""
    tree = RngTree(seed)
    chunk = chunk_delta(config)
    candidates: list[tuple[datetime, int, Any, ExerciseProtocol | SpineCheckProtocol]] = []
    for order, slot in enumerate(config.schedule.sessions):
        if day.phase not in slot.phases or day.index % slot.every_n_days:
            continue
        rng = tree.rng(astronaut_id, day.index, slot.name, "plan")
        jitter_ms = round(float(rng.normal(0.0, slot.start_jitter_s)) * 1000.0)
        candidates.append((day.at(slot.time_utc) + timedelta(milliseconds=jitter_ms), order, slot, config.protocol(slot.protocol_id)))
    candidates.sort(key=lambda c: (c[0], c[1]))

    plans: list[SessionPlan] = []
    previous_end: datetime | None = None
    for start, _, slot, protocol in candidates:
        if previous_end is not None and start < previous_end:
            start = previous_end
        end = start + chunks_for(config, protocol_duration_s(protocol)) * chunk
        exercise = isinstance(protocol, ExerciseProtocol)
        plans.append(
            SessionPlan(
                ref=SessionRef(astronaut_id, day.index, slot.name),
                session_id=tree.uuid(astronaut_id, day.index, slot.name),
                session_type=protocol.session_type,
                protocol_id=slot.protocol_id,
                start=start,
                end=end,
                stream_ids=tuple(protocol.streams),
                reps=rep_schedule(protocol) if exercise else (),
                background=False,
                pauses=(),
            )
        )
        previous_end = end

    background = config.schedule.background
    if background.enabled:
        start = day.at(background.start_time_utc)
        window_s = (day.at(background.end_time_utc) - start).total_seconds()
        end = start + chunks_for(config, window_s) * chunk
        pauses = tuple((max(p.start, start), min(p.end, end)) for p in plans if p.start < end and p.end > start)
        plans.append(
            SessionPlan(
                ref=SessionRef(astronaut_id, day.index, background.name),
                session_id=tree.uuid(astronaut_id, day.index, background.name),
                session_type="spine_check",
                protocol_id=background.protocol_id,
                start=start,
                end=end,
                stream_ids=tuple(background.streams),
                reps=(),
                background=True,
                pauses=pauses,
            )
        )
    plans.sort(key=lambda p: (p.start, p.background))
    return tuple(plans)


def active_chunks(config: MissionConfig, plan: SessionPlan) -> tuple[tuple[int, int], ...]:
    """Tramos [k0, k1) de chunks emitidos. Fondo: sin los chunks que acaban después del inicio de una
    pausa y empiezan antes de su fin."""
    total = n_chunks(config, plan)
    if not plan.pauses:
        return ((0, total),)
    chunk_us = config.signals.chunk_ms * 1000
    blocked: list[tuple[int, int]] = []
    for pause_start, pause_end in plan.pauses:
        a = max(0, ((pause_start - plan.start) // _US) // chunk_us)
        b = min(total, -(-((pause_end - plan.start) // _US) // chunk_us))
        if b > a:
            blocked.append((a, b))
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for a, b in sorted(blocked):
        if a > cursor:
            ranges.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < total:
        ranges.append((cursor, total))
    return tuple(ranges)


# --- Calibraciones ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalibrationRecord:
    """Una calibración emitida (o vigente) y lo que hace falta para aplicarla."""

    calibration_id: str
    stream_id: str
    calibration_type: Literal["stretch", "emg_mvc", "imu"]
    created_at: datetime
    session_id: str | None  # sesión en la que se registró (None: al encender, fuera de sesión)
    day: int  # índice de día en que se registró
    model: StretchCalibration | EmgMvcCalibration | ImuCalibration
    curve: tuple[tuple[float, float], ...] | None  # solo stretch: (resistance_ohm, length_mm)


def calibration_day(timeline: MissionTimeline, every_n_days: int, day: int) -> int:
    """Último día <= `day` con calibración programada (índice % every_n_days == 0 o primer día de tramo)."""
    for k in range(day, -1, -1):
        info = timeline.day(k)
        if k % every_n_days == 0 or info.day_in_segment == 0:
            return k
    raise ValueError("day 0 always calibrates")  # pragma: no cover


def _base(config: MissionConfig, member: CrewMember, *, calibration_id: str, stream_id: str, session_id: str | None, created_at: datetime) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "calibration_id": calibration_id,
        "astronaut_id": member.astronaut_id,
        "device_id": member.device_id,
        "stream_id": stream_id,
        "created_at": created_at,
        "source": SOURCE,
    }
    if session_id is not None:
        base["session_id"] = session_id
    return base


def stretch_calibration(
    config: MissionConfig,
    rng: np.random.Generator,
    member: CrewMember,
    *,
    calibration_id: str,
    stream_id: str,
    session_id: str | None,
    created_at: datetime,
    day: int,
) -> CalibrationRecord:
    """Curva medida (ruidosa) de la curva real del cordón; el primer punto es el reposo."""
    rest = member.baseline.cord_rest_length_mm
    curve = stretch_signal.measured_curve(rng, config.signals.stretch, config.calibration.stretch, rest_length_mm=rest)
    cfg = config.calibration.stretch
    model = StretchCalibration.model_validate({
        **_base(config, member, calibration_id=calibration_id, stream_id=stream_id, session_id=session_id, created_at=created_at),
        "calibration_type": "stretch", "rest_resistance_ohm": curve[0][0], "rest_length_mm": rest,
        "recovery_wait_s": cfg.recovery_wait_s, "temperature_c": cfg.temperature_c,
        "curve": {"model": "piecewise_linear", "points": [{"resistance_ohm": r, "length_mm": length} for r, length in curve]},
    })  # fmt: skip
    return CalibrationRecord(calibration_id, stream_id, "stretch", created_at, session_id, day, model, curve)


def imu_calibration(
    config: MissionConfig,
    rng: np.random.Generator,
    member: CrewMember,
    *,
    calibration_id: str,
    stream_id: str,
    session_id: str | None,
    created_at: datetime,
    day: int,
) -> CalibrationRecord:
    """Bias estimado = bias real de `signals.imu` + error de medida; rangos y montaje del config."""
    imu = config.signals.imu
    cfg = config.calibration.imu
    gyro = rng.normal(0.0, cfg.gyro_bias_noise_dps, 3)
    accel = rng.normal(0.0, cfg.accel_bias_noise_g, 3)
    gb, ab = imu.gyro_bias_dps, imu.accel_bias_g
    level = config.device.stream(stream_id).placement.spine_level
    model = ImuCalibration.model_validate({
        **_base(config, member, calibration_id=calibration_id, stream_id=stream_id, session_id=session_id, created_at=created_at),
        "calibration_type": "imu", "spine_level": level.value if level is not None else None,
        "accel_range_g": imu.accel_range_g, "gyro_range_dps": imu.gyro_range_dps,
        "gyro_bias_dps": {"x": gb.x + float(gyro[0]), "y": gb.y + float(gyro[1]), "z": gb.z + float(gyro[2])},
        "accel_bias_g": {"x": ab.x + float(accel[0]), "y": ab.y + float(accel[1]), "z": ab.z + float(accel[2])},
        "mounting": {"sensor_x": imu.mounting.sensor_x.value, "sensor_y": imu.mounting.sensor_y.value, "sensor_z": imu.mounting.sensor_z.value},
        "static_duration_s": cfg.static_duration_s,
    })  # fmt: skip
    return CalibrationRecord(calibration_id, stream_id, "imu", created_at, session_id, day, model, None)


def emg_mvc_calibration(
    config: MissionConfig,
    rng: np.random.Generator,
    member: CrewMember,
    *,
    calibration_id: str,
    stream_id: str,
    session_id: str | None,
    created_at: datetime,
    day: int,
) -> CalibrationRecord:
    """MVC medida = la real × (1 + N(0, measurement_noise_rel)); reposo = RMS del tono de reposo y el ruido."""
    cfg = config.calibration.emg_mvc
    emg = config.signals.emg
    true_mvc = member.baseline.emg_mvc_rms_mv[stream_id]
    measured = abs(true_mvc * (1.0 + float(rng.normal(0.0, cfg.measurement_noise_rel))))
    placement = config.device.stream(stream_id).placement
    model = EmgMvcCalibration.model_validate({
        **_base(config, member, calibration_id=calibration_id, stream_id=stream_id, session_id=session_id, created_at=created_at),
        "calibration_type": "emg_mvc",
        "muscle": placement.muscle.value if placement.muscle is not None else None,
        "side": placement.side.value if placement.side is not None else None,
        "mvc_rms_mv": measured, "window_ms": cfg.window_ms, "n_trials": cfg.n_trials, "signal_kind": "raw",
        "resting_baseline_mv": math.hypot(true_mvc * emg.rest_pct_mvc / 100.0, emg.noise_rms_mv),
    })  # fmt: skip
    return CalibrationRecord(calibration_id, stream_id, "emg_mvc", created_at, session_id, day, model, None)


_SCHEDULED = {StreamKind.STRETCH: ("stretch", stretch_calibration), StreamKind.IMU: ("imu", imu_calibration)}


def scheduled_calibrations(
    config: MissionConfig,
    seed: int,
    timeline: MissionTimeline,
    astronaut_id: str,
    day: DayInfo,
) -> dict[str, CalibrationRecord]:
    """Calibraciones stretch/imu vigentes el día `day`, por stream (registradas en su día de calibración).

    RNG `(astronaut_id, día_de_calibración, "calibration", tipo, stream_id)`; id con las mismas claves.
    """
    tree = RngTree(seed)
    member = config.member(astronaut_id)
    out: dict[str, CalibrationRecord] = {}
    for stream in config.device.streams:
        if stream.stream_kind not in _SCHEDULED:
            continue
        cal_type, build = _SCHEDULED[stream.stream_kind]
        every = config.calibration.stretch.every_n_days if cal_type == "stretch" else config.calibration.imu.every_n_days
        cal_index = calibration_day(timeline, every, day.index)
        cal_day = timeline.day(cal_index)
        host = next((p for p in plan_day(config, seed, astronaut_id, cal_day) if stream.stream_id in p.stream_ids), None)
        keys: tuple[Key, ...] = (astronaut_id, cal_index, "calibration", cal_type, stream.stream_id)
        out[stream.stream_id] = build(
            config, tree.rng(*keys), member, calibration_id=tree.uuid(*keys), stream_id=stream.stream_id,
            session_id=host.session_id if host is not None else None,
            created_at=host.start if host is not None else cal_day.at(config.device.power_on_time_utc), day=cal_index,
        )  # fmt: skip
    return out


def session_mvc_calibrations(config: MissionConfig, seed: int, plan: SessionPlan) -> dict[str, CalibrationRecord]:
    """emg_mvc de cada stream EMG de una sesión `exercise`, al inicio. RNG `(astronaut_id, día, slot, stream_id, "emg_mvc")`."""
    if plan.session_type != "exercise":
        return {}
    tree = RngTree(seed)
    member = config.member(plan.ref.astronaut_id)
    out: dict[str, CalibrationRecord] = {}
    for stream_id in plan.stream_ids:
        if config.device.stream(stream_id).stream_kind is not StreamKind.EMG:
            continue
        keys: tuple[Key, ...] = (plan.ref.astronaut_id, plan.ref.day, plan.ref.slot, stream_id, "emg_mvc")
        out[stream_id] = emg_mvc_calibration(
            config, tree.rng(*keys), member, calibration_id=tree.uuid(*keys), stream_id=stream_id,
            session_id=plan.session_id, created_at=plan.start, day=plan.ref.day,
        )  # fmt: skip
    return out


# --- Registro Session -------------------------------------------------------------------------------


def _event(event_type: str, at: datetime, device_plan: DevicePlan, **extra: Any) -> dict[str, Any]:
    event: dict[str, Any] = {"event_type": event_type, "timestamp": at}
    epoch = device_plan.epoch_at(at)
    if epoch is not None:
        event["device_ms"] = epoch.device_ms(at)
        event["boot_id"] = epoch.boot_id
    event.update(extra)
    return event


def build_session(
    config: MissionConfig,
    plan: SessionPlan,
    *,
    day: DayInfo,
    device_plan: DevicePlan,
    gravity_environment: GravityEnvironment,
    calibration_ids: Mapping[str, str],
) -> Session:
    """Registro Session completo (con `ended_at`): se emite al empezar la sesión."""
    member = config.member(plan.ref.astronaut_id)
    streams = []
    for stream_id in plan.stream_ids:
        cfg = config.device.stream(stream_id)
        declaration: dict[str, Any] = {
            "stream_id": stream_id,
            "stream_kind": cfg.stream_kind.value,
            "placement": cfg.placement.model_dump(mode="json", exclude_none=True),
            "sample_rate_hz": stream_rate(config, stream_id, background=plan.background),
        }
        if stream_id in calibration_ids:
            declaration["calibration_id"] = calibration_ids[stream_id]
        streams.append(declaration)

    events: list[dict[str, Any]] = []
    for rep in plan.reps:
        events.append(_event("rep_start", plan.start + timedelta(seconds=rep.start_s), device_plan, rep_index=rep.index))
        events.append(_event("rep_end", plan.start + timedelta(seconds=rep.end_s), device_plan, rep_index=rep.index))
    if plan.background:
        chunk = chunk_delta(config)
        ranges = active_chunks(config, plan)
        total = n_chunks(config, plan)
        for k0, k1 in ranges:
            if k0 > 0:
                events.append(_event("note", plan.start + k0 * chunk, device_plan, note="background resumed"))
            if k1 < total:
                events.append(_event("note", plan.start + k1 * chunk, device_plan, note="background paused"))

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": plan.session_id,
        "astronaut_id": plan.ref.astronaut_id,
        "device_id": member.device_id,
        "session_type": plan.session_type,
        "protocol_id": plan.protocol_id,
        "source": SOURCE,
        "started_at": plan.start,
        "ended_at": plan.end,
        "end_reason": "completed",
        "mission_context": {"mission_day": day.mission_day, "phase": day.phase.value, "gravity_environment": gravity_environment.value},
        "streams": streams,
        "events": events,
    }
    if plan.session_type == "exercise":
        protocol = config.protocol(plan.protocol_id)
        assert isinstance(protocol, ExerciseProtocol)
        payload["load_declared"] = protocol.load.model_dump(mode="json", exclude_none=True)
        payload["planned_reps"] = protocol.planned_reps
    return Session.model_validate(payload)


# --- Síntesis ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SynthSpec:
    """Lo que hace falta para sintetizar los streams de una sesión (o de un bloque en vivo)."""

    keys: tuple[Key, ...]  # prefijo de las ramas de RNG
    start: datetime  # instante real de la muestra 0 de todos los streams
    n_chunks: int
    stream_ids: tuple[str, ...]
    background: bool  # frecuencias reducidas y flexiones espontáneas
    reps: tuple[Rep, ...]
    protocol: ExerciseProtocol | None  # %MVC, ROM y plano de las reps (None: reposo)
    gravity_environment: GravityEnvironment
    awake_from: datetime  # ventana encendido -> apagado para la pérdida diurna del cordón
    awake_to: datetime
    initial_residual_mm: Mapping[str, float] = field(default_factory=dict)  # por stream de cordón (vivo)


@dataclass(frozen=True, slots=True)
class StreamSynthesis:
    """Arrays publicables de un stream y sus intermedios deterministas (sin ruido) para el ground truth."""

    stream_id: str
    kind: StreamKind
    rate_hz: float
    samples_per_chunk: int
    start: datetime
    channels: dict[str, np.ndarray]  # claves que espera `SimDevice.chunks`
    quality: list[ChunkQuality]  # una por chunk
    calibration_id: str | None
    episodes: tuple[FaultEpisode, ...]
    truth: dict[str, Any]  # emg: pct_mvc, fatigue, mvc_rms_mv | imu: angle_deg | stretch: length_mm, residual_mm, rest_length_mm, angle_deg

    def slice(self, k0: int, k1: int) -> tuple[dict[str, np.ndarray], list[ChunkQuality]]:
        """Canales y quality de los chunks [k0, k1)."""
        a, b = k0 * self.samples_per_chunk, k1 * self.samples_per_chunk
        return {name: arr[a:b] for name, arr in self.channels.items()}, self.quality[k0:k1]

    def chunk_start(self, k: int) -> datetime:
        return self.start + timedelta(microseconds=round(k * self.samples_per_chunk / self.rate_hz * 1e6))


@dataclass(frozen=True, slots=True)
class SessionSynthesis:
    spec: SynthSpec
    streams: tuple[StreamSynthesis, ...]

    def stream(self, stream_id: str) -> StreamSynthesis:
        for s in self.streams:
            if s.stream_id == stream_id:
                return s
        raise KeyError(stream_id)

    @property
    def final_residual_mm(self) -> dict[str, float]:
        """Residuo de histéresis al final de cada stream de cordón (continuidad entre bloques en vivo)."""
        return {s.stream_id: float(s.truth["residual_mm"][-1]) for s in self.streams if s.kind is StreamKind.STRETCH and s.truth["residual_mm"].size}


def _angle(config: MissionConfig, tree: RngTree, spec: SynthSpec, state: LatentState, n: int, rate: float) -> np.ndarray:
    if spec.background:
        bg = config.schedule.background
        return imu_signal.background_angle_profile(
            tree.rng(*spec.keys, "motion"), n_samples=n, rate_hz=rate,
            bends_per_hour=bg.bends_per_hour, bend_rom_deg=bg.bend_rom_deg, bend_duration_s=bg.bend_duration_s,
        )  # fmt: skip
    if spec.protocol is not None and spec.reps:
        rom = imu_signal.effective_rom_deg(spec.protocol.rom_deg, state, config.signals.imu)
        return imu_signal.angle_profile(spec.reps, rom_deg=rom, n_samples=n, rate_hz=rate)
    return np.zeros(n, dtype=np.float64)


def synthesize(
    config: MissionConfig,
    seed: int,
    spec: SynthSpec,
    *,
    member: CrewMember,
    state: LatentState,
    calibrations: Mapping[str, CalibrationRecord],
) -> SessionSynthesis:
    """Señal, fallos de calidad y derivados de cada stream de `spec` (sin trocear)."""
    tree = RngTree(seed)
    signals = config.signals
    gravity_g = GRAVITY_G[spec.gravity_environment]
    plane = spec.protocol.plane if spec.protocol is not None else AnatomicalPlane.SAGITTAL
    reps = spec.reps if spec.protocol is not None else ()
    streams: list[StreamSynthesis] = []
    for stream_id in spec.stream_ids:
        kind = config.device.stream(stream_id).stream_kind
        rate = stream_rate(config, stream_id, background=spec.background)
        spc = signals.samples_per_chunk(rate)
        n = spec.n_chunks * spc
        rng = tree.rng(*spec.keys, stream_id, "signal")
        truth: dict[str, Any] = {}
        calibration = calibrations.get(stream_id)
        if kind is StreamKind.EMG:
            params = signals.emg
            mvc = member.baseline.emg_mvc_rms_mv[stream_id]
            peak = emg_signal.effective_pct_mvc(spec.protocol.pct_mvc, state.muscle_deconditioning, params) if spec.protocol is not None else params.rest_pct_mvc
            pct = emg_signal.activation_profile(reps, peak_pct_mvc=peak, rest_pct_mvc=params.rest_pct_mvc, n_samples=n, rate_hz=rate)
            per_rep = emg_signal.effective_fatigue_per_rep(state.muscle_deconditioning, params)
            fatigue = emg_signal.fatigue_profile(reps, fatigue_per_rep=per_rep, n_samples=n, rate_hz=rate)
            raw = {"emg": emg_signal.synth_emg(rng, params, mvc_rms_mv=mvc, pct_mvc=pct, fatigue=fatigue, rate_hz=rate)}
            truth = {"pct_mvc": pct, "fatigue": fatigue, "mvc_rms_mv": mvc}
        elif kind is StreamKind.IMU:
            angle = _angle(config, tree, spec, state, n, rate)
            tremor = signals.imu.tremor_dps_per_unit * state.rom_motor_control
            raw = imu_signal.synth_imu(rng, signals.imu, angle_deg=angle, plane=plane, gravity_g=gravity_g, rate_hz=rate, tremor_dps=tremor)
            truth = {"angle_deg": angle}
        else:
            params = signals.stretch
            angle = _angle(config, tree, spec, state, n, rate)
            span_s = (spec.awake_to - spec.awake_from).total_seconds()
            awake = ((spec.start - spec.awake_from).total_seconds() + sample_times(n, rate)) / span_s
            offset = stretch_signal.diurnal_loss_mm(params, gravity_g=gravity_g, awake_fraction=awake)
            rest = member.baseline.cord_rest_length_mm
            length = stretch_signal.cord_length(params, rest_length_mm=rest, elongation_mm=state.spine_elongation_mm, angle_deg=angle, offset_mm=offset)
            static = float(length[0] - params.flexion_mm_per_deg * abs(angle[0])) if n else rest
            cord = stretch_signal.synth_stretch(
                rng, params, length_mm=length, rest_length_mm=static, rate_hz=rate,
                initial_residual_mm=spec.initial_residual_mm.get(stream_id, 0.0),
            )  # fmt: skip
            raw = {"adc_counts": cord.adc_counts}
            truth = {"length_mm": length, "residual_mm": cord.residual_mm, "rest_length_mm": rest, "angle_deg": angle}

        faults_rng = tree.rng(*spec.keys, stream_id, "faults")
        episodes = plan_quality_faults(faults_rng, config.faults.quality, stream_id=stream_id, kind=kind, n_samples=n, rate_hz=rate, samples_per_chunk=spc)
        channels, quality = apply_quality_faults(
            faults_rng, episodes, kind=kind, channels=raw, rate_hz=rate, samples_per_chunk=spc, signals=signals, faults=config.faults.quality
        )
        calibration_id: str | None = None
        if kind is StreamKind.STRETCH:
            params = signals.stretch
            if calibration is not None and calibration.curve is not None:
                resistance, length_mm = stretch_signal.derive(channels["adc_counts"], params, calibration.curve)
                channels["length_mm"] = length_mm
                calibration_id = calibration.calibration_id
            else:
                resistance = stretch_signal.adc_to_resistance(channels["adc_counts"], params)
            channels["resistance_ohm"] = resistance
            recovering = mask_quality(
                truth["residual_mm"] >= params.band_recovering_threshold_mm, kind=kind, key="length_mm",
                flag=QualityFlag.BAND_RECOVERING, score=params.band_recovering_score, samples_per_chunk=spc,
            )  # fmt: skip
            quality = merge_quality(quality, recovering)
        streams.append(StreamSynthesis(stream_id, kind, rate, spc, spec.start, channels, quality, calibration_id, episodes, truth))
    return SessionSynthesis(spec, tuple(streams))


def mission_spec(
    config: MissionConfig,
    plan: SessionPlan,
    *,
    day: DayInfo,
    gravity_environment: GravityEnvironment,
) -> SynthSpec:
    """SynthSpec de una sesión de la misión (RNG `(astronaut_id, day.index, slot, ...)`)."""
    protocol = None
    if plan.session_type == "exercise" and not plan.background:
        candidate = config.protocol(plan.protocol_id)
        protocol = candidate if isinstance(candidate, ExerciseProtocol) else None
    return SynthSpec(
        keys=(plan.ref.astronaut_id, plan.ref.day, plan.ref.slot),
        start=plan.start,
        n_chunks=n_chunks(config, plan),
        stream_ids=plan.stream_ids,
        background=plan.background,
        reps=plan.reps,
        protocol=protocol,
        gravity_environment=gravity_environment,
        awake_from=day.at(config.device.power_on_time_utc),
        awake_to=day.at(config.device.power_off_time_utc),
    )


def session_calibrations(
    config: MissionConfig,
    seed: int,
    plan: SessionPlan,
    scheduled: Mapping[str, CalibrationRecord],
) -> tuple[dict[str, CalibrationRecord], list[CalibrationRecord]]:
    """(calibración vigente por stream de la sesión, calibraciones que se registran en esta sesión)."""
    in_force: dict[str, CalibrationRecord] = {s: scheduled[s] for s in plan.stream_ids if s in scheduled}
    mvc = session_mvc_calibrations(config, seed, plan)
    in_force.update(mvc)
    recorded = [r for r in scheduled.values() if r.session_id == plan.session_id] + list(mvc.values())
    return in_force, recorded


def resimulate(generator: MissionGenerator, ref: SessionRef, gravity_environment: GravityEnvironment) -> list[Envelope]:
    """La sesión `ref` regenerada con otra gravedad (par A/B exacto con `generator.session_messages(ref)`)."""
    return list(generator.session_messages(ref, gravity_environment=gravity_environment))


__all__ = [
    "CalibrationRecord",
    "SessionPlan",
    "SessionRef",
    "SessionSynthesis",
    "StreamSynthesis",
    "SynthSpec",
    "active_chunks",
    "build_session",
    "plan_day",
    "resimulate",
    "scheduled_calibrations",
    "session_calibrations",
    "synthesize",
]
