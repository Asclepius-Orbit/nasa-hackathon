"""Generador de misión: iterador perezoso de `Envelope`s, reproducible bit a bit por rama.

Dueño: C1. Debe implementar `MissionGenerator`:
- `iter_messages`: perfiles (al inicio, `at` = start_date 00:00 UTC) y luego día a día. Dentro de cada
  día, los mensajes de todos los astronautas mezclados por `at` (merge estable en el orden de `crew`):
  daily_context, sesiones (session, calibration, stream_chunk), device_status cada
  `device.status_interval_s` mientras está encendido (retained), clock_sync al arrancar y cada
  `device.clock_sync_interval_s`, self_report y ground_truth (latentes, eventos de dispositivo...).
- `day_messages` / `session_messages`: la misma salida que dentro de `iter_messages`, regenerada sola
  (un día o una sesión sueltos dan exactamente los mismos payloads que en la misión completa).
- `resimulate`: delega en `sessions.resimulate`.
- API en vivo para el gateway (D2): `live_session`, `live_calibration` y `live_background` sintetizan con
  la misma física que la misión pero sobre un `SimDevice` y unos ids que da el gateway.
Topics con `spine_contracts.topics` (stream/status/clock/ack). `source` siempre `simulator`.
"""

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Literal

from spine_contracts import GravityEnvironment

from spine_physiology.config import MissionConfig
from spine_physiology.device import SimDevice
from spine_physiology.envelope import Envelope
from spine_physiology.sessions import SessionPlan, SessionRef
from spine_physiology.state import DayInfo, LatentModel, MissionTimeline


class MissionGenerator:
    def __init__(self, config: MissionConfig, seed: int | None = None) -> None:
        """`seed=None` usa `config.seed`."""
        raise NotImplementedError

    @property
    def config(self) -> MissionConfig:
        raise NotImplementedError

    @property
    def seed(self) -> int:
        raise NotImplementedError

    @property
    def timeline(self) -> MissionTimeline:
        raise NotImplementedError

    @property
    def latents(self) -> LatentModel:
        raise NotImplementedError

    def iter_days(self) -> Iterator[DayInfo]:
        raise NotImplementedError

    def iter_messages(self) -> Iterator[Envelope]:
        raise NotImplementedError

    def profiles(self) -> Iterator[Envelope]:
        raise NotImplementedError

    def day_messages(self, day: int, astronaut_id: str | None = None) -> Iterator[Envelope]:
        """Mensajes de un día (de un astronauta, o de todos mezclados por `at`)."""
        raise NotImplementedError

    def sessions(self, day: int, astronaut_id: str) -> tuple[SessionPlan, ...]:
        raise NotImplementedError

    def session_messages(self, ref: SessionRef, *, gravity_environment: GravityEnvironment | None = None) -> Iterator[Envelope]:
        raise NotImplementedError

    def resimulate(self, ref: SessionRef, gravity_environment: GravityEnvironment) -> list[Envelope]:
        raise NotImplementedError

    # --- API en vivo (gateway) ----------------------------------------------------------------------

    def live_session(
        self,
        *,
        astronaut_id: str,
        day: int,
        device: SimDevice,
        session_id: str,
        session_type: Literal["exercise", "spine_check"],
        protocol_id: str,
        stream_ids: Sequence[str],
        start: datetime,
    ) -> Iterator[Envelope]:
        """Chunks (y ground truth) de una sesión pedida por `start_session`, en orden de `at`.

        Usa el protocolo `protocol_id` del config si existe (si no, un spine_check de duración abierta).
        `stream_ids` vacío = todos los del protocolo. El gateway corta el iterador al recibir
        `stop_session` o al pasar `max_duration_s`. RNG: `(astronaut_id, "live", session_id, ...)`.
        """
        raise NotImplementedError

    def live_calibration(
        self,
        *,
        astronaut_id: str,
        day: int,
        session_id: str,
        calibration_type: Literal["stretch", "emg_mvc", "imu"],
        stream_ids: Sequence[str],
        at: datetime,
    ) -> list[Envelope]:
        """Calibraciones (una por stream) de un `calibrate`; su calibration_id va en el ack `completed`."""
        raise NotImplementedError

    def live_background(
        self,
        *,
        astronaut_id: str,
        day: int,
        device: SimDevice,
        session_id: str,
        start: datetime,
    ) -> Iterator[Envelope]:
        """Chunks de fondo (IMU + cordón a las frecuencias reducidas) desde `start`, sin fin."""
        raise NotImplementedError
