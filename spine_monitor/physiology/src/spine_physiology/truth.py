"""Registros de ground truth: lo que de verdad pasó, para evaluar el motor contra el simulador.

No hay schema de contrato: cada registro es un dict JSON-ready con `type` (uno de `TruthType`),
`astronaut_id`, `day` y los datos propios; los de sesión llevan además `session_id` y `slot`
(None en vivo). Instantes en RFC 3339 UTC con `Z`, como los contratos.
- `latent_record`: latentes del día y la actividad (dosis) que los produjo.
- `device_record`: arranques (boot_id, boot_at, end_at, deriva) y reinicios del plan del día.
- `quality_faults_record`: episodios inyectados por stream (flag, inicio/fin UTC y en muestras, score).
- `lost_chunks_record`: chunks perdidos por stream (boot_id, seq, t0_device_ms, cuándo habrían llegado).
- `expected_features_record`: valores verdaderos de las features del catálogo
  (`contracts/catalog/feature-catalog.json`, con los placeholders expandidos) calculados del modelo
  SIN ruido de sensor ni fallos, respecto a la MVC y la longitud de reposo verdaderas:
  emg.{muscle}.{side}.rms_mv / rms_pct_mvc / peak_pct_mvc / rest_rms_mv / median_freq_hz /
  median_freq_slope_hz_per_rep, emg.{muscle}.symmetry_index_pct, imu.{spine_level}.{plane}.rom_deg /
  peak_angular_velocity_dps, exercise.rep_count / rep_duration_s_mean / rep_duration_cv_pct,
  stretch.{spine_region}.peak_elongation_mm / range_mm (exercise, longitud real del cordón durante las
  reps, sin histéresis) y elongation_mm / elongation_pct / rest_resistance_ohm (spine_check y fondo,
  media de la longitud real en los chunks emitidos).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import numpy as np
from scipy import signal as sp_signal
from spine_contracts import GravityEnvironment, StreamKind

from spine_physiology.config import ExerciseProtocol, MissionConfig
from spine_physiology.device import ChunkOutcome, DevicePlan
from spine_physiology.faults import FaultEpisode
from spine_physiology.sessions import SessionSynthesis, StreamSynthesis
from spine_physiology.signals import emg as emg_signal
from spine_physiology.signals import stretch as stretch_signal
from spine_physiology.signals.imu import effective_rom_deg
from spine_physiology.state import DailyActivity, DayInfo, LatentState

TruthType = Literal["latent_state", "device_events", "quality_faults", "lost_chunks", "expected_features"]

# Velocidad pico del perfil minimum-jerk normalizado (d/dτ de 10τ³ − 15τ⁴ + 6τ⁵ en τ = 1/2): matemática, no parámetro.
_MIN_JERK_PEAK_VELOCITY = 1.875

# Unidades de cada feature, las del catálogo de features del contrato.
UNITS: dict[str, str] = {
    "rms_mv": "mV",
    "rms_pct_mvc": "%",
    "peak_pct_mvc": "%",
    "rest_rms_mv": "mV",
    "median_freq_hz": "Hz",
    "median_freq_slope_hz_per_rep": "Hz/rep",
    "symmetry_index_pct": "%",
    "rom_deg": "deg",
    "peak_angular_velocity_dps": "deg/s",
    "rep_count": "count",
    "rep_duration_s_mean": "s",
    "rep_duration_cv_pct": "%",
    "peak_elongation_mm": "mm",
    "range_mm": "mm",
    "elongation_mm": "mm",
    "elongation_pct": "%",
    "rest_resistance_ohm": "ohm",
}


def iso(at: datetime) -> str:
    """RFC 3339 UTC con `Z` (mismo formato que los payloads de contrato)."""
    return at.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class TruthSession:
    """Sesión a la que se refiere un registro de ground truth."""

    astronaut_id: str
    day: int
    session_id: str
    slot: str | None  # None en vivo
    protocol_id: str
    gravity_environment: GravityEnvironment


def _base(kind: TruthType, astronaut_id: str, day: int) -> dict[str, Any]:
    return {"type": kind, "astronaut_id": astronaut_id, "day": day}


def _session_base(kind: TruthType, session: TruthSession) -> dict[str, Any]:
    return {**_base(kind, session.astronaut_id, session.day), "session_id": session.session_id, "slot": session.slot}


def latent_record(state: LatentState, day: DayInfo, activity: DailyActivity) -> dict[str, Any]:
    return {
        **_base("latent_state", state.astronaut_id, day.index),
        "date": day.date.isoformat(),
        "mission_day": day.mission_day,
        "phase": day.phase.value,
        "gravity_environment": day.gravity_environment.value,
        "latents": {
            "spine_elongation_mm": state.spine_elongation_mm,
            "muscle_deconditioning": state.muscle_deconditioning,
            "rom_motor_control": state.rom_motor_control,
            "pain_stiffness": state.pain_stiffness,
        },
        "activity": {
            "dose_ratio": activity.dose_ratio,
            "exercise_min": activity.exercise_min,
            "planned_exercise_min": activity.planned_exercise_min,
            "exercise": [{"modality": b.modality, "duration_min": b.duration_min} for b in activity.exercise],
            "countermeasures": [{"type": c.type, "duration_min": c.duration_min} for c in activity.countermeasures],
        },
    }


def device_record(plan: DevicePlan, day: DayInfo) -> dict[str, Any]:
    return {
        **_base("device_events", plan.astronaut_id, day.index),
        "device_id": plan.device_id,
        "power_on": iso(plan.power_on),
        "power_off": iso(plan.power_off) if plan.power_off is not None else None,
        "boots": [
            {"boot_id": b.boot_id, "boot_at": iso(b.boot_at), "end_at": iso(b.end_at) if b.end_at is not None else None, "drift_ppm": b.drift_ppm}
            for b in plan.boots
        ],
        "reboots": [{"at": iso(r.at), "offline_s": r.offline_s} for r in plan.reboots],
    }


def _visible(episode: FaultEpisode, stream: StreamSynthesis, active: Sequence[tuple[int, int]] | None) -> bool:
    if active is None:
        return True
    spc = stream.samples_per_chunk
    return any(episode.start_sample < k1 * spc and episode.end_sample > k0 * spc for k0, k1 in active)


def quality_faults_record(
    session: TruthSession,
    streams: Sequence[StreamSynthesis],
    *,
    active: Sequence[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Episodios inyectados por stream; con `active` (tramos de chunks emitidos) solo los que se ven."""
    out: dict[str, list[dict[str, Any]]] = {}
    for stream in streams:
        episodes = []
        for e in stream.episodes:
            if not _visible(e, stream, active):
                continue
            episodes.append({
                "flag": e.flag.value, "score": e.score, "amplitude": e.amplitude,
                "start": iso(stream.start + timedelta(seconds=e.start_sample / stream.rate_hz)),
                "end": iso(stream.start + timedelta(seconds=e.end_sample / stream.rate_hz)),
                "start_sample": e.start_sample, "end_sample": e.end_sample,
            })  # fmt: skip
        out[stream.stream_id] = episodes
    return {**_session_base("quality_faults", session), "streams": out}


def lost_chunks_record(session: TruthSession, lost: Mapping[str, Sequence[ChunkOutcome]]) -> dict[str, Any]:
    """Solo los outcomes perdidos (chunk is None), por stream."""
    return {
        **_session_base("lost_chunks", session),
        "streams": {
            stream_id: [
                {"boot_id": o.boot_id, "seq": o.seq, "t0_device_ms": o.t0_device_ms, "received_at": iso(o.received_at)}
                for o in outcomes
                if o.lost
            ]
            for stream_id, outcomes in lost.items()
        },
    }


def expected_features_record(session: TruthSession, features: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        **_session_base("expected_features", session),
        "protocol_id": session.protocol_id,
        "gravity_environment": session.gravity_environment.value,
        "features": [dict(f) for f in features],
    }


# --- Features esperadas -----------------------------------------------------------------------------


def _feature(feature_id: str, value: float, n: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"feature_id": feature_id, "value": float(value), "unit": UNITS[feature_id.rsplit(".", 1)[1]]}
    if n is not None:
        out["n"] = int(n)
    return out


def _rep_mask(protocol_reps: Sequence[Any], n: int, rate: float) -> np.ndarray:
    t = np.arange(n, dtype=np.float64) / rate
    mask = np.zeros(n, dtype=bool)
    for rep in protocol_reps:
        mask |= (t >= rep.start_s) & (t < rep.end_s)
    return mask


def _active_mask(stream: StreamSynthesis, active: Sequence[tuple[int, int]] | None) -> np.ndarray:
    n = len(stream.quality) * stream.samples_per_chunk
    if active is None:
        return np.ones(n, dtype=bool)
    mask = np.zeros(n, dtype=bool)
    for k0, k1 in active:
        mask[k0 * stream.samples_per_chunk : k1 * stream.samples_per_chunk] = True
    return mask


def band_median_hz(config: MissionConfig, fatigue: float, rate_hz: float) -> float:
    """Frecuencia mediana de la potencia del EMG sintético para un nivel de fatiga (respuesta |H|⁴ del
    filtro de fase cero de `signals.emg`, sin ruido de sensor)."""
    sos = emg_signal._band_sos(config.signals.emg, fatigue, rate_hz)
    freqs, h = sp_signal.sosfreqz(sos, worN=emg_signal._RESPONSE_GRID, fs=rate_hz)
    power = np.cumsum(np.abs(h) ** 4)
    return float(np.interp(power[-1] / 2.0, power, freqs))


def expected_features(
    config: MissionConfig,
    synthesis: SessionSynthesis,
    *,
    state: LatentState,
    active: Sequence[tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    """Features verdaderas de una sesión (ver docstring del módulo). `active`: tramos de chunks emitidos (fondo)."""
    spec = synthesis.spec
    protocol: ExerciseProtocol | None = spec.protocol if spec.reps else None
    features: list[dict[str, Any]] = []
    emg_pct: dict[tuple[str, str], float] = {}
    for stream in synthesis.streams:
        placement = config.device.stream(stream.stream_id).placement
        n = len(stream.quality) * stream.samples_per_chunk
        if stream.kind is StreamKind.EMG and placement.muscle is not None and placement.side is not None:
            prefix = f"emg.{placement.muscle.value}.{placement.side.value}"
            params = config.signals.emg
            mvc = stream.truth["mvc_rms_mv"]
            features.append(_feature(f"{prefix}.rest_rms_mv", float(np.hypot(mvc * params.rest_pct_mvc / 100.0, params.noise_rms_mv))))
            if protocol is None:
                continue
            mask = _rep_mask(spec.reps, n, stream.rate_hz)
            pct = stream.truth["pct_mvc"][mask]
            rms_pct = float(np.sqrt(np.mean(pct**2)))
            emg_pct[(placement.muscle.value, placement.side.value)] = rms_pct
            features.append(_feature(f"{prefix}.rms_mv", mvc * rms_pct / 100.0, len(spec.reps)))
            features.append(_feature(f"{prefix}.rms_pct_mvc", rms_pct, len(spec.reps)))
            features.append(_feature(f"{prefix}.peak_pct_mvc", float(pct.max()), len(spec.reps)))
            fatigue = stream.truth["fatigue"]
            medians = []
            for rep in spec.reps:
                mid = min(n - 1, round((rep.start_s + rep.end_s) / 2.0 * stream.rate_hz))
                medians.append(band_median_hz(config, float(fatigue[mid]), stream.rate_hz))
            features.append(_feature(f"{prefix}.median_freq_hz", float(np.mean(medians)), len(medians)))
            if len(medians) >= 2:
                slope = float(np.polyfit([r.index for r in spec.reps], medians, 1)[0])
                features.append(_feature(f"{prefix}.median_freq_slope_hz_per_rep", slope, len(medians)))
        elif stream.kind is StreamKind.IMU and placement.spine_level is not None and protocol is not None:
            prefix = f"imu.{placement.spine_level.value}.{protocol.plane.value}"
            rom = effective_rom_deg(protocol.rom_deg, state, config.signals.imu)
            features.append(_feature(f"{prefix}.rom_deg", rom, len(spec.reps)))
            peak = _MIN_JERK_PEAK_VELOCITY * rom / (protocol.rep_duration_s / 2.0)
            features.append(_feature(f"{prefix}.peak_angular_velocity_dps", peak, len(spec.reps)))
        elif stream.kind is StreamKind.STRETCH and placement.spine_region is not None:
            prefix = f"stretch.{placement.spine_region.value}"
            length = stream.truth["length_mm"]
            rest = stream.truth["rest_length_mm"]
            if protocol is not None:
                during = length[_rep_mask(spec.reps, n, stream.rate_hz)]
                features.append(_feature(f"{prefix}.peak_elongation_mm", float(during.max()) - rest, len(spec.reps)))
                features.append(_feature(f"{prefix}.range_mm", float(during.max() - during.min()), len(spec.reps)))
            else:
                values = length[_active_mask(stream, active)]
                if values.size == 0:
                    continue
                elongation = float(values.mean()) - rest
                features.append(_feature(f"{prefix}.elongation_mm", elongation, int(values.size)))
                features.append(_feature(f"{prefix}.elongation_pct", 100.0 * elongation / rest, int(values.size)))
                resistance = stretch_signal.length_to_resistance(np.array([values.mean()]), config.signals.stretch.curve_pairs())
                features.append(_feature(f"{prefix}.rest_resistance_ohm", float(resistance[0]), int(values.size)))
    for muscle in sorted({m for m, _ in emg_pct}):
        left, right = emg_pct.get((muscle, "left")), emg_pct.get((muscle, "right"))
        if left is not None and right is not None and left + right > 0:
            features.append(_feature(f"emg.{muscle}.symmetry_index_pct", 100.0 * abs(left - right) / ((left + right) / 2.0)))
    if protocol is not None:
        features.append(_feature("exercise.rep_count", len(spec.reps)))
        features.append(_feature("exercise.rep_duration_s_mean", protocol.rep_duration_s, len(spec.reps)))
        features.append(_feature("exercise.rep_duration_cv_pct", 0.0, len(spec.reps)))
    return features
