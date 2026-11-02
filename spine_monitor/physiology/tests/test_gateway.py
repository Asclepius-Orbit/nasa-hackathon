"""Gateway falso con un cliente MQTT falso y tiempo virtual (sin broker)."""

import asyncio
import itertools
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiomqtt
import pytest
import yaml
from spine_contracts import topics, validation
from spine_physiology import MissionGenerator
from spine_physiology.config import MissionConfig
from spine_physiology.gateway.app import FakeGateway, _serve
from spine_physiology.gateway.settings import GatewaySettings

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
AST, DEV = "AST-01", "sim-01"
SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"
OTHER = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a02"
SCHEMAS = {
    "stream": "telemetry/stream-chunk", "status": "telemetry/device-status", "clock": "telemetry/clock-sync", "ack": "telemetry/command-ack",
    "calibration": "domain/calibration",
}  # fmt: skip


def make_config(edit=None) -> MissionConfig:
    """Ejemplo con chunks de 500 ms (menos chunks que validar), sin pérdidas ni reinicios."""
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw["signals"]["chunk_ms"] = 500
    raw["faults"]["device"]["packet_loss"]["probability"] = 0.0
    raw["faults"]["device"]["reboot"]["probability_per_hour"] = 0.0
    if edit:
        edit(raw)
    return MissionConfig.model_validate(raw)


def settings(**kw: Any) -> GatewaySettings:
    return GatewaySettings(config=EXAMPLE, speed=kw.pop("speed", 0.0), start_day=kw.pop("start_day", 4), **kw)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class Published:
    topic: str
    payload: dict
    qos: int
    retain: bool

    @property
    def channel(self) -> str:
        return self.topic.split("/")[-2] if "/stream/" in self.topic or "/calibration/" in self.topic else self.topic.rsplit("/", 1)[-1]


@dataclass
class Message:
    topic: str
    payload: bytes


class FakeClient:
    """`drop_after`: el broker se cae al intentar publicar el chunk número `drop_after` (no llega) y desde
    entonces todo publish falla. `drop_messages`: la recepción se corta en cuanto se escucha."""

    def __init__(self, *, drop_after: int | None = None, drop_messages: bool = False) -> None:
        self.published: list[Published] = []
        self.subscriptions: list[tuple[str, int]] = []
        self.queue: asyncio.Queue[Message] = asyncio.Queue()
        self.drop_after = drop_after
        self.drop_messages = drop_messages
        self.dropped = False

    async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        if self.drop_after is not None and "/stream/" in topic and len(self.on("stream")) + 1 >= self.drop_after:
            self.dropped = True
        if self.dropped:
            raise aiomqtt.MqttError("Disconnected")
        self.published.append(Published(topic, json.loads(payload), qos, retain))

    async def subscribe(self, topic: str, qos: int) -> None:
        self.subscriptions.append((topic, qos))

    @property
    def messages(self):
        return self._messages()

    async def _messages(self):
        while True:
            if self.drop_messages:
                raise aiomqtt.MqttError("Disconnected during message iteration")
            yield await self.queue.get()

    def on(self, channel: str) -> list[Published]:
        return [p for p in self.published if p.channel == channel]

    def acks(self, command_id: str) -> list[dict]:
        return [p.payload for p in self.on("ack") if p.payload["command_id"] == command_id]


def command(kind: str, n: int, **fields: Any) -> dict:
    return {
        "schema_version": "1.0.0", "command_id": f"c0ffee00-0000-4000-8000-{n:012d}", "astronaut_id": AST,
        "device_id": DEV, "issued_at": "2026-11-02T09:00:00Z", "type": kind, **fields,
    }  # fmt: skip


def start(n: int, session_id: str = SESSION, **fields: Any) -> dict:
    fields.setdefault("session_type", "exercise")
    fields.setdefault("protocol_id", "lat_pulldown_standing_v1")
    return command("start_session", n, session_id=session_id, **fields)


def stop(n: int, session_id: str = SESSION) -> dict:
    return command("stop_session", n, session_id=session_id, reason="completed")


class Harness:
    """Gateway + cliente falso + reloj virtual. `run(seconds)` avanza el reloj y publica lo que toca."""

    def __init__(self, config: MissionConfig | None = None, **kw: Any) -> None:
        self.config = config or make_config()
        self.clock = Clock()
        self.client = FakeClient()
        self.gateway = FakeGateway(MissionGenerator(self.config), settings(**kw), clock=self.clock, astronaut_ids=[AST])

    async def send(self, payload: dict | bytes, topic: str | None = None) -> None:
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        await self.gateway.handle(self.client, topic or topics.cmd(AST, DEV), raw)

    async def run(self, seconds: float, step: float = 0.5) -> None:
        end = self.clock.now + timedelta(seconds=seconds)
        while True:
            await self.gateway.tick(self.client)
            if self.clock.now >= end:
                return
            self.clock.now = min(end, self.clock.now + timedelta(seconds=step))


def go(coro):
    return asyncio.run(coro)


def assert_all_valid(client: FakeClient) -> None:
    for p in client.published:
        assert p.qos == 1
        assert validation.errors(SCHEMAS[p.channel], p.payload) == [], (p.topic, p.payload)
        if p.channel == "calibration":
            assert p.retain and p.topic == topics.calibration(p.payload["astronaut_id"], p.payload["device_id"], p.payload["stream_id"])


def assert_calibrations_resolvable(published: list[Published], *, superseded_ok: bool = False) -> int:
    """Cada chunk con calibration_id llega cuando la retained de `topics.calibration` de su stream es esa
    calibración (publicada antes por este cliente): un consumidor tardío siempre la resuelve. Con
    `superseded_ok`, también vale una que fue la retained de ese topic antes (el resto del bloque en curso
    tras un `calibrate`). Devuelve cuántos chunks la citan."""
    retained: dict[str, str] = {}
    history: dict[str, set[str]] = {}
    cited = 0
    for p in published:
        if p.channel == "calibration":
            retained[p.topic] = p.payload["calibration_id"]
            history.setdefault(p.topic, set()).add(p.payload["calibration_id"])
        elif p.channel == "stream" and "calibration_id" in p.payload:
            c = p.payload
            topic = topics.calibration(c["astronaut_id"], c["device_id"], c["stream_id"])
            ok = retained.get(topic) == c["calibration_id"] or (superseded_ok and c["calibration_id"] in history.get(topic, set()))
            assert ok, (c["stream_id"], c["seq"], c["calibration_id"], retained.get(topic))
            cited += 1
    return cited


def assert_retained_calibrations_never_regress(published: list[Published]) -> None:
    """En cada topic de calibración, una vez sale otro calibration_id no vuelve a salir uno anterior."""
    seen: dict[str, list[str]] = {}
    for p in published:
        if p.channel == "calibration":
            ids = seen.setdefault(p.topic, [])
            calibration_id = p.payload["calibration_id"]
            assert not ids or ids[-1] == calibration_id or calibration_id not in ids, (p.topic, ids, calibration_id)
            ids.append(calibration_id)


def chunks(client: FakeClient, session_id: str | None = None) -> list[dict]:
    return [p.payload for p in client.on("stream") if session_id is None or p.payload["session_id"] == session_id]


def seqs_per_stream(payloads: list[dict]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for p in payloads:
        out.setdefault(p["stream_id"], []).append(p["seq"])
    return out


# --- arranque -------------------------------------------------------------------------------------


def test_first_tick_publishes_retained_status_and_clock_and_background():
    async def scenario():
        h = Harness()
        await h.run(3)
        status, clock = h.client.on("status"), h.client.on("clock")
        assert status and clock and all(p.retain for p in status + clock)
        assert status[0].topic == topics.status(AST, DEV) and status[0].payload["status"] == "ok"
        assert clock[0].topic == topics.clock(AST, DEV)
        background = chunks(h.client)
        assert background and {c["stream_id"] for c in background} == set(h.config.schedule.background.streams)
        assert all(c["source"] == "simulator" for c in background)
        assert all(not p.retain for p in h.client.on("stream"))
        assert_all_valid(h.client)

    go(scenario())


def test_status_and_clock_follow_their_intervals():
    async def scenario():
        h = Harness(make_config(lambda raw: raw["schedule"]["background"].update(enabled=False)))
        await h.run(125, step=5)
        device = h.config.device
        assert len(h.client.on("status")) == 1 + int(125 // device.status_interval_s)
        assert len(h.client.on("clock")) == 1 + int(125 // device.clock_sync_interval_s)
        assert chunks(h.client) == []  # sin fondo ni sesión no se publica nada en stream

    go(scenario())


# --- sesiones -------------------------------------------------------------------------------------


def test_start_session_acks_and_streams_its_chunks():
    async def scenario():
        h = Harness()
        await h.run(2)
        await h.send(start(1))
        assert [a["status"] for a in h.client.acks(start(1)["command_id"])] == ["accepted"]
        await h.run(5)
        session = chunks(h.client, SESSION)
        protocol = h.config.protocol("lat_pulldown_standing_v1")
        assert {c["stream_id"] for c in session} == set(protocol.streams)
        assert all(c["source"] == "simulator" and c["astronaut_id"] == AST and c["device_id"] == DEV for c in session)
        for p in h.client.on("stream"):
            assert p.topic == topics.stream(AST, DEV, p.payload["stream_id"])
        assert_all_valid(h.client)

    go(scenario())


def test_second_start_is_rejected_busy_and_unknown_stream_is_rejected():
    async def scenario():
        h = Harness()
        await h.send(start(1))
        await h.send(start(2, OTHER))
        (busy,) = h.client.acks(start(2)["command_id"])
        assert busy["status"] == "rejected" and busy["error"]["code"] == "busy"
        await h.send(stop(3))
        await h.send(start(4, OTHER, stream_ids=["nope_stream"]))
        (unknown,) = h.client.acks(start(4)["command_id"])
        assert unknown["status"] == "rejected" and unknown["error"]["code"] == "unknown_stream"
        assert_all_valid(h.client)

    go(scenario())


def test_stop_completes_the_session_and_background_pauses_and_resumes_with_continuous_seq():
    async def scenario():
        h = Harness()
        await h.run(4)
        before = len(h.client.published)
        await h.send(start(1, stream_ids=["imu_l3", "stretch_lumbar"]))
        await h.run(6)
        await h.send(stop(2))
        after_stop = len(h.client.published)
        await h.run(4)

        assert [a["status"] for a in h.client.acks(start(1)["command_id"])] == ["accepted", "completed"]
        assert [a["status"] for a in h.client.acks(stop(2)["command_id"])] == ["accepted", "completed"]
        background_id = chunks(h.client)[0]["session_id"]
        during = [p.payload for p in h.client.published[before:after_stop] if p.channel == "stream"]
        first_session = next(i for i, c in enumerate(during) if c["session_id"] == SESSION)
        # Tras el primer chunk de la sesión no hay fondo hasta el stop (solo el vaciado justo al empezar).
        assert all(c["session_id"] == SESSION for c in during[first_session:])
        resumed = [p.payload for p in h.client.published[after_stop:] if p.channel == "stream"]
        assert resumed and {c["session_id"] for c in resumed} == {background_id}
        # Mismo stream_id, el seq sigue sin huecos: fondo -> sesión -> fondo.
        for stream, seq in seqs_per_stream(chunks(h.client)).items():
            assert seq == list(range(len(seq))), stream
        # Y los tramos no se solapan en el tiempo del dispositivo.
        for stream in ("imu_l3", "stretch_lumbar"):
            t0 = [c["t0_device_ms"] for c in chunks(h.client) if c["stream_id"] == stream]
            assert t0 == sorted(t0)
        assert_all_valid(h.client)

    go(scenario())


def test_stop_without_matching_session_is_rejected_no_session():
    async def scenario():
        h = Harness()
        await h.send(stop(1))
        await h.send(start(2))
        await h.send(stop(3, OTHER))
        for n in (1, 3):
            (ack,) = h.client.acks(stop(n)["command_id"])
            assert ack["status"] == "rejected" and ack["error"]["code"] == "no_session"
        assert_all_valid(h.client)

    go(scenario())


def test_max_duration_ends_the_session():
    async def scenario():
        h = Harness()
        await h.send(start(1, max_duration_s=3))
        await h.run(8)
        assert [a["status"] for a in h.client.acks(start(1)["command_id"])] == ["accepted", "completed"]
        session = chunks(h.client, SESSION)
        assert session
        ends = [datetime.fromisoformat(c["received_at"]) for c in session]
        assert max(ends) <= T0 + timedelta(seconds=3 + 1)  # como mucho el chunk en vuelo al cortar
        # Después vuelve el fondo.
        assert chunks(h.client)[-1]["session_id"] != SESSION
        await h.send(start(2, OTHER))  # ya no está ocupado
        assert h.client.acks(start(2)["command_id"])[0]["status"] == "accepted"

    go(scenario())


def test_mark_event_needs_the_running_session():
    async def scenario():
        h = Harness()
        mark = command("mark_event", 1, session_id=SESSION, event_type="rep_start", rep_index=1)
        await h.send(mark)
        assert h.client.acks(mark["command_id"])[0]["error"]["code"] == "no_session"
        await h.send(start(2))
        mark = command("mark_event", 3, session_id=SESSION, event_type="rep_start", rep_index=1)
        await h.send(mark)
        assert [a["status"] for a in h.client.acks(mark["command_id"])] == ["accepted", "completed"]
        assert_all_valid(h.client)

    go(scenario())


# --- calibración ----------------------------------------------------------------------------------


def test_calibrate_completes_with_a_calibration_id_used_by_the_next_session():
    async def scenario():
        h = Harness()
        cal = command("calibrate", 1, session_id=SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"], duration_s=2)
        await h.send(cal)
        assert [a["status"] for a in h.client.acks(cal["command_id"])] == ["accepted"]
        await h.run(3)
        _, completed = h.client.acks(cal["command_id"])
        assert completed["status"] == "completed"
        calibration_id = completed["result"]["calibration_id"]
        assert datetime.fromisoformat(completed["timestamp"]) == T0 + timedelta(seconds=2)
        await h.send(start(2, session_type="spine_check", protocol_id="spine_length_rest_v1"))
        await h.run(3)
        stretch = [c for c in chunks(h.client, SESSION) if c["stream_kind"] == "stretch"]
        assert stretch and {c["calibration_id"] for c in stretch} == {calibration_id}
        assert_all_valid(h.client)

    go(scenario())


def test_calibrate_publishes_retained_calibrations_right_before_its_completed_ack():
    async def scenario():
        h = Harness(make_config(lambda raw: raw["schedule"]["background"].update(enabled=False)))
        cal = command("calibrate", 1, session_id=SESSION, calibration_type="emg_mvc", stream_ids=["emg_lat_left", "emg_lat_right"], duration_s=2)
        await h.send(cal)
        await h.run(1)
        assert h.client.on("calibration") == []  # aún no ha acabado
        await h.run(2)
        published = [(p.channel, p.payload) for p in h.client.published if p.channel in ("calibration", "ack")]
        accepted, first, second, completed = published
        assert accepted[1]["status"] == "accepted" and completed[1]["status"] == "completed"
        assert [first[1]["stream_id"], second[1]["stream_id"]] == ["emg_lat_left", "emg_lat_right"]
        assert completed[1]["result"]["calibration_id"] == first[1]["calibration_id"]
        assert all(p[1]["calibration_type"] == "emg_mvc" and p[1]["session_id"] == SESSION for p in (first, second))
        assert all(p.retain and p.qos == 1 for p in h.client.on("calibration"))
        await h.send(cal)  # reentrega: mismos acks, sin volver a publicar calibraciones
        assert len(h.client.on("calibration")) == 2 and len(h.client.acks(cal["command_id"])) == 4
        assert_all_valid(h.client)

    go(scenario())


def test_every_chunk_citing_a_calibration_comes_after_its_retained_calibration():
    async def scenario():
        h = Harness()
        await h.run(3)
        # El fondo cita la calibración programada de la misión: ya está retained antes de su primer chunk.
        scheduled = {c["calibration_id"] for c in chunks(h.client) if "calibration_id" in c}
        assert len(scheduled) == 1 and h.client.on("calibration")[0].payload["calibration_id"] in scheduled
        cal = command("calibrate", 1, session_id=SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"])
        await h.send(cal)
        live = h.client.acks(cal["command_id"])[-1]["result"]["calibration_id"]
        await h.run(3)
        await h.send(start(2, session_type="spine_check", protocol_id="spine_length_rest_v1"))
        await h.run(4)
        await h.send(stop(3))
        await h.run(4)
        cited = {c["calibration_id"] for c in chunks(h.client) if "calibration_id" in c}
        assert live in cited and cited <= {p.payload["calibration_id"] for p in h.client.on("calibration")}
        # Tras el calibrate, el resto del bloque de fondo en curso aún cita la anterior (ya retained antes).
        assert assert_calibrations_resolvable(h.client.published, superseded_ok=True) > 10
        assert_retained_calibrations_never_regress(h.client.published)
        assert not any(p.channel not in SCHEMAS for p in h.client.published)  # el ground truth nunca va al bus
        assert_all_valid(h.client)

    go(scenario())


def stretch_ids_after(client: FakeClient, mark: int) -> list[str]:
    return [p.payload["calibration_id"] for p in client.published[mark:] if p.channel == "stream" and p.payload["stream_kind"] == "stretch"]


def test_calibrate_while_background_runs_moves_its_chunks_to_the_new_calibration_from_the_next_block():
    async def scenario():
        h = Harness()
        block_s = h.config.device.status_interval_s  # un bloque en vivo = un intervalo de estado
        await h.run(5)
        (old,) = {c["calibration_id"] for c in chunks(h.client) if c["stream_kind"] == "stretch"}
        cal = command("calibrate", 1, session_id=SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"])
        await h.send(cal)
        accepted, completed = h.client.acks(cal["command_id"])
        new = completed["result"]["calibration_id"]
        assert accepted["status"] == "accepted" and completed["status"] == "completed" and new != old
        mark = len(h.client.published)
        await h.run(2 * block_s)
        ids = stretch_ids_after(h.client, mark)
        assert len({c["session_id"] for c in chunks(h.client)}) == 1  # todo es fondo
        # El resto del bloque en curso cita la anterior; desde el bloque siguiente, la nueva y ya nunca la anterior.
        first_new = ids.index(new)
        assert set(ids[:first_new]) <= {old} and set(ids[first_new:]) == {new} and len(ids) - first_new >= block_s / 2
        # La retained del stream pasa de la anterior a la nueva y no vuelve atrás (antes se republicaba la vieja).
        topic = topics.calibration(AST, DEV, "stretch_lumbar")
        assert [p.payload["calibration_id"] for p in h.client.on("calibration") if p.topic == topic] == [old, new]
        assert_retained_calibrations_never_regress(h.client.published)
        assert assert_calibrations_resolvable(h.client.published, superseded_ok=True) > 0
        assert h.client.acks(cal["command_id"])[-1]["result"]["calibration_id"] == new  # el ack no cambia
        assert_all_valid(h.client)

    go(scenario())


def test_a_calibrate_superseded_before_it_completes_is_not_published_but_keeps_its_ack():
    async def scenario():
        h = Harness()
        await h.run(3)
        (scheduled,) = {c["calibration_id"] for c in chunks(h.client) if c["stream_kind"] == "stretch"}
        slow = command("calibrate", 1, session_id=SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"], duration_s=10)
        fast = command("calibrate", 2, session_id=SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"])
        await h.send(slow)
        await h.send(fast)
        await h.run(h.config.device.status_interval_s + 10)
        slow_completed = h.client.acks(slow["command_id"])[-1]
        fast_id = h.client.acks(fast["command_id"])[-1]["result"]["calibration_id"]
        assert slow_completed["status"] == "completed" and slow_completed["result"]["calibration_id"] not in (fast_id, scheduled)
        topic = topics.calibration(AST, DEV, "stretch_lumbar")
        assert [p.payload["calibration_id"] for p in h.client.on("calibration") if p.topic == topic] == [scheduled, fast_id]
        assert stretch_ids_after(h.client, 0)[-1] == fast_id
        assert_retained_calibrations_never_regress(h.client.published)
        assert_all_valid(h.client)

    go(scenario())


# --- estado: last_seq -----------------------------------------------------------------------------


def test_status_last_seq_never_reports_an_unpublished_seq():
    async def scenario():
        h = Harness()
        await h.run(20)
        await h.send(start(1, stream_ids=["imu_l3", "stretch_lumbar", "emg_lat_left"]))
        await h.run(25)
        await h.send(stop(2))
        await h.run(30)
        seen: dict[tuple[int, str], int] = {}
        statuses = 0
        for p in h.client.published:
            if p.channel == "stream":
                key = (p.payload["boot_id"], p.payload["stream_id"])
                seen[key] = max(seen.get(key, -1), p.payload["seq"])
            elif p.channel == "status" and p.payload["status"] != "offline":
                statuses += 1
                for s in p.payload["streams"]:
                    published = seen.get((p.payload["boot_id"], s["stream_id"]))
                    # Exactamente lo último publicado (el dispositivo ya comprometió el chunk siguiente).
                    assert s.get("last_seq") == published, (p.payload["timestamp"], s)
        assert statuses >= 3
        device = h.gateway._units[0].device
        boot = h.client.on("status")[-1].payload["boot_id"]
        assert any(device.next_seq(boot, s) - 1 > seen[(boot, s)] for s in ("imu_l3", "stretch_lumbar"))  # hay anticipo

    go(scenario())


# --- ritmo y bucle de eventos ---------------------------------------------------------------------


def test_each_source_paces_the_loop_with_its_own_chunk():
    async def scenario():
        config = make_config()  # sesiones a 500 ms, fondo a 1000 ms
        assert config.schedule.background.chunk_ms == 1000.0
        clock = Clock()
        client = FakeClient()
        sleeps: list[tuple[float, bool]] = []
        gateway = FakeGateway(MissionGenerator(config), settings(), clock=clock, sleep=lambda s: virtual(s), astronaut_ids=[AST])

        async def virtual(seconds: float) -> None:
            sleeps.append((seconds, gateway._units[0].session is not None))
            clock.advance(seconds)
            if clock.now >= T0 + timedelta(seconds=6) and not client.acks(start(1)["command_id"]):
                await client.queue.put(Message(topics.cmd(AST, DEV), json.dumps(start(1)).encode()))
            if clock.now >= T0 + timedelta(seconds=12):
                done.set()
            await asyncio.sleep(0)

        done = asyncio.Event()
        task = asyncio.create_task(gateway.run(client))
        await asyncio.wait_for(done.wait(), timeout=60)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        background = [s for s, in_session in sleeps if not in_session]
        session = [s for s, in_session in sleeps if in_session]
        assert session and max(session) <= 0.5
        assert max(background) <= 1.0 and max(background) > 0.5  # el fondo no se despierta cada 500 ms
        t0 = [c["t0_device_ms"] for c in chunks(client) if c["stream_id"] == "imu_l3" and c["session_id"] != SESSION][:4]
        assert len(t0) == 4 and all(abs(b - a - 1000) <= 2 for a, b in itertools.pairwise(t0))
        assert_all_valid(client)

    go(scenario())


def test_long_synthesis_runs_off_the_event_loop():
    async def scenario():
        h = Harness()
        await h.run(2)
        gate = threading.Event()
        released: list[bool] = []
        real = h.gateway._generator.live_session

        def slow_live_session(**kw: Any):
            inner = real(**kw)

            def gen():
                # Síntesis "larga": solo acaba cuando otra tarea del bucle la libera. Si corriera en el
                # bucle, nadie la liberaría y `wait` agotaría su tiempo.
                released.append(gate.wait(timeout=10))
                yield from inner

            return gen()

        h.gateway._generator.live_session = slow_live_session
        await h.send(start(1))
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while not released:
                ticks += 1
                if ticks == 5:
                    gate.set()
                await asyncio.sleep(0)

        ticking = asyncio.create_task(ticker())
        await h.run(3)
        await ticking
        assert released == [True] and ticks >= 5
        session = chunks(h.client, SESSION)
        assert session
        for stream, seq in seqs_per_stream(chunks(h.client)).items():
            assert seq == list(range(len(seq))), stream
        assert_all_valid(h.client)

    go(scenario())


def test_calibrate_rejections():
    async def scenario():
        h = Harness()
        wrong_kind = command("calibrate", 1, session_id=SESSION, calibration_type="imu", stream_ids=["emg_lat_left"])
        unknown = command("calibrate", 2, session_id=SESSION, calibration_type="imu", stream_ids=["nope_stream"])
        await h.send(wrong_kind)
        await h.send(unknown)
        assert h.client.acks(wrong_kind["command_id"])[0]["error"]["code"] == "invalid_command"
        assert h.client.acks(unknown["command_id"])[0]["error"]["code"] == "unknown_stream"
        assert_all_valid(h.client)

    go(scenario())


# --- comandos inválidos ---------------------------------------------------------------------------


def test_invalid_commands_are_rejected_or_ignored_without_crashing():
    async def scenario():
        h = Harness()
        await h.send(b"\xff not json")
        await h.send(b"[1, 2, 3]")
        await h.send({"type": "start_session"})  # sin command_id: nada que responder
        await h.send(start(9), topic=topics.cmd("AST-02", "sim-02"))  # topic de otro gateway
        assert h.client.published == []

        missing = start(1)
        del missing["protocol_id"]
        await h.send(missing)
        bad_type = command("reboot_now", 2)
        await h.send(bad_type)
        foreign = start(3)
        foreign["astronaut_id"], foreign["device_id"] = "AST-02", "sim-02"
        await h.send(foreign)
        for payload in (missing, bad_type, foreign):
            (ack,) = h.client.acks(payload["command_id"])
            assert ack["status"] == "rejected" and ack["error"]["code"] == "invalid_command"
            assert ack["astronaut_id"] == AST and ack["device_id"] == DEV
        # Y sigue funcionando.
        await h.send(start(4))
        assert h.client.acks(start(4)["command_id"])[0]["status"] == "accepted"
        assert_all_valid(h.client)

    go(scenario())


def test_hostile_payloads_never_bring_the_gateway_down():
    async def scenario():
        h = Harness()
        # Anidado hasta provocar RecursionError en json.loads: se ignora (menor que el tope de tamaño).
        deep = b"[" * 10_000 + b"]" * 10_000
        await h.send(deep)
        assert h.client.published == []
        # Demasiado grande con command_id legible: invalid_command sin parsear; sin command_id: se ignora.
        big = start(1)
        big["padding"] = "x" * 20_000
        await h.send(big)
        (ack,) = h.client.acks(start(1)["command_id"])
        assert ack["status"] == "rejected" and ack["error"]["code"] == "invalid_command"
        await h.send(b'{"padding": "' + b"x" * 20_000 + b'"}')
        assert len(h.client.published) == 1
        # Y sigue funcionando.
        await h.send(start(2))
        assert h.client.acks(start(2)["command_id"])[0]["status"] == "accepted"
        assert_all_valid(h.client)

    go(scenario())


def test_failed_accepted_publish_leaves_no_session_running():
    async def scenario():
        h = Harness()
        await h.run(2)
        real = h.client.publish

        async def flaky(topic: str, payload: bytes, qos: int, retain: bool) -> None:
            if topic.endswith("/ack"):
                raise ConnectionError("broker gone")
            await real(topic, payload, qos, retain)

        h.client.publish = flaky
        await h.send(start(1))
        h.client.publish = real
        assert h.gateway._units[0].session is None
        assert h.client.acks(start(1)["command_id"]) == []
        await h.run(2)  # el fondo sigue publicando
        assert chunks(h.client)[-1]["session_id"] != SESSION
        # El cliente reintenta (mismo command_id): se ejecuta de nuevo con normalidad.
        await h.send(start(1))
        assert [a["status"] for a in h.client.acks(start(1)["command_id"])] == ["accepted"]
        assert h.gateway._units[0].session is not None
        assert_all_valid(h.client)

    go(scenario())


def test_redelivered_commands_get_the_same_acks_without_re_executing():
    async def scenario():
        h = Harness()
        await h.send(start(1))
        await h.send(start(1))  # DUP de QoS 1
        assert [a["status"] for a in h.client.acks(start(1)["command_id"])] == ["accepted", "accepted"]
        assert len({json.dumps(a, sort_keys=True) for a in h.client.acks(start(1)["command_id"])}) == 1
        await h.run(2)
        await h.send(stop(2))
        await h.send(stop(2))  # DUP
        assert [a["status"] for a in h.client.acks(stop(2)["command_id"])] == ["accepted", "completed"] * 2
        assert [a["status"] for a in h.client.acks(start(1)["command_id"])][-1] == "completed"
        assert not any(a["status"] == "rejected" for a in h.client.acks(stop(2)["command_id"]))
        assert h.gateway._units[0].session is None
        assert_all_valid(h.client)

    go(scenario())


def test_handled_commands_lru_is_bounded():
    async def scenario():
        h = Harness()
        for n in range(1, 300):
            await h.send(stop(n))  # no_session: cada uno anota su ack
        assert len(h.gateway._units[0].handled) <= 256

    go(scenario())


def test_huge_durations_are_rejected_before_touching_state():
    async def scenario():
        h = Harness()
        await h.run(2)
        await h.send(start(1, max_duration_s=10**12))
        await h.send(command("calibrate", 2, session_id=SESSION, calibration_type="imu", stream_ids=["imu_l3"], duration_s=1e300))
        await h.send(command("calibrate", 3, session_id=SESSION, calibration_type="imu", stream_ids=["imu_l3"], duration_s=1e999))
        for n in (1, 2, 3):
            (ack,) = h.client.acks(start(n)["command_id"])
            assert ack["status"] == "rejected" and ack["error"]["code"] == "invalid_command"
            assert "duration exceeds" in ack["error"]["message"]
        assert h.gateway._units[0].session is None
        await h.run(2)
        assert chunks(h.client)[-1]["session_id"] != SESSION  # el fondo no se paró
        assert_all_valid(h.client)

    go(scenario())


# --- día de misión y dispositivo ------------------------------------------------------------------


def test_mission_day_advances_with_speed_and_is_clamped():
    h = Harness(speed=1440.0, start_day=2)  # un día de misión por minuto
    total = h.config.total_days
    assert h.gateway.mission_day(T0) == 2
    assert h.gateway.mission_day(T0 + timedelta(seconds=59)) == 2
    assert h.gateway.mission_day(T0 + timedelta(seconds=61)) == 3
    assert h.gateway.mission_day(T0 + timedelta(hours=10)) == total - 1
    assert Harness(speed=0.0, start_day=5).gateway.mission_day(T0 + timedelta(days=30)) == 5
    with pytest.raises(ValueError, match="start_day"):
        Harness(start_day=total)


def test_background_gets_a_new_session_id_each_mission_day():
    async def scenario():
        h = Harness(speed=86400.0 / 10, start_day=1)  # un día de misión cada 10 s
        await h.run(25)
        ids = list(dict.fromkeys(c["session_id"] for c in chunks(h.client)))
        assert len(ids) == 3
        for stream, seq in seqs_per_stream(chunks(h.client)).items():
            assert seq == list(range(len(seq))), stream
        assert_all_valid(h.client)

    go(scenario())


def test_device_recharges_after_its_power_window():
    def short_day(raw):
        raw["device"].update(power_on_time_utc="09:00", power_off_time_utc="09:00:20")
        raw["schedule"]["background"].update(start_time_utc="09:00", end_time_utc="09:00:20")
        for s in raw["schedule"]["sessions"]:
            s["time_utc"] = "09:00:10"

    async def scenario():
        h = Harness(make_config(short_day))
        await h.run(30)
        boots = list(dict.fromkeys(p.payload["boot_id"] for p in h.client.on("status")))
        assert len(boots) == 2
        second = [c for c in chunks(h.client) if c["boot_id"] == boots[1]]
        assert second and min(c["seq"] for c in second) == 0
        assert_all_valid(h.client)

    go(scenario())


# --- bucle completo, Last Will y parada -----------------------------------------------------------


def test_run_subscribes_serves_commands_and_publishes_offline_on_shutdown():
    async def scenario():
        clock = Clock()
        client = FakeClient()
        reached = asyncio.Event()

        async def sleep(seconds: float) -> None:  # tiempo virtual
            clock.advance(seconds)
            if clock.now >= T0 + timedelta(seconds=6):
                reached.set()
            await asyncio.sleep(0)

        gateway = FakeGateway(MissionGenerator(make_config()), settings(), clock=clock, sleep=sleep)
        await client.queue.put(Message(topics.cmd(AST, DEV), json.dumps(start(1)).encode()))
        task = asyncio.create_task(gateway.run(client))
        await asyncio.wait_for(reached.wait(), timeout=60)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sorted(client.subscriptions) == [(topics.cmd("AST-01", "sim-01"), 1), (topics.cmd("AST-02", "sim-02"), 1)]
        assert h_status(client, "AST-02")  # también el segundo astronauta
        assert client.acks(start(1)["command_id"])[0]["status"] == "accepted"
        assert chunks(client, SESSION)
        last = [p for p in client.on("status") if p.payload["astronaut_id"] == AST][-1]
        assert last.retain and last.payload["status"] == "offline"
        assert_all_valid(client)

    go(scenario())


def h_status(client: FakeClient, astronaut_id: str) -> list[Published]:
    return [p for p in client.on("status") if p.payload["astronaut_id"] == astronaut_id]


def test_serve_opens_one_connection_per_device_with_its_last_will():
    async def scenario():
        opened: list[dict] = []
        clients: list[FakeClient] = []

        class Factory:
            def __init__(self, **kw: Any) -> None:
                opened.append(kw)
                self.client = FakeClient()
                clients.append(self.client)

            async def __aenter__(self) -> FakeClient:
                return self.client

            async def __aexit__(self, *exc: object) -> None:
                return None

        config = make_config()
        task = asyncio.create_task(_serve(MissionGenerator(config), settings(mqtt_client_id="sim-gw"), client_factory=Factory, clock=lambda: datetime.now(UTC)))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [kw["identifier"] for kw in opened] == ["sim-gw-sim-01", "sim-gw-sim-02"]
        for kw, member in zip(opened, config.crew, strict=True):
            will = kw["will"]
            assert will.topic == topics.status(member.astronaut_id, member.device_id) and will.qos == 1 and will.retain
            payload = json.loads(will.payload)
            assert payload["status"] == "offline" and validation.errors("telemetry/device-status", payload) == []
        for client, member in zip(clients, config.crew, strict=True):
            assert client.subscriptions == [(topics.cmd(member.astronaut_id, member.device_id), 1)]
            assert {p.payload["astronaut_id"] for p in client.published} == {member.astronaut_id}
            assert client.on("status")[-1].payload["status"] == "offline"

    go(scenario())


class FakeConnection:
    """Un intento de conexión: entrar da `client`, o MqttError si es None (el broker la rechaza)."""

    def __init__(self, client: FakeClient | None) -> None:
        self.client = client

    async def __aenter__(self) -> FakeClient:
        if self.client is None:
            raise aiomqtt.MqttError("[code:111] Connection refused")
        return self.client

    async def __aexit__(self, *exc: object) -> None:
        return None


class Connections:
    """client_factory para `_serve`: anota cada intento (kwargs) y da el cliente que diga `plan(identifier,
    intento)`; None = el broker rechaza la conexión."""

    def __init__(self, plan) -> None:
        self.plan = plan
        self.attempts: list[dict] = []

    def __call__(self, **kw: Any) -> FakeConnection:
        self.attempts.append(kw)
        n = sum(1 for a in self.attempts if a["identifier"] == kw["identifier"])
        return FakeConnection(self.plan(kw["identifier"], n))


def test_broker_drop_mid_run_reconnects_resubscribes_and_resumes_without_seq_gaps():
    async def scenario():
        clock = Clock()
        backoff: list[float] = []
        reached = asyncio.Event()
        first = FakeClient(drop_after=30)
        await first.queue.put(Message(topics.cmd(AST, DEV), json.dumps(start(1, session_type="spine_check", protocol_id="spine_length_rest_v1")).encode()))
        second = FakeClient()

        def plan(identifier: str, n: int) -> FakeClient:
            if identifier.endswith("sim-01"):
                return first if n == 1 else second
            return FakeClient()

        async def sleep(seconds: float) -> None:  # tiempo virtual; el ritmo nunca pasa de 1 s, el backoff sí
            if seconds > 1.0:
                backoff.append(seconds)
            clock.advance(seconds)
            if len(chunks(second, SESSION)) >= 20:
                reached.set()
            await asyncio.sleep(0)

        connections = Connections(plan)
        config = make_config()
        task = asyncio.create_task(
            _serve(
                MissionGenerator(config),
                settings(mqtt_reconnect_initial_s=3.0, mqtt_reconnect_max_s=5.0),
                client_factory=connections,
                clock=clock,
                sleep=sleep,
            )
        )
        await asyncio.wait_for(reached.wait(), timeout=60)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert first.dropped and backoff == [3.0]
        ours = [a for a in connections.attempts if a["identifier"].endswith("sim-01")]
        assert len(ours) == 2 and all(a["will"].topic == topics.status(AST, DEV) for a in ours)
        assert second.subscriptions == [(topics.cmd(AST, DEV), 1)]  # se vuelve a suscribir
        # Al reconectar: estado `ok` (el Last Will dejó `offline`) antes que ningún chunk, y la sesión sigue.
        assert second.published[0].channel in ("status", "clock") and second.on("status")[0].payload["status"] == "ok"
        assert chunks(first, SESSION) and chunks(second, SESSION)
        # Ni huecos ni duplicados de seq entre las dos conexiones (el chunk que falló se publica después).
        both = chunks(first) + chunks(second)
        for stream, seq in seqs_per_stream(both).items():
            assert seq == list(range(len(seq))), stream
        # La calibración se vuelve a dejar retained en la conexión nueva antes de sus chunks.
        assert assert_calibrations_resolvable(second.published) > 0
        assert_retained_calibrations_never_regress(first.published + second.published)
        assert second.on("status")[-1].payload["status"] == "offline" and second.on("status")[-1].retain  # SIGTERM
        assert_all_valid(first)
        assert_all_valid(second)

    go(scenario())


def test_reconnect_backoff_grows_caps_and_resets_after_connecting():
    async def scenario():
        clock = Clock()
        backoff: list[float] = []
        final = FakeClient()

        def plan(identifier: str, n: int) -> FakeClient | None:
            if n <= 5:
                return None  # broker caído: conexión rechazada
            return FakeClient(drop_messages=True) if n == 6 else final  # conecta y se corta enseguida; luego bien

        async def sleep(seconds: float) -> None:
            if seconds > 1.0:
                backoff.append(seconds)
            clock.advance(seconds)
            await asyncio.sleep(0)

        connections = Connections(plan)
        config = make_config(lambda raw: raw.update(crew=raw["crew"][:1]))
        s = settings(mqtt_reconnect_initial_s=1.5, mqtt_reconnect_factor=2.0, mqtt_reconnect_max_s=5.0)
        task = asyncio.create_task(_serve(MissionGenerator(config), s, client_factory=connections, clock=clock, sleep=sleep))
        await asyncio.wait_for(_until(lambda: final.on("status")), timeout=60)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert backoff == [1.5, 3.0, 5.0, 5.0, 5.0, 1.5]
        assert len(connections.attempts) == 7 and all(a["will"].retain for a in connections.attempts)
        assert final.subscriptions == [(topics.cmd(AST, DEV), 1)]
        assert final.on("status")[-1].payload["status"] == "offline"

    go(scenario())


async def _until(predicate) -> None:
    while not predicate():
        await asyncio.sleep(0)


def test_reconnect_settings_have_defaults_and_validate_the_cap(monkeypatch):
    s = settings()
    assert (s.mqtt_reconnect_initial_s, s.mqtt_reconnect_factor, s.mqtt_reconnect_max_s) == (1.0, 2.0, 30.0)
    monkeypatch.setenv("SPINE_SIM_MQTT_RECONNECT_MAX_S", "0.5")
    with pytest.raises(ValueError, match="mqtt_reconnect_max_s"):
        settings()
    with pytest.raises(ValueError):
        settings(mqtt_reconnect_factor=0.5)


def test_settings_come_from_spine_sim_env(monkeypatch):
    monkeypatch.setenv("SPINE_SIM_CONFIG", str(EXAMPLE))
    monkeypatch.setenv("SPINE_SIM_SPEED", "60")
    monkeypatch.setenv("SPINE_SIM_START_DAY", "3")
    monkeypatch.setenv("SPINE_SIM_MQTT_HOST", "mosquitto")
    s = GatewaySettings()
    assert (s.config, s.speed, s.start_day, s.mqtt_host, s.seed) == (EXAMPLE, 60.0, 3, "mosquitto", None)
    monkeypatch.delenv("SPINE_SIM_SPEED")
    with pytest.raises(ValueError):
        GatewaySettings()
