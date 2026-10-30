"""Generador de misión: iterador perezoso de `Envelope`s, reproducible bit a bit por rama.

`MissionGenerator`:
- `iter_messages`: perfiles (al inicio, `at` = start_date 00:00 UTC) y luego día a día. Dentro de cada
  día, los mensajes de todos los astronautas mezclados por `at` (merge estable en el orden de `crew`).
  Por astronauta y día, en orden de `at` (empates por orden de producción): daily_context y ground truth
  diario (latent_state, device_events) a las 00:00; clock_sync en cada arranque y cada
  `device.clock_sync_interval_s` (retained); device_status (retained) al encender, cada
  `device.status_interval_s` y uno `offline` al apagar; por sesión, el registro Session y las
  calibraciones que se registran en ella a su inicio, los stream_chunk a su `received_at` y el ground
  truth de la sesión (quality_faults, lost_chunks, expected_features) a su fin; el self_report a su hora.
- `day_messages` / `session_messages`: la misma salida que dentro de `iter_messages`, regenerada sola
  (un día o una sesión sueltos dan exactamente los mismos payloads, en el mismo orden relativo).
- `resimulate`: la sesión con otra gravedad (delega en `sessions.resimulate`).
- API en vivo para el gateway: `live_session`, `live_calibration` y `live_background`.
Topics con `spine_contracts.topics`. `source` siempre `simulator`.

Cómo se recorre un astronauta-día (lo que garantiza la reproducibilidad por rama):
- El dispositivo arranca limpio cada día (`plan_device_day` + `SimDevice` nuevo): nada se arrastra de
  un día a otro salvo la deriva por dosis de los latentes, que es regenerable por día.
- Todos los ClockSync del día se registran al principio (un anclaje solo afecta a los chunks
  posteriores, así que el orden de registro no cambia nada).
- Los tramos de streaming (sesiones y tramos del fondo entre pausas) se procesan en orden temporal sobre
  el mismo `SimDevice`; en `session_messages` los tramos de otras sesiones se consumen con
  `SimDevice.advance` (mismos seq sin sintetizar). Cada tramo usa su propio RNG
  `(astronaut_id, day, slot, stream_id, "device", k0)`, nunca estado de otra sesión.
- `SimDevice.chunks` asigna seq y registra batería y ráfagas `stalled` del tramo entero al llamarse;
  los chunks se consumen perezosamente. En cada DeviceStatus el `last_seq` de cada stream se corrige
  con el último seq (emitido o perdido) cuyo chunk acabó antes del instante del estado.
- La salida se reordena por `at` con un montículo que se vacía en cada instante de estado: memoria
  acotada a un intervalo de estado, aunque el fondo dure horas.
"""

import heapq
import itertools
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import numpy as np
from spine_contracts import GravityEnvironment, QualityFlag, StreamKind, dump, topics

from spine_physiology import reports, truth
from spine_physiology import sessions as sess
from spine_physiology.config import ExerciseProtocol, MissionConfig
from spine_physiology.device import ChunkOutcome, DevicePlan, SimDevice, plan_device_day
from spine_physiology.envelope import Envelope, EnvelopeKind
from spine_physiology.faults import QualityState
from spine_physiology.rng import RngTree
from spine_physiology.sessions import (
    CalibrationRecord,
    SessionPlan,
    SessionRef,
    SessionSynthesis,
    StreamSynthesis,
    SynthSpec,
)
from spine_physiology.signals import rep_schedule
from spine_physiology.state import DayInfo, LatentModel, MissionTimeline

_US = timedelta(microseconds=1)


def _chunk_start(start: datetime, k: int, samples_per_chunk: int, rate_hz: float) -> datetime:
    """Instante real del inicio del chunk k (misma aritmética que `SimDevice`)."""
    return start + timedelta(microseconds=round(k * samples_per_chunk / rate_hz * 1e6))


def _existing_chunk_ends(plan: DevicePlan, start: datetime, n_chunks: int, samples_per_chunk: int, rate_hz: float) -> list[datetime]:
    """Fin real de cada chunk que existe (dispositivo encendido al empezar el chunk), con el mismo
    reparto en arranques que `SimDevice`: a cada uno le corresponde un ChunkOutcome, en orden."""
    idx = np.arange(n_chunks, dtype=np.int64)
    start_us = np.rint(idx * samples_per_chunk / rate_hz * 1e6).astype(np.int64)
    end_us = np.rint((idx + 1) * samples_per_chunk / rate_hz * 1e6).astype(np.int64)
    epoch = np.full(n_chunks, -1, dtype=np.int64)
    for k, boot in enumerate(plan.boots):
        inside = (start_us >= (boot.boot_at - start) // _US) & (epoch < 0)
        if boot.end_at is not None:
            inside &= start_us < (boot.end_at - start) // _US
        epoch[inside] = k
    return [start + timedelta(microseconds=int(e)) for e in end_us[epoch >= 0]]


class _Reorder:
    """Montículo por (`at`, orden de llegada): suelta en orden lo anterior a una marca."""

    def __init__(self) -> None:
        self._items: list[tuple[datetime, int, Envelope]] = []
        self._count = itertools.count()

    def push(self, envelope: Envelope) -> None:
        heapq.heappush(self._items, (envelope.at, next(self._count), envelope))

    def release(self, before: datetime | None) -> Iterator[Envelope]:
        """Todo lo que tiene `at` < `before` (todo si None)."""
        while self._items and (before is None or self._items[0][0] < before):
            yield heapq.heappop(self._items)[2]


class _Cursor:
    """Consumo perezoso de los ChunkOutcome de un stream en un tramo, hasta un instante de fin de chunk."""

    def __init__(self, outcomes: Iterator[ChunkOutcome], ends: list[datetime]) -> None:
        self._outcomes = outcomes
        self._ends = ends
        self._next = 0

    def drain(self, until: datetime | None) -> Iterator[ChunkOutcome]:
        while self._next < len(self._ends) and (until is None or self._ends[self._next] <= until):
            self._next += 1
            yield next(self._outcomes)


@dataclass(slots=True)
class _Run:
    """Una sesión en curso dentro del recorrido de un día."""

    plan: SessionPlan
    synthesis: SessionSynthesis
    gravity_environment: GravityEnvironment
    cursors: list[_Cursor] = field(default_factory=list)
    lost: dict[str, list[ChunkOutcome]] = field(default_factory=dict)


class MissionGenerator:
    def __init__(self, config: MissionConfig, seed: int | None = None) -> None:
        """`seed=None` usa `config.seed`."""
        self._config = config
        self._seed = config.seed if seed is None else seed
        self._tree = RngTree(self._seed)
        self._timeline = MissionTimeline(config)
        self._latents = LatentModel(config, self._timeline, self._seed)
        self._plans: dict[tuple[str, int], tuple[SessionPlan, ...]] = {}
        # Calibraciones hechas en vivo (gateway), por (astronauta, stream): sustituyen a las programadas.
        self._live_calibrations: dict[tuple[str, str], CalibrationRecord] = {}

    @property
    def config(self) -> MissionConfig:
        return self._config

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def timeline(self) -> MissionTimeline:
        return self._timeline

    @property
    def latents(self) -> LatentModel:
        return self._latents

    def iter_days(self) -> Iterator[DayInfo]:
        return iter(self._timeline)

    def iter_messages(self) -> Iterator[Envelope]:
        yield from self.profiles()
        for day in self._timeline:
            yield from self.day_messages(day.index)

    def profiles(self) -> Iterator[Envelope]:
        at = self._timeline.day(0).start
        for member in self._config.crew:
            profile = reports.build_profile(member, updated_at=at)
            yield Envelope.of(EnvelopeKind.PROFILE, profile, at=at, astronaut_id=member.astronaut_id, day=None)

    def day_messages(self, day: int, astronaut_id: str | None = None) -> Iterator[Envelope]:
        """Mensajes de un día (de un astronauta, o de todos mezclados por `at`)."""
        self._timeline.day(day)  # IndexError ya, no al consumir
        if astronaut_id is not None:
            self._config.member(astronaut_id)
            return self._astronaut_day(astronaut_id, day)
        streams = [self._astronaut_day(m.astronaut_id, day) for m in self._config.crew]
        return heapq.merge(*streams, key=lambda e: e.at)

    def sessions(self, day: int, astronaut_id: str) -> tuple[SessionPlan, ...]:
        key = (astronaut_id, day)
        plans = self._plans.get(key)
        if plans is None:
            self._config.member(astronaut_id)
            plans = sess.plan_day(self._config, self._seed, astronaut_id, self._timeline.day(day))
            self._plans[key] = plans
        return plans

    def session_plan(self, ref: SessionRef) -> SessionPlan:
        """Plan de la sesión `ref` (KeyError si ese día no hay sesión en ese hueco)."""
        for plan in self.sessions(ref.day, ref.astronaut_id):
            if plan.ref == ref:
                return plan
        raise KeyError(f"no session {ref.slot!r} for {ref.astronaut_id} on day {ref.day}")

    def session_messages(self, ref: SessionRef, *, gravity_environment: GravityEnvironment | None = None) -> Iterator[Envelope]:
        """Session, calibraciones registradas en ella, chunks y ground truth de la sesión, como en la misión
        (con `gravity_environment` distinta solo cambia lo que depende de la gravedad)."""
        self.session_plan(ref)
        return self._astronaut_day(ref.astronaut_id, ref.day, target=ref.slot, gravity=gravity_environment)

    def resimulate(self, ref: SessionRef, gravity_environment: GravityEnvironment) -> list[Envelope]:
        return sess.resimulate(self, ref, gravity_environment)

    # --- Recorrido de un astronauta-día -----------------------------------------------------------

    def _astronaut_day(
        self,
        astronaut_id: str,
        day_index: int,
        *,
        target: str | None = None,
        gravity: GravityEnvironment | None = None,
    ) -> Iterator[Envelope]:
        config, seed, tree = self._config, self._seed, self._tree
        member = config.member(astronaut_id)
        device_id = member.device_id
        day = self._timeline.day(day_index)
        state = self._latents.state(astronaut_id, day_index)
        device_plan = plan_device_day(config, seed, astronaut_id, day)
        device = SimDevice(config, device_plan)
        plans = self.sessions(day_index, astronaut_id)
        scheduled = sess.scheduled_calibrations(config, seed, self._timeline, astronaut_id, day)
        full = target is None
        out = _Reorder()

        def envelope(kind: EnvelopeKind, model: Any, at: datetime, **kw: Any) -> Envelope:
            return Envelope.of(kind, model, at=at, astronaut_id=astronaut_id, day=day_index, **kw)

        def ground_truth(record: dict[str, Any], at: datetime) -> Envelope:
            return Envelope(EnvelopeKind.GROUND_TRUTH, None, record, at, False, astronaut_id, day_index)

        clock_rng = tree.rng(astronaut_id, day_index, "clock_sync")
        for at in _clock_times(config, device_plan):
            sync = device.clock_sync(clock_rng, at)
            if full and sync is not None:
                out.push(envelope(EnvelopeKind.CLOCK_SYNC, sync, at, topic=topics.clock(astronaut_id, device_id), retained=True))

        if full:
            activity = self._latents.activity(astronaut_id, day_index)
            context = reports.build_daily_context(tree.rng(astronaut_id, day_index, "daily_context"), config, member, day, activity=activity, state=state)
            out.push(envelope(EnvelopeKind.DAILY_CONTEXT, context, day.start))
            out.push(ground_truth(truth.latent_record(state, day, activity), day.start))
            out.push(ground_truth(truth.device_record(device_plan, day), day.start))
            report = reports.build_daily_self_report(
                tree.rng(astronaut_id, day_index, "self_report"), config, member, day, state=state,
                report_id=tree.uuid(astronaut_id, day_index, "self_report"),
            )  # fmt: skip
            out.push(envelope(EnvelopeKind.SELF_REPORT, report, report.timestamp))
            for record in scheduled.values():
                if record.day == day_index and record.session_id is None:
                    out.push(envelope(EnvelopeKind.CALIBRATION, record.model, record.created_at))

        # Eventos del día en orden: inicio de tramo (0) < fin de sesión (1) < instante de estado (2).
        chunk = sess.chunk_delta(config)
        events: list[tuple[datetime, int, int, str, SessionPlan | None, int, int]] = []
        order = itertools.count()
        for plan in plans:
            for k0, k1 in sess.active_chunks(config, plan):
                events.append((plan.start + k0 * chunk, 0, next(order), "start", plan, k0, k1))
            events.append((plan.end, 1, next(order), "end", plan, 0, 0))
        for at in _status_times(config, device_plan):
            events.append((at, 2, next(order), "tick", None, 0, 0))
        events.sort(key=lambda e: e[:3])

        runs: dict[str, _Run] = {}
        last_seq: dict[tuple[int, str], int] = {}
        status_rng = tree.rng(astronaut_id, day_index, "status")

        def drain(run: _Run, until: datetime | None) -> None:
            for cursor in run.cursors:
                for outcome in cursor.drain(until):
                    key = (outcome.boot_id, outcome.stream_id)
                    last_seq[key] = max(last_seq.get(key, -1), outcome.seq)
                    if outcome.chunk is None:
                        run.lost[outcome.stream_id].append(outcome)
                    else:
                        topic = topics.stream(astronaut_id, device_id, outcome.stream_id)
                        out.push(envelope(EnvelopeKind.STREAM_CHUNK, outcome.chunk, outcome.received_at, topic=topic))

        for at, _, _, kind, plan, k0, k1 in events:
            if kind == "start":
                assert plan is not None
                if full or plan.ref.slot == target:
                    run = runs.get(plan.ref.slot)
                    if run is None:
                        g = gravity if gravity is not None else day.gravity_environment
                        in_force, recorded = sess.session_calibrations(config, seed, plan, scheduled)
                        session = sess.build_session(
                            config, plan, day=day, device_plan=device_plan, gravity_environment=g,
                            calibration_ids={s: r.calibration_id for s, r in in_force.items()},
                        )  # fmt: skip
                        out.push(envelope(EnvelopeKind.SESSION, session, plan.start))
                        for record in recorded:
                            out.push(envelope(EnvelopeKind.CALIBRATION, record.model, record.created_at))
                        spec = sess.mission_spec(config, plan, day=day, gravity_environment=g)
                        synthesis = sess.synthesize(config, seed, spec, member=member, state=state, calibrations=in_force)
                        run = _Run(plan, synthesis, g, lost={s: [] for s in plan.stream_ids})
                        runs[plan.ref.slot] = run
                    for stream in run.synthesis.streams:
                        channels, quality = stream.slice(k0, k1)
                        start = stream.chunk_start(k0)
                        outcomes = device.chunks(
                            tree.rng(*run.synthesis.spec.keys, stream.stream_id, "device", k0), stream_id=stream.stream_id,
                            session_id=plan.session_id, start=start, rate_hz=stream.rate_hz, channels=channels,
                            quality=quality, calibration_id=stream.calibration_id,
                        )  # fmt: skip
                        ends = _existing_chunk_ends(device_plan, start, k1 - k0, stream.samples_per_chunk, stream.rate_hz)
                        run.cursors.append(_Cursor(outcomes, ends))
                else:
                    for stream_id in plan.stream_ids:
                        rate = sess.stream_rate(config, stream_id, background=plan.background)
                        spc = config.signals.samples_per_chunk(rate)
                        device.advance(stream_id, start=_chunk_start(plan.start, k0, spc, rate), n_samples=(k1 - k0) * spc, rate_hz=rate)
            elif kind == "end":
                assert plan is not None
                run = runs.get(plan.ref.slot)
                if run is not None:
                    drain(run, None)
                    for record in self._session_truth(run, day_index, state):
                        out.push(ground_truth(record, plan.end))
            else:
                for run in runs.values():
                    drain(run, at)
                if full:
                    out.push(self._status(device, device_plan, status_rng, at, last_seq, astronaut_id, day_index))
                yield from out.release(at)
        for run in runs.values():
            drain(run, None)
        yield from out.release(None)

    def _session_truth(self, run: _Run, day_index: int, state: Any) -> list[dict[str, Any]]:
        plan = run.plan
        session = truth.TruthSession(plan.ref.astronaut_id, day_index, plan.session_id, plan.ref.slot, plan.protocol_id, run.gravity_environment)
        active = sess.active_chunks(self._config, plan) if plan.background else None
        features = truth.expected_features(self._config, run.synthesis, state=state, active=active)
        return [
            truth.quality_faults_record(session, run.synthesis.streams, active=active),
            truth.lost_chunks_record(session, run.lost),
            truth.expected_features_record(session, features),
        ]

    def _status(
        self,
        device: SimDevice,
        device_plan: DevicePlan,
        rng: np.random.Generator,
        at: datetime,
        last_seq: dict[tuple[int, str], int],
        astronaut_id: str,
        day_index: int,
    ) -> Envelope:
        """DeviceStatus con `last_seq` de lo enviado hasta `at` (SimDevice cuenta ya los tramos enteros)."""
        payload = dump(device.status(rng, at))
        epoch = device_plan.epoch_at(at)
        if epoch is not None:
            for entry in payload.get("streams", []):
                seq = last_seq.get((epoch.boot_id, entry["stream_id"]))
                if seq is None:
                    entry.pop("last_seq", None)
                else:
                    entry["last_seq"] = seq
        topic = topics.status(astronaut_id, device_plan.device_id)
        return Envelope(EnvelopeKind.DEVICE_STATUS, topic, payload, at, True, astronaut_id, day_index)

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
        """Chunks (y ground truth) de una sesión pedida por `start_session`, en orden de chunk.

        Usa el protocolo `protocol_id` del config si existe; al acabarlo (o desde el principio si no
        existe) sigue en reposo indefinidamente: el iterador no termina, el gateway lo corta con
        `stop_session` o al pasar `max_duration_s`. `stream_ids` vacío = los del protocolo (o todos los del
        dispositivo). `session_type` es el del comando; la señal la decide el protocolo. RNG:
        `(astronaut_id, "live", session_id, ...)`. Ver `_live_chunks` para el troceo.
        """
        config = self._config
        member = config.member(astronaut_id)
        info = self._timeline.day(day)
        protocol = config.protocols.get(protocol_id)
        if stream_ids:
            streams = tuple(stream_ids)
        elif protocol is not None:
            streams = tuple(protocol.streams)
        else:
            streams = tuple(s.stream_id for s in config.device.streams)
        for stream_id in streams:
            config.device.stream(stream_id)
        return self._live_session_iter(
            astronaut_id=member.astronaut_id, info=info, device=device, session_id=session_id,
            protocol_id=protocol_id, streams=streams, start=start,
        )  # fmt: skip

    def _live_session_iter(
        self,
        *,
        astronaut_id: str,
        info: DayInfo,
        device: SimDevice,
        session_id: str,
        protocol_id: str,
        streams: tuple[str, ...],
        start: datetime,
    ) -> Iterator[Envelope]:
        config = self._config
        protocol = config.protocols.get(protocol_id)
        state = self._latents.state(astronaut_id, info.index)
        calibrations = self._live_in_force(astronaut_id, info, streams)
        keys = (astronaut_id, "live", session_id)
        session = truth.TruthSession(astronaut_id, info.index, session_id, None, protocol_id, info.gravity_environment)
        residual: dict[str, float] = {}
        cursor = start
        if protocol is not None:
            exercise = isinstance(protocol, ExerciseProtocol)
            spec = self._live_spec(
                (*keys, "protocol"), cursor, sess.chunks_for(config, sess.protocol_duration_s(protocol)), streams, info,
                background=False, reps=rep_schedule(protocol) if exercise else (), protocol=protocol if exercise else None, residual=residual,
            )  # fmt: skip
            synthesis = sess.synthesize(config, self._seed, spec, member=config.member(astronaut_id), state=state, calibrations=calibrations)
            features = truth.expected_features(config, synthesis, state=state)
            yield Envelope(EnvelopeKind.GROUND_TRUTH, None, truth.expected_features_record(session, features), start, False, astronaut_id, info.index)
            yield from self._live_chunks(device, synthesis, session, astronaut_id, info.index)
            residual = synthesis.final_residual_mm
            cursor = cursor + spec.n_chunks * sess.chunk_delta(config)
        for block in itertools.count():
            spec = self._live_spec((*keys, "rest", block), cursor, _live_block_chunks(config), streams, info, background=False, reps=(), protocol=None, residual=residual)
            synthesis = sess.synthesize(config, self._seed, spec, member=config.member(astronaut_id), state=state, calibrations=calibrations)
            yield from self._live_chunks(device, synthesis, session, astronaut_id, info.index)
            residual = synthesis.final_residual_mm
            cursor = cursor + spec.n_chunks * sess.chunk_delta(config)

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
        """Calibraciones (una por stream) de un `calibrate`; su calibration_id va en el ack `completed`.

        Pasan a ser las vigentes de esos streams en las siguientes `live_session`/`live_background`.
        ValueError si `stream_ids` está vacío o algún stream no es del tipo de la calibración (nada se registra).
        """
        config = self._config
        member = config.member(astronaut_id)
        info = self._timeline.day(day)
        kinds = {"stretch": StreamKind.STRETCH, "emg_mvc": StreamKind.EMG, "imu": StreamKind.IMU}
        builders: dict[str, Callable[..., CalibrationRecord]] = {
            "stretch": sess.stretch_calibration, "emg_mvc": sess.emg_mvc_calibration, "imu": sess.imu_calibration,
        }  # fmt: skip
        if calibration_type not in kinds:
            raise ValueError(f"unknown calibration_type {calibration_type!r}")
        if not stream_ids:
            raise ValueError("calibrate needs at least one stream_id")
        for stream_id in stream_ids:
            kind = config.device.stream(stream_id).stream_kind
            if kind is not kinds[calibration_type]:
                raise ValueError(f"{calibration_type} calibration does not apply to {kind.value} stream {stream_id}")
        out: list[Envelope] = []
        for stream_id in stream_ids:
            keys = (astronaut_id, "live", session_id, "calibration", calibration_type, stream_id, at.isoformat())
            record = builders[calibration_type](
                config, self._tree.rng(*keys), member, calibration_id=self._tree.uuid(*keys), stream_id=stream_id,
                session_id=session_id, created_at=at, day=info.index,
            )  # fmt: skip
            self._live_calibrations[(astronaut_id, stream_id)] = record
            out.append(Envelope.of(EnvelopeKind.CALIBRATION, record.model, at=at, astronaut_id=astronaut_id, day=info.index))
        return out

    def live_background(
        self,
        *,
        astronaut_id: str,
        day: int,
        device: SimDevice,
        session_id: str,
        start: datetime,
    ) -> Iterator[Envelope]:
        """Chunks de fondo (IMU + cordón a las frecuencias reducidas) desde `start`, sin fin.

        Para pausar, el gateway deja de consumir; para reanudar pide otro iterador con el `start` nuevo y el
        mismo `session_id` (el seq sigue en `device`). RNG `(astronaut_id, "live", session_id, start, ...)`.
        """
        config = self._config
        config.member(astronaut_id)
        info = self._timeline.day(day)
        return self._live_background_iter(astronaut_id, info, device, session_id, start)

    def _live_background_iter(self, astronaut_id: str, info: DayInfo, device: SimDevice, session_id: str, start: datetime) -> Iterator[Envelope]:
        config = self._config
        background = config.schedule.background
        streams = tuple(background.streams)
        state = self._latents.state(astronaut_id, info.index)
        calibrations = self._live_in_force(astronaut_id, info, streams)
        session = truth.TruthSession(astronaut_id, info.index, session_id, None, background.protocol_id, info.gravity_environment)
        keys = (astronaut_id, "live", session_id, start.isoformat())
        residual: dict[str, float] = {}
        cursor = start
        for block in itertools.count():
            spec = self._live_spec((*keys, block), cursor, _live_block_chunks(config), streams, info, background=True, reps=(), protocol=None, residual=residual)
            synthesis = sess.synthesize(config, self._seed, spec, member=config.member(astronaut_id), state=state, calibrations=calibrations)
            yield from self._live_chunks(device, synthesis, session, astronaut_id, info.index)
            residual = synthesis.final_residual_mm
            cursor = cursor + spec.n_chunks * sess.chunk_delta(config)

    def _live_in_force(self, astronaut_id: str, info: DayInfo, streams: Sequence[str]) -> dict[str, CalibrationRecord]:
        """Calibración vigente por stream: la última hecha en vivo, o la programada de la misión ese día."""
        scheduled = sess.scheduled_calibrations(self._config, self._seed, self._timeline, astronaut_id, info)
        out: dict[str, CalibrationRecord] = {}
        for stream_id in streams:
            record = self._live_calibrations.get((astronaut_id, stream_id)) or scheduled.get(stream_id)
            if record is not None:
                out[stream_id] = record
        return out

    def _live_spec(
        self,
        keys: tuple[Any, ...],
        start: datetime,
        n_chunks: int,
        streams: tuple[str, ...],
        info: DayInfo,
        *,
        background: bool,
        reps: tuple[Any, ...],
        protocol: ExerciseProtocol | None,
        residual: dict[str, float],
    ) -> SynthSpec:
        device = self._config.device
        return SynthSpec(
            keys=keys, start=start, n_chunks=n_chunks, stream_ids=streams, background=background, reps=reps, protocol=protocol,
            gravity_environment=info.gravity_environment,
            awake_from=info.at(device.power_on_time_utc),
            awake_to=info.at(device.power_off_time_utc),  # del día de misión, no del bloque: pasadas las 00:00 UTC la fracción despierta no se reinicia
            initial_residual_mm=dict(residual),
        )  # fmt: skip

    def _live_chunks(self, device: SimDevice, synthesis: SessionSynthesis, session: truth.TruthSession, astronaut_id: str, day_index: int) -> Iterator[Envelope]:
        """Trocea un bloque en vivo llamando a `SimDevice.chunks` chunk a chunk (perezosamente), así el seq
        del dispositivo nunca va por delante de lo consumido: si el gateway corta el iterador, la siguiente
        sesión sigue el seq sin huecos. Contrapartida: cada pérdida es de un solo chunk (sin ráfagas ni
        `stalled`). El `packet_gap` del chunk siguiente a una pérdida se añade aquí. Ground truth del bloque
        (quality_faults / lost_chunks) al final, solo si hay algo.
        """
        config = self._config
        loss = config.faults.device.packet_loss
        gap = QualityState(loss.packet_gap_score, frozenset({QualityFlag.PACKET_GAP}))
        device_id = config.member(astronaut_id).device_id
        spec = synthesis.spec
        previous_lost = device.live_previous_lost  # por stream y en el dispositivo: la pérdida al final de un bloque marca el primer chunk del siguiente
        lost: dict[str, list[ChunkOutcome]] = {s.stream_id: [] for s in synthesis.streams}
        for k in range(spec.n_chunks):
            batch: list[Envelope] = []
            for stream in synthesis.streams:
                channels, quality = stream.slice(k, k + 1)
                if previous_lost.get(stream.stream_id, False):
                    quality = [{key: state.merge(gap) for key, state in quality[0].items()}]
                outcomes = list(
                    device.chunks(
                        self._tree.rng(*spec.keys, stream.stream_id, "device", k), stream_id=stream.stream_id,
                        session_id=session.session_id, start=stream.chunk_start(k), rate_hz=stream.rate_hz, channels=channels,
                        quality=quality, calibration_id=stream.calibration_id,
                    )
                )  # fmt: skip
                if not outcomes:
                    previous_lost[stream.stream_id] = False
                    continue
                outcome = outcomes[0]
                previous_lost[stream.stream_id] = outcome.chunk is None
                if outcome.chunk is None:
                    lost[stream.stream_id].append(outcome)
                else:
                    topic = topics.stream(astronaut_id, device_id, stream.stream_id)
                    batch.append(Envelope.of(EnvelopeKind.STREAM_CHUNK, outcome.chunk, at=outcome.received_at, astronaut_id=astronaut_id, day=day_index, topic=topic))
            batch.sort(key=lambda e: e.at)
            yield from batch
        end = spec.start + spec.n_chunks * sess.chunk_delta(config)
        if any(s.episodes for s in synthesis.streams):
            record = truth.quality_faults_record(session, synthesis.streams)
            yield Envelope(EnvelopeKind.GROUND_TRUTH, None, record, end, False, astronaut_id, day_index)
        if any(lost.values()):
            yield Envelope(EnvelopeKind.GROUND_TRUTH, None, truth.lost_chunks_record(session, lost), end, False, astronaut_id, day_index)


def _live_block_chunks(config: MissionConfig) -> int:
    """Chunks por bloque de síntesis en vivo: un intervalo de DeviceStatus (las flexiones del fondo no
    cruzan bloques; el cordón encadena su residuo entre bloques)."""
    return max(1, round(config.device.status_interval_s * 1000.0 / config.signals.chunk_ms))


def _clock_times(config: MissionConfig, plan: DevicePlan) -> list[datetime]:
    """ClockSync al arrancar y cada `clock_sync_interval_s` mientras dura cada arranque."""
    step = timedelta(seconds=config.device.clock_sync_interval_s)
    times: list[datetime] = []
    for boot in plan.boots:
        end = boot.end_at if boot.end_at is not None else plan.power_off
        at = boot.boot_at
        while end is None or at < end:
            times.append(at)
            at += step
            if end is None:  # pragma: no cover  (los planes diarios siempre tienen apagado)
                break
    return times


def _status_times(config: MissionConfig, plan: DevicePlan) -> list[datetime]:
    """DeviceStatus al encender, cada `status_interval_s` y uno al apagar (offline)."""
    assert plan.power_off is not None
    step = timedelta(seconds=config.device.status_interval_s)
    times: list[datetime] = []
    at = plan.power_on
    while at < plan.power_off:
        times.append(at)
        at += step
    times.append(plan.power_off)
    return times


__all__ = ["MissionGenerator", "SessionRef", "StreamSynthesis"]
