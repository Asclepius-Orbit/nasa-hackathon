"""Sesiones: agenda del día, registro Session con eventos, calibraciones y síntesis de sus streams.

Dueño: C1. Debe implementar:
- `plan_day`: sesiones planificadas del día (las de `schedule.sessions` cuya fase y `every_n_days`
  tocan, con jitter de inicio, sin solaparse) y, si `schedule.background.enabled`, la sesión de fondo
  `spine_check` con `pauses` donde hay sesiones planificadas. Ids: `derive_uuid(seed, astronaut_id,
  day.index, slot)`. RNG: `(astronaut_id, day.index, slot, "plan")`.
- `run_session`: genera todo lo de una sesión en orden temporal: calibraciones que tocan (emg_mvc en cada
  `exercise`; stretch/imu según `calibration.*.every_n_days` y primer día de tramo), señales por stream
  (signals/*, con RNG `(astronaut_id, day.index, slot, stream_id, "signal")`), fallos de calidad
  (`(..., stream_id, "faults")`), troceo con el `SimDevice` del día (`(..., stream_id, "device")`), el
  Session con sus eventos (rep_start/rep_end, mvc_start/mvc_end, con device_ms y boot_id) y ground
  truth (fallos inyectados, chunks perdidos, features esperadas). `gravity_environment` permite forzar la
  gravedad (re-simulación A/B): solo cambian gravedad, accel y lo que dependa de ella (elongación
  diurna); todo lo demás sale idéntico bit a bit.
- `resimulate`: misma sesión, solo cambia `gravity_environment`.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from spine_contracts import GravityEnvironment

from spine_physiology.config import MissionConfig
from spine_physiology.device import SimDevice
from spine_physiology.envelope import Envelope
from spine_physiology.signals import Rep
from spine_physiology.state import DayInfo, LatentState

if TYPE_CHECKING:
    from spine_physiology.generator import MissionGenerator


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


def plan_day(config: MissionConfig, seed: int, astronaut_id: str, day: DayInfo) -> tuple[SessionPlan, ...]:
    """Sesiones del día ordenadas por inicio (el fondo, si existe, incluido)."""
    raise NotImplementedError


def run_session(
    config: MissionConfig,
    seed: int,
    plan: SessionPlan,
    *,
    day: DayInfo,
    state: LatentState,
    device: SimDevice,
    gravity_environment: GravityEnvironment | None = None,
) -> Iterator[Envelope]:
    """Envelopes de la sesión (session, calibration, stream_chunk, ground_truth) en orden de `at`.

    `device` debe venir con los seq ya consumidos por lo anterior del día (ver `SimDevice.advance`).
    `gravity_environment=None` usa la del día.
    """
    raise NotImplementedError


def resimulate(generator: MissionGenerator, ref: SessionRef, gravity_environment: GravityEnvironment) -> list[Envelope]:
    """La sesión `ref` regenerada con otra gravedad (par A/B exacto con `generator.session_messages(ref)`)."""
    raise NotImplementedError
