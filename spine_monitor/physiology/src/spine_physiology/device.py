"""Simulated wearable: boots, monotonic clock, chunking, DeviceStatus and ClockSync.

- `plan_device_day`: one boot at power-on (`device.power_on_time_utc`), spontaneous restarts
  (`faults.plan_reboots`) with their offline time, and power-off at `power_off_time_utc`. Each boot has a
  derived boot_id (`rng.derive_boot_id(seed, astronaut_id, day.index, n)`, n = 0, 1, ...) and its own clock
  drift. RNG: `(astronaut_id, day.index, "device")`. Plan regenerable per (astronaut, day) alone.
- `SimDevice.chunks`: slices session arrays into chunks of `chunk_ms` (explicit argument of each span:
  `signals.chunk_ms` in sessions, `schedule.background.chunk_ms` in the background) (EmgChunk /
  ImuChunk / StretchChunk contracts of spine_contracts, `source: simulator`): seq per (boot_id, stream) from 0 and
  without gaps except losses (which also consume seq), `t0_device_ms` from the boot clock (with
  drift), `received_at` = real instant of the end of the chunk + latency, metadata (`acquisition`,
  `filter`, `config`, `circuit`) from the config, `calibration_id` if there is length_mm, quality per chunk
  (+ `packet_gap` on the first chunk after a loss, + `clock_uncertain` if the clock error since the
  last anchor exceeds the threshold). Chunks that start while the device is off/restarting do not
  exist (nor consume seq); after a restart seq and t0 go back to 0 with the new boot_id. Losses
  come out as `ChunkOutcome(chunk=None)` for the ground truth.
- `SimDevice.advance`: consumes seq as if it had been emitted (to regenerate a single session with the
  seq it would have within the full day).
- `status` (DeviceStatus: ok/degraded due to battery or stalled/offline stream, battery, temperature,
  link, streams with last_seq; `published` pins each stream's last_seq to what was already published) and `clock_sync`
  (ClockSync `method: simulated`, None if powered off).
All models are built without passing None to optional fields (see `spine_contracts.dump`).

State that depends on call order (the generator processes the day in temporal order): the `clock_sync`
anchors (only those registered before generating the chunks that follow count for `clock_uncertain`),
the `stalled` spans (registered by `chunks`) and the battery streaming spans.
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
    """Whole microseconds of a timedelta."""
    return delta // _US


@dataclass(frozen=True, slots=True)
class BootEpoch:
    """A boot: monotonic clock from `boot_at` until `end_at` (power-off or drop due to restart)."""

    boot_id: int
    boot_at: datetime  # real (UTC) instant at which device_ms = 0
    end_at: datetime | None  # None = open-ended (gateway)
    drift_ppm: float  # the device clock advances (1 + drift_ppm × 1e-6) times the real one

    def contains(self, at: datetime) -> bool:
        return self.boot_at <= at and (self.end_at is None or at < self.end_at)

    def device_ms(self, at: datetime) -> int:
        """Device clock reading at real instant `at` (integer ms, >= 0)."""
        return max(0, math.floor(_us(at - self.boot_at) * self.clock_rate / 1000.0))

    def real_at(self, device_ms: float) -> datetime:
        """Real instant at which the device clock reads `device_ms`."""
        return self.boot_at + timedelta(microseconds=device_ms * 1000.0 / self.clock_rate)

    @property
    def clock_rate(self) -> float:
        return 1.0 + self.drift_ppm * 1e-6


@dataclass(frozen=True, slots=True)
class DevicePlan:
    """Boots and drops of a device in a window (one day, or open-ended in the gateway)."""

    astronaut_id: str
    device_id: str
    power_on: datetime
    power_off: datetime | None
    boots: tuple[BootEpoch, ...]  # ordered; gaps between them = offline
    reboots: tuple[RebootEvent, ...]

    def epoch_at(self, at: datetime) -> BootEpoch | None:
        """Boot active at `at`, or None if the device is off or restarting."""
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
    """Boots between restarts; after a restart that does not return before power-off there is no new boot."""
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
    """Interactive gateway plan: boots at `boot_at`, planned restarts up to `boot_at + horizon`
    (the gateway asks for another plan when it runs out). RNG `(astronaut_id, "live", boot_at.isoformat(), "device")`.
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
    """A chunk slot: emitted (`chunk`) or lost on the link (`chunk is None`, the seq is consumed)."""

    stream_id: str
    stream_kind: StreamKind
    boot_id: int
    seq: int
    t0_device_ms: int
    received_at: datetime  # for a lost one: when it would have arrived
    chunk: StreamChunkModel | None

    @property
    def lost(self) -> bool:
        return self.chunk is None


@dataclass(slots=True)
class _Layout:
    """Split of a streaming span into chunks with respect to the plan's boots."""

    spc: int
    n_chunks: int
    start_us: np.ndarray  # start of each chunk, µs since `start`
    epoch_idx: np.ndarray  # index into plan.boots, -1 if the device does not exist at that instant
    seq: np.ndarray  # assigned seq (-1 if it does not exist)
    t0_ms: np.ndarray  # device clock at the start of the chunk (0 if it does not exist)


class SimDevice:
    """Mutable state of a device during a plan: seq per (boot_id, stream), battery, last anchor.

    The sessions of a day are processed in temporal order on the same instance.
    """

    def __init__(self, config: MissionConfig, plan: DevicePlan) -> None:
        self._config = config
        self._plan = plan
        self._seq: dict[tuple[int, str], int] = {}
        self._anchors: dict[int, list[datetime]] = {}  # boot_id -> real ClockSync instants
        self._streaming: list[tuple[datetime, datetime]] = []  # streaming spans (battery), ordered and disjoint
        self._stalls: dict[str, list[tuple[datetime, datetime]]] = {}  # stream_id -> stalled bursts
        self._rates: dict[str, float] = {}  # stream_id -> last rate used (the active one, e.g. the reduced background one)
        self.live_previous_lost: dict[str, bool] = {}  # live mode only: stream_id -> the last emitted chunk was lost

    @property
    def plan(self) -> DevicePlan:
        return self._plan

    def next_seq(self, boot_id: int, stream_id: str) -> int:
        return self._seq.get((boot_id, stream_id), 0)

    # --- split into chunks ------------------------------------------------------------------------

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
        """Assigns seq to the existing chunks and advances each boot's counter."""
        for k, boot in enumerate(self._plan.boots):
            selected = np.flatnonzero(layout.epoch_idx == k)
            if selected.size == 0:
                continue
            base = self.next_seq(boot.boot_id, stream_id)
            layout.seq[selected] = base + np.arange(selected.size)
            self._seq[(boot.boot_id, stream_id)] = base + int(selected.size)

    def advance(self, stream_id: str, *, start: datetime, n_samples: int, rate_hz: float, chunk_ms: float) -> None:
        """Consumes the seq (and battery) of a streaming span without generating chunks (`chunk_ms`: the span's)."""
        self._rates[stream_id] = rate_hz
        layout = self._layout(start, n_samples, rate_hz, chunk_ms)
        self._assign_seq(layout, stream_id)
        self._record_streaming(start, n_samples, rate_hz)

    def _record_streaming(self, start: datetime, n_samples: int, rate_hz: float) -> None:
        """Inserts the span merging it with neighbours that overlap or touch (the union does not change)."""
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
        """Slices `channels` (all with n samples; sample 0 is the real instant `start`) into chunks of
        `chunk_ms` (the span's: sessions `signals.chunk_ms`, background `schedule.background.chunk_ms`); the last may be
        partial. `quality[i]` is that of chunk i. `channels` keys:
        emg -> {"emg"}; imu -> the 6 of `signals.imu.IMU_CHANNELS`; stretch -> {"adc_counts",
        "resistance_ohm", "length_mm"} (the two optional derived ones; with length_mm `calibration_id`
        is needed). RNG: losses and latencies of this span.

        The state (seq, battery, stalled bursts) is updated on call, not when consuming the iterator.
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
        """Bool per chunk: clock error accumulated since the last anchor above the threshold."""
        clock = self._config.faults.device.clock
        flags = np.zeros(layout.n_chunks, dtype=bool)
        for k, boot in enumerate(self._plan.boots):
            selected = np.flatnonzero(layout.epoch_idx == k)
            if selected.size == 0:
                continue
            boot_us = _us(boot.boot_at - start)
            # _anchors is kept sorted with insort and all are >= boot_at: the list already comes out sorted
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
        """Config metadata blocks (acquisition/filter, config, circuit) for the stream type."""
        signals = self._config.signals
        if kind is StreamKind.EMG:
            acq = signals.emg.acquisition
            meta: dict[str, Any] = {"acquisition": {"adc_bits": acq.adc_bits, "vref_v": acq.vref_v, "gain": acq.gain, "input_pin": acq.input_pin}}
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
            if state is None:  # e.g. length_mm without its own quality inherits that of adc_counts
                state = chunk_quality.get("adc_counts", CLEAN)
            return state.merge(extra).to_contract()

        if kind is StreamKind.EMG:
            return EmgChunk.model_validate({
                **base, "stream_kind": "emg", "signal_kind": self._config.signals.emg.signal_kind, "units": {"emg": "mV"},
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

    # --- battery, status and clock ----------------------------------------------------------------

    def battery_pct(self, at: datetime) -> float:
        """Charge at instant `at`: `start_pct` at power-on minus idle drain since power-on and the
        streaming extra (union of the registered streaming spans) up to `at`. In [0, 100].
        """
        cfg = self._config.device.battery
        hours_on = max(0.0, (at - self._plan.power_on).total_seconds()) / 3600.0
        streamed = 0.0
        for a, b in self._streaming:  # already sorted and disjoint
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
        """DeviceStatus at `at`. `published`: stream_id -> last PUBLISHED seq of the current boot; if
        given, `streams[].last_seq` uses it (and it is omitted for streams not listed) instead of
        `next_seq - 1`, so that the status never declares a seq that is not yet on the bus."""
        cfg = self._config.device
        # Same RNG draws whether the device is powered on or not.
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
        """device_ms <-> UTC anchor at `at` (registered as the last anchor); None if powered off."""
        uncertainty = float(rng.uniform(self._config.faults.device.clock.sync_uncertainty_ms.min, self._config.faults.device.clock.sync_uncertainty_ms.max))
        epoch = self._plan.epoch_at(at)
        if epoch is None:
            return None
        insort(self._anchors.setdefault(epoch.boot_id, []), at)
        return ClockSync.model_validate({
            "schema_version": SCHEMA_VERSION, "astronaut_id": self._plan.astronaut_id, "device_id": self._plan.device_id,
            "boot_id": epoch.boot_id, "device_ms": epoch.device_ms(at), "utc": at, "uncertainty_ms": uncertainty, "method": "simulated",
        })  # fmt: skip
