"""Wearable simulado: arranques, reloj monotónico, troceo en chunks, DeviceStatus y ClockSync.

Dueño: B4. Debe implementar:
- `plan_device_day`: un arranque al encender (`device.power_on_time_utc`), reinicios espontáneos
  (`faults.plan_reboots`) con su tiempo offline y apagado a `power_off_time_utc`. Cada arranque tiene un
  boot_id derivado (`rng.derive_boot_id(seed, astronaut_id, day.index, n)`, n = 0, 1, ...) y una deriva
  de reloj propia. RNG: `(astronaut_id, day.index, "device")`. Plan regenerable solo por (astronauta, día).
- `SimDevice.chunks`: trocea arrays de sesión en chunks de `signals.chunk_ms` (contratos EmgChunk /
  ImuChunk / StretchChunk de spine_contracts, `source: simulator`): seq por (boot_id, stream) desde 0 y
  sin huecos salvo pérdidas (que también consumen seq), `t0_device_ms` del reloj del arranque (con
  deriva), `received_at` = instante real del final del chunk + latencia, metadatos (`acquisition`,
  `filter`, `config`, `circuit`) del config, `calibration_id` si hay length_mm, quality por chunk
  (+ `packet_gap` en el primer chunk tras una pérdida, + `clock_uncertain` si el error de reloj desde el
  último anclaje supera el umbral). Chunks que empiezan con el dispositivo apagado/reiniciando no
  existen (ni consumen seq); tras un reinicio seq y t0 vuelven a 0 con el nuevo boot_id. Las pérdidas
  salen como `ChunkOutcome(chunk=None)` para el ground truth.
- `SimDevice.advance`: consume seq como si se hubiera emitido (para regenerar una sesión suelta con los
  seq que tendría dentro del día completo).
- `status` (DeviceStatus: ok/degraded por batería o stream stalled/offline, batería, temperatura,
  link, streams con last_seq) y `clock_sync` (ClockSync `method: simulated`, None si está apagado).
Todos los modelos se construyen sin pasar None a campos opcionales (ver `spine_contracts.dump`).
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from spine_contracts import ClockSync, DeviceStatus, EmgChunk, ImuChunk, StreamKind, StretchChunk

from spine_physiology.config import MissionConfig
from spine_physiology.faults import ChunkQuality, RebootEvent
from spine_physiology.state import DayInfo


@dataclass(frozen=True, slots=True)
class BootEpoch:
    """Un arranque: reloj monotónico desde `boot_at` hasta `end_at` (apagado o caída por reinicio)."""

    boot_id: int
    boot_at: datetime  # instante real (UTC) en que device_ms = 0
    end_at: datetime | None  # None = abierto (gateway)
    drift_ppm: float  # el reloj del dispositivo avanza (1 + drift_ppm × 1e-6) veces el real

    def contains(self, at: datetime) -> bool:
        raise NotImplementedError

    def device_ms(self, at: datetime) -> int:
        """Lectura del reloj del dispositivo en el instante real `at` (ms enteros, >= 0)."""
        raise NotImplementedError

    def real_at(self, device_ms: float) -> datetime:
        """Instante real en que el reloj del dispositivo marca `device_ms`."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class DevicePlan:
    """Arranques y caídas de un dispositivo en una ventana (un día, o abierta en el gateway)."""

    astronaut_id: str
    device_id: str
    power_on: datetime
    power_off: datetime | None
    boots: tuple[BootEpoch, ...]  # ordenados; huecos entre ellos = offline
    reboots: tuple[RebootEvent, ...]

    def epoch_at(self, at: datetime) -> BootEpoch | None:
        """Arranque activo en `at`, o None si el dispositivo está apagado o reiniciando."""
        raise NotImplementedError


def plan_device_day(config: MissionConfig, seed: int, astronaut_id: str, day: DayInfo) -> DevicePlan:
    raise NotImplementedError


def plan_device_live(config: MissionConfig, seed: int, astronaut_id: str, *, boot_at: datetime, horizon: timedelta) -> DevicePlan:
    """Plan del gateway interactivo: arranca en `boot_at`, reinicios planificados hasta `boot_at + horizon`
    (el gateway pide otro plan al agotarlo). RNG `(astronaut_id, "live", boot_at.isoformat(), "device")`.
    """
    raise NotImplementedError


type StreamChunkModel = EmgChunk | ImuChunk | StretchChunk


@dataclass(frozen=True, slots=True)
class ChunkOutcome:
    """Un hueco de chunk: emitido (`chunk`) o perdido por el enlace (`chunk is None`, el seq se consume)."""

    stream_id: str
    stream_kind: StreamKind
    boot_id: int
    seq: int
    t0_device_ms: int
    received_at: datetime  # para un perdido: cuándo habría llegado
    chunk: StreamChunkModel | None

    @property
    def lost(self) -> bool:
        return self.chunk is None


class SimDevice:
    """Estado mutable de un dispositivo durante un plan: seq por (boot_id, stream), batería, último anclaje.

    Las sesiones de un día se procesan en orden temporal sobre la misma instancia.
    """

    def __init__(self, config: MissionConfig, plan: DevicePlan) -> None:
        raise NotImplementedError

    @property
    def plan(self) -> DevicePlan:
        raise NotImplementedError

    def next_seq(self, boot_id: int, stream_id: str) -> int:
        raise NotImplementedError

    def advance(self, stream_id: str, *, start: datetime, n_samples: int, rate_hz: float) -> None:
        """Consume los seq (y la batería) de un tramo de streaming sin generar chunks."""
        raise NotImplementedError

    def chunks(
        self,
        rng: np.random.Generator,
        *,
        stream_id: str,
        session_id: str,
        start: datetime,
        rate_hz: float,
        channels: Mapping[str, np.ndarray],
        quality: Sequence[ChunkQuality],
        calibration_id: str | None,
    ) -> Iterator[ChunkOutcome]:
        """Trocea `channels` (todas con n muestras, n múltiplo de las muestras por chunk; la muestra 0 es
        el instante real `start`) en chunks; `quality[i]` es la del chunk i. Claves de `channels`:
        emg -> {"emg"}; imu -> los 6 de `signals.imu.IMU_CHANNELS`; stretch -> {"adc_counts",
        "resistance_ohm", "length_mm"} (las dos derivadas opcionales; con length_mm hace falta
        `calibration_id`). RNG: pérdidas y latencias de este tramo.
        """
        raise NotImplementedError

    def battery_pct(self, at: datetime) -> float:
        raise NotImplementedError

    def status(self, rng: np.random.Generator, at: datetime) -> DeviceStatus:
        raise NotImplementedError

    def clock_sync(self, rng: np.random.Generator, at: datetime) -> ClockSync | None:
        """Anclaje device_ms <-> UTC en `at` (lo registra como último anclaje); None si está apagado."""
        raise NotImplementedError
