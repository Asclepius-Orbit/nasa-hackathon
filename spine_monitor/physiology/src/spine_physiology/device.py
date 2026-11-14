"""Wearable simulado: arranques, reloj monotónico, troceo en chunks, DeviceStatus y ClockSync.

- `plan_device_day`: un arranque al encender (`device.power_on_time_utc`), reinicios espontáneos
  (`faults.plan_reboots`) con su tiempo offline y apagado a `power_off_time_utc`. Cada arranque tiene un
  boot_id derivado (`rng.derive_boot_id(seed, astronaut_id, day.index, n)`, n = 0, 1, ...) y una deriva
  de reloj propia. RNG: `(astronaut_id, day.index, "device")`. Plan regenerable solo por (astronauta, día).
- `SimDevice.chunks`: trocea arrays de sesión en chunks de `chunk_ms` (argumento explícito de cada tramo:
  `signals.chunk_ms` en sesiones, `schedule.background.chunk_ms` en el fondo) (contratos EmgChunk /
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
  link, streams con last_seq; `published` fija el last_seq de cada stream a lo ya publicado) y `clock_sync`
  (ClockSync `method: simulated`, None si está apagado).
Todos los modelos se construyen sin pasar None a campos opcionales (ver `spine_contracts.dump`).

Estado que depende del orden de llamadas (el generador procesa el día en orden temporal): los anclajes
de `clock_sync` (solo cuentan para `clock_uncertain` los registrados antes de generar los chunks que
siguen), los tramos `stalled` (los registra `chunks`) y los tramos de streaming de la batería.
"""

import math
from bisect import bisect_left, insort
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from spine_contracts import (
    SCHEMA_VERSION,
    ClockSync,
    DeviceStatus,
    EmgChunk,
    ImuChunk,
    QualityFlag,
    StreamKind,
    StretchChunk,
)

from spine_physiology.config import MissionConfig
from spine_physiology.faults import (
    CLEAN,
    ChunkQuality,
    QualityState,
    RebootEvent,
    plan_packet_loss,
    plan_reboots,
    sample_drift_ppm,
    sample_latency_ms,
)
from spine_physiology.rng import derive_boot_id, generator
from spine_physiology.signals.imu import IMU_CHANNELS
from spine_physiology.state import DayInfo

_US = timedelta(microseconds=1)


def _us(delta: timedelta) -> int:
    """Microsegundos enteros de un timedelta."""
    return delta // _US


@dataclass(frozen=True, slots=True)
class BootEpoch:
    """Un arranque: reloj monotónico desde `boot_at` hasta `end_at` (apagado o caída por reinicio)."""

    boot_id: int
    boot_at: datetime  # instante real (UTC) en que device_ms = 0
    end_at: datetime | None  # None = abierto (gateway)
    drift_ppm: float  # el reloj del dispositivo avanza (1 + drift_ppm × 1e-6) veces el real

    def contains(self, at: datetime) -> bool:
        return self.boot_at <= at and (self.end_at is None or at < self.end_at)

    def device_ms(self, at: datetime) -> int:
        """Lectura del reloj del dispositivo en el instante real `at` (ms enteros, >= 0)."""
        return max(0, math.floor(_us(at - self.boot_at) * self.clock_rate / 1000.0))

    def real_at(self, device_ms: float) -> datetime:
        """Instante real en que el reloj del dispositivo marca `device_ms`."""
        return self.boot_at + timedelta(microseconds=device_ms * 1000.0 / self.clock_rate)

    @property
    def clock_rate(self) -> float:
        return 1.0 + self.drift_ppm * 1e-6


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
        for boot in self.boots:
            if boot.contains(at):
                return boot
        return None


def _build_boots(
    config: MissionConfig,
    rng: np.random.Generator,
    reboots: Sequence[RebootEvent],
    *,
    power_on: datetime,
    power_off: datetime | None,
    boot_id_for: Any,
) -> tuple[BootEpoch, ...]:
    """Arranques entre reinicios; tras un reinicio que no vuelve antes del apagado no hay arranque nuevo."""
    boots: list[BootEpoch] = []
    boot_at = power_on
    for n in range(len(reboots) + 1):
        end_at = reboots[n].at if n < len(reboots) else power_off
        if power_off is not None and boot_at >= power_off:
            break
        drift = sample_drift_ppm(rng, config.faults.device.clock)
        boots.append(BootEpoch(boot_id=boot_id_for(n), boot_at=boot_at, end_at=end_at, drift_ppm=drift))
        if n < len(reboots):
            boot_at = reboots[n].at + timedelta(seconds=reboots[n].offline_s)
    return tuple(boots)


def plan_device_day(config: MissionConfig, seed: int, astronaut_id: str, day: DayInfo) -> DevicePlan:
    rng = generator(seed, astronaut_id, day.index, "device")
    power_on = day.at(config.device.power_on_time_utc)
    power_off = day.at(config.device.power_off_time_utc)
    reboots = plan_reboots(rng, config.faults.device.reboot, start=power_on, end=power_off)
    boots = _build_boots(
        config, rng, reboots, power_on=power_on, power_off=power_off,
        boot_id_for=lambda n: derive_boot_id(seed, astronaut_id, day.index, n),
    )  # fmt: skip
    return DevicePlan(
        astronaut_id=astronaut_id,
        device_id=config.member(astronaut_id).device_id,
        power_on=power_on,
        power_off=power_off,
        boots=boots,
        reboots=reboots,
    )


def plan_device_live(config: MissionConfig, seed: int, astronaut_id: str, *, boot_at: datetime, horizon: timedelta) -> DevicePlan:
    """Plan del gateway interactivo: arranca en `boot_at`, reinicios planificados hasta `boot_at + horizon`
    (el gateway pide otro plan al agotarlo). RNG `(astronaut_id, "live", boot_at.isoformat(), "device")`.
    """
    keys = (astronaut_id, "live", boot_at.isoformat())
    rng = generator(seed, *keys, "device")
    reboots = plan_reboots(rng, config.faults.device.reboot, start=boot_at, end=boot_at + horizon)
    boots = _build_boots(
        config, rng, reboots, power_on=boot_at, power_off=None,
        boot_id_for=lambda n: derive_boot_id(seed, *keys, n),
    )  # fmt: skip
    return DevicePlan(
        astronaut_id=astronaut_id,
        device_id=config.member(astronaut_id).device_id,
        power_on=boot_at,
        power_off=None,
        boots=boots,
        reboots=reboots,
    )


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


@dataclass(slots=True)
class _Layout:
    """Reparto de un tramo de streaming en chunks respecto a los arranques del plan."""

    spc: int
    n_chunks: int
    start_us: np.ndarray  # inicio de cada chunk, µs desde `start`
    epoch_idx: np.ndarray  # índice en plan.boots, -1 si el dispositivo no existe en ese instante
    seq: np.ndarray  # seq asignado (-1 si no existe)
    t0_ms: np.ndarray  # reloj del dispositivo al inicio del chunk (0 si no existe)


class SimDevice:
    """Estado mutable de un dispositivo durante un plan: seq por (boot_id, stream), batería, último anclaje.

    Las sesiones de un día se procesan en orden temporal sobre la misma instancia.
    """

    def __init__(self, config: MissionConfig, plan: DevicePlan) -> None:
        self._config = config
        self._plan = plan
        self._seq: dict[tuple[int, str], int] = {}
        self._anchors: dict[int, list[datetime]] = {}  # boot_id -> instantes reales de ClockSync
        self._streaming: list[tuple[datetime, datetime]] = []  # tramos con streaming (batería), ordenados y disjuntos
        self._stalls: dict[str, list[tuple[datetime, datetime]]] = {}  # stream_id -> ráfagas stalled
        self._rates: dict[str, float] = {}  # stream_id -> última frecuencia usada (la activa, p. ej. la reducida del fondo)
        self.live_previous_lost: dict[str, bool] = {}  # solo modo en vivo: stream_id -> el último chunk emitido se perdió

    @property
    def plan(self) -> DevicePlan:
        return self._plan

    def next_seq(self, boot_id: int, stream_id: str) -> int:
        return self._seq.get((boot_id, stream_id), 0)

    # --- reparto en chunks ------------------------------------------------------------------------

    def _layout(self, start: datetime, n_samples: int, rate_hz: float, chunk_ms: float) -> _Layout:
        spc = self._config.signals.samples_per_chunk(rate_hz, chunk_ms=chunk_ms)
        n_chunks = -(-n_samples // spc) if n_samples > 0 else 0
        start_us = np.rint(np.arange(n_chunks) * spc / rate_hz * 1e6).astype(np.int64)
        epoch_idx = np.full(n_chunks, -1, dtype=np.int64)
        t0_ms = np.zeros(n_chunks, dtype=np.int64)
        for k, boot in enumerate(self._plan.boots):
            boot_us = _us(boot.boot_at - start)
            inside = (start_us >= boot_us) & (epoch_idx < 0)
            if boot.end_at is not None:
                inside &= start_us < _us(boot.end_at - start)
            epoch_idx[inside] = k
            t0_ms[inside] = np.floor((start_us[inside] - boot_us).astype(np.float64) * boot.clock_rate / 1000.0).astype(np.int64)
        return _Layout(spc, n_chunks, start_us, epoch_idx, np.full(n_chunks, -1, dtype=np.int64), t0_ms)

    def _assign_seq(self, layout: _Layout, stream_id: str) -> None:
        """Asigna seq a los chunks existentes y avanza el contador de cada arranque."""
        for k, boot in enumerate(self._plan.boots):
            selected = np.flatnonzero(layout.epoch_idx == k)
            if selected.size == 0:
                continue
            base = self.next_seq(boot.boot_id, stream_id)
            layout.seq[selected] = base + np.arange(selected.size)
            self._seq[(boot.boot_id, stream_id)] = base + int(selected.size)

    def advance(self, stream_id: str, *, start: datetime, n_samples: int, rate_hz: float, chunk_ms: float) -> None:
        """Consume los seq (y la batería) de un tramo de streaming sin generar chunks (`chunk_ms`: el del tramo)."""
        self._rates[stream_id] = rate_hz
        layout = self._layout(start, n_samples, rate_hz, chunk_ms)
        self._assign_seq(layout, stream_id)
        self._record_streaming(start, n_samples, rate_hz)

    def _record_streaming(self, start: datetime, n_samples: int, rate_hz: float) -> None:
        """Inserta el tramo fundiéndolo con los vecinos que solapan o tocan (la unión no cambia)."""
        if n_samples <= 0:
            return
        new_a = start
        new_b = start + timedelta(microseconds=round(n_samples / rate_hz * 1e6))
        lo = bisect_left(self._streaming, new_a, key=lambda iv: iv[0])
        if lo > 0 and self._streaming[lo - 1][1] >= new_a:
            lo -= 1
        hi = lo
        while hi < len(self._streaming) and self._streaming[hi][0] <= new_b:
            new_a = min(new_a, self._streaming[hi][0])
            new_b = max(new_b, self._streaming[hi][1])
            hi += 1
        self._streaming[lo:hi] = [(new_a, new_b)]

    def chunks(
        self,
        rng: np.random.Generator,
        *,
        stream_id: str,
        session_id: str,
        start: datetime,
        rate_hz: float,
        chunk_ms: float,
        channels: Mapping[str, np.ndarray],
        quality: Sequence[ChunkQuality],
        calibration_id: str | None,
    ) -> Iterator[ChunkOutcome]:
        """Trocea `channels` (todas con n muestras; la muestra 0 es el instante real `start`) en chunks de
        `chunk_ms` (el del tramo: sesiones `signals.chunk_ms`, fondo `schedule.background.chunk_ms`); el último puede ser
        parcial. `quality[i]` es la del chunk i. Claves de `channels`:
        emg -> {"emg"}; imu -> los 6 de `signals.imu.IMU_CHANNELS`; stretch -> {"adc_counts",
        "resistance_ohm", "length_mm"} (las dos derivadas opcionales; con length_mm hace falta
        `calibration_id`). RNG: pérdidas y latencias de este tramo.

        El estado (seq, batería, ráfagas stalled) se actualiza al llamar, no al consumir el iterador.
        """
        kind = self._config.device.stream(stream_id).stream_kind
        required = {StreamKind.EMG: ("emg",), StreamKind.IMU: IMU_CHANNELS, StreamKind.STRETCH: ("adc_counts",)}[kind]
        missing = [c for c in required if c not in channels]
        if missing:
            raise ValueError(f"{kind.value} stream {stream_id} needs channels {missing}")
        if kind is StreamKind.STRETCH and "length_mm" in channels and calibration_id is None:
            raise ValueError("calibration_id is required when length_mm is present")
        lengths = {len(a) for a in channels.values()}
        if len(lengths) != 1:
            raise ValueError(f"channels must have the same length, got {sorted(lengths)}")
        n_samples = lengths.pop()

        self._rates[stream_id] = rate_hz
        layout = self._layout(start, n_samples, rate_hz, chunk_ms)
        if len(quality) < layout.n_chunks:
            raise ValueError(f"need quality for {layout.n_chunks} chunks, got {len(quality)}")
        faults = self._config.faults.device
        lost = plan_packet_loss(rng, faults.packet_loss, layout.n_chunks)
        latency_us = np.rint(sample_latency_ms(rng, faults.clock, layout.n_chunks) * 1000.0).astype(np.int64)
        exists = layout.epoch_idx >= 0
        lost &= exists
        self._assign_seq(layout, stream_id)
        self._record_streaming(start, n_samples, rate_hz)
        self._record_stalls(stream_id, start, layout, lost, n_samples, rate_hz)
        uncertain = self._clock_uncertain(layout, start)
        return self._iter_chunks(
            kind=kind, stream_id=stream_id, session_id=session_id, start=start, rate_hz=rate_hz, channels=channels,
            quality=quality, calibration_id=calibration_id, layout=layout, lost=lost, latency_us=latency_us,
            uncertain=uncertain, n_samples=n_samples,
        )  # fmt: skip

    def _record_stalls(self, stream_id: str, start: datetime, layout: _Layout, lost: np.ndarray, n_samples: int, rate_hz: float) -> None:
        threshold = self._config.faults.device.packet_loss.stalled_after_chunks
        padded = np.concatenate(([False], lost, [False])).astype(np.int8)
        edges = np.flatnonzero(np.diff(padded))
        for first, stop in zip(edges[::2], edges[1::2], strict=True):
            if stop - first >= threshold:
                end_sample = min(int(stop) * layout.spc, n_samples)
                a = start + timedelta(microseconds=int(layout.start_us[first]))
                b = start + timedelta(microseconds=round(end_sample / rate_hz * 1e6))
                self._stalls.setdefault(stream_id, []).append((a, b))

    def _clock_uncertain(self, layout: _Layout, start: datetime) -> np.ndarray:
        """Bool por chunk: error de reloj acumulado desde el último anclaje por encima del umbral."""
        clock = self._config.faults.device.clock
        flags = np.zeros(layout.n_chunks, dtype=bool)
        for k, boot in enumerate(self._plan.boots):
            selected = np.flatnonzero(layout.epoch_idx == k)
            if selected.size == 0:
                continue
            boot_us = _us(boot.boot_at - start)
            # _anchors se mantiene ordenado con insort y todos son >= boot_at: la lista ya sale ordenada
            anchors = [boot_us] + [_us(a - start) for a in self._anchors.get(boot.boot_id, []) if a >= boot.boot_at]
            offsets = layout.start_us[selected]
            last = np.asarray(anchors)[np.searchsorted(anchors, offsets, side="right") - 1]
            error_ms = abs(boot.drift_ppm) * 1e-6 * (offsets - last) / 1000.0
            flags[selected] = error_ms > clock.uncertain_threshold_ms
        return flags

    def _iter_chunks(
        self,
        *,
        kind: StreamKind,
        stream_id: str,
        session_id: str,
        start: datetime,
        rate_hz: float,
        channels: Mapping[str, np.ndarray],
        quality: Sequence[ChunkQuality],
        calibration_id: str | None,
        layout: _Layout,
        lost: np.ndarray,
        latency_us: np.ndarray,
        uncertain: np.ndarray,
        n_samples: int,
    ) -> Iterator[ChunkOutcome]:
        faults = self._config.faults.device
        common_meta = self._chunk_meta(kind, channels)
        gap_state = QualityState(faults.packet_loss.packet_gap_score, frozenset({QualityFlag.PACKET_GAP}))
        unc_state = QualityState(faults.clock.clock_uncertain_score, frozenset({QualityFlag.CLOCK_UNCERTAIN}))
        previous_lost = False
        for i in range(layout.n_chunks):
            if layout.epoch_idx[i] < 0:
                previous_lost = False
                continue
            boot = self._plan.boots[int(layout.epoch_idx[i])]
            a = i * layout.spc
            b = min(a + layout.spc, n_samples)
            end_us = round(b / rate_hz * 1e6)
            received_at = start + timedelta(microseconds=end_us + int(latency_us[i]))
            seq = int(layout.seq[i])
            t0 = int(layout.t0_ms[i])
            if lost[i]:
                previous_lost = True
                yield ChunkOutcome(stream_id, kind, boot.boot_id, seq, t0, received_at, None)
                continue
            extra = CLEAN
            if previous_lost:
                extra = extra.merge(gap_state)
            if uncertain[i]:
                extra = extra.merge(unc_state)
            previous_lost = False
            chunk = self._build_chunk(
                kind, common_meta, quality[i], extra,
                base={
                    "schema_version": SCHEMA_VERSION, "astronaut_id": self._plan.astronaut_id, "device_id": self._plan.device_id,
                    "stream_id": stream_id, "session_id": session_id, "source": "simulator", "boot_id": boot.boot_id,
                    "seq": seq, "t0_device_ms": t0, "received_at": received_at, "sample_rate_hz": rate_hz, "n_samples": b - a,
                },
                channels={name: arr[a:b] for name, arr in channels.items()},
                calibration_id=calibration_id,
            )  # fmt: skip
            yield ChunkOutcome(stream_id, kind, boot.boot_id, seq, t0, received_at, chunk)

    def _chunk_meta(self, kind: StreamKind, channels: Mapping[str, np.ndarray]) -> dict[str, Any]:
        """Bloques de metadatos del config (acquisition/filter, config, circuit) para el tipo de stream."""
        signals = self._config.signals
        if kind is StreamKind.EMG:
            acq = signals.emg.acquisition
            meta: dict[str, Any] = {
                "acquisition": {"adc_bits": acq.adc_bits, "vref_v": acq.vref_v, "gain": acq.gain, "input_pin": acq.input_pin}
            }
            f = signals.emg.filter
            filt = {k: v for k, v in {"highpass_hz": f.highpass_hz, "lowpass_hz": f.lowpass_hz, "notch_hz": f.notch_hz}.items() if v is not None}
            if filt:
                meta["filter"] = filt
            return meta
        if kind is StreamKind.IMU:
            imu = signals.imu
            return {"config": {"accel_range_g": imu.accel_range_g, "gyro_range_dps": imu.gyro_range_dps, "dlpf_hz": imu.dlpf_hz}}
        c = signals.stretch.circuit
        return {"circuit": {"adc_bits": c.adc_bits, "vref_v": c.vref_v, "series_resistor_ohm": c.series_resistor_ohm, "input_pin": c.input_pin}}

    def _build_chunk(
        self,
        kind: StreamKind,
        meta: dict[str, Any],
        chunk_quality: ChunkQuality,
        extra: QualityState,
        *,
        base: dict[str, Any],
        channels: Mapping[str, np.ndarray],
        calibration_id: str | None,
    ) -> StreamChunkModel:
        def q(key: str) -> Any:
            state = chunk_quality.get(key)
            if state is None:  # p. ej. length_mm sin quality propia hereda la de adc_counts
                state = chunk_quality.get("adc_counts", CLEAN)
            return state.merge(extra).to_contract()

        if kind is StreamKind.EMG:
            return EmgChunk.model_validate({
                **base, "stream_kind": "emg", "signal_kind": "raw", "units": {"emg": "mV"},
                "data": {"emg": channels["emg"].tolist()}, "quality": {"emg": q("emg")}, **meta,
            })  # fmt: skip
        if kind is StreamKind.IMU:
            return ImuChunk.model_validate({
                **base, "stream_kind": "imu",
                "units": {c: ("g" if c.startswith("accel") else "deg/s") for c in IMU_CHANNELS},
                "data": {c: channels[c].tolist() for c in IMU_CHANNELS},
                "quality": {"accel": q("accel"), "gyro": q("gyro")}, **meta,
            })  # fmt: skip
        units = {"adc_counts": "count"}
        data: dict[str, Any] = {"adc_counts": [int(v) for v in channels["adc_counts"]]}
        qual = {"adc_counts": q("adc_counts")}
        if "resistance_ohm" in channels:
            units["resistance_ohm"] = "ohm"
            data["resistance_ohm"] = channels["resistance_ohm"].tolist()
        stretch: dict[str, Any] = {**base, "stream_kind": "stretch", "units": units, "data": data, **meta}
        if "length_mm" in channels:
            units["length_mm"] = "mm"
            data["length_mm"] = channels["length_mm"].tolist()
            qual["length_mm"] = q("length_mm")
            stretch["calibration_id"] = calibration_id
        stretch["quality"] = qual
        return StretchChunk.model_validate(stretch)

    # --- batería, estado y reloj ------------------------------------------------------------------

    def battery_pct(self, at: datetime) -> float:
        """Carga al instante `at`: `start_pct` al encender menos el drenaje idle desde el encendido y el
        extra de streaming (unión de los tramos con streaming registrados) hasta `at`. En [0, 100].
        """
        cfg = self._config.device.battery
        hours_on = max(0.0, (at - self._plan.power_on).total_seconds()) / 3600.0
        streamed = 0.0
        for a, b in self._streaming:  # ya ordenados y disjuntos
            if a >= at:
                break
            b = min(b, at)
            if b > a:
                streamed += (b - a).total_seconds()
        pct = cfg.start_pct - cfg.drain_pct_per_hour_idle * hours_on - (cfg.drain_pct_per_hour_streaming - cfg.drain_pct_per_hour_idle) * streamed / 3600.0
        return float(min(100.0, max(0.0, pct)))

    def _stalled(self, stream_id: str, at: datetime) -> bool:
        return any(a <= at < b for a, b in self._stalls.get(stream_id, ()))

    def status(self, rng: np.random.Generator, at: datetime, *, published: Mapping[str, int] | None = None) -> DeviceStatus:
        """DeviceStatus en `at`. `published`: stream_id -> último seq PUBLICADO del arranque en curso; si se
        da, `streams[].last_seq` lo usa (y se omite en los streams que no figuran) en lugar de
        `next_seq - 1`, para que el estado nunca declare un seq que aún no está en el bus."""
        cfg = self._config.device
        # Mismas extracciones del RNG esté el dispositivo encendido o no.
        temperature = float(rng.normal(cfg.temperature_c.mean, cfg.temperature_c.sd))
        rssi = int(rng.integers(cfg.rssi_dbm.min, cfg.rssi_dbm.max + 1)) if cfg.rssi_dbm is not None else None
        battery = self.battery_pct(at)
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "astronaut_id": self._plan.astronaut_id,
            "device_id": self._plan.device_id,
            "timestamp": at,
            "source": "simulator",
            "firmware_version": cfg.firmware_version,
            "battery_pct": battery,
        }
        epoch = self._plan.epoch_at(at)
        if epoch is None:
            return DeviceStatus.model_validate({**payload, "status": "offline"})
        streams = []
        stalled = False
        for s in cfg.streams:
            is_stalled = self._stalled(s.stream_id, at)
            stalled |= is_stalled
            entry: dict[str, Any] = {
                "stream_id": s.stream_id,
                "stream_kind": s.stream_kind.value,
                "state": "stalled" if is_stalled else "active",
                "sample_rate_hz": self._rates.get(s.stream_id, self._config.signals.rate(s.stream_kind)),
            }
            last = self.next_seq(epoch.boot_id, s.stream_id) - 1 if published is None else published.get(s.stream_id, -1)
            if last >= 0:
                entry["last_seq"] = last
            streams.append(entry)
        link: dict[str, Any] = {"type": cfg.link_type}
        if rssi is not None:
            link["rssi_dbm"] = rssi
        degraded = stalled or battery < cfg.battery.degraded_below_pct
        return DeviceStatus.model_validate({
            **payload, "status": "degraded" if degraded else "ok", "boot_id": epoch.boot_id,
            "uptime_ms": epoch.device_ms(at), "temperature_c": temperature, "link": link, "streams": streams,
        })  # fmt: skip

    def clock_sync(self, rng: np.random.Generator, at: datetime) -> ClockSync | None:
        """Anclaje device_ms <-> UTC en `at` (lo registra como último anclaje); None si está apagado."""
        uncertainty = float(rng.uniform(self._config.faults.device.clock.sync_uncertainty_ms.min, self._config.faults.device.clock.sync_uncertainty_ms.max))
        epoch = self._plan.epoch_at(at)
        if epoch is None:
            return None
        insort(self._anchors.setdefault(epoch.boot_id, []), at)
        return ClockSync.model_validate({
            "schema_version": SCHEMA_VERSION, "astronaut_id": self._plan.astronaut_id, "device_id": self._plan.device_id,
            "boot_id": epoch.boot_id, "device_ms": epoch.device_ms(at), "utc": at, "uncertainty_ms": uncertainty, "method": "simulated",
        })  # fmt: skip
