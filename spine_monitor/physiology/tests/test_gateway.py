"""Gateway falso con un cliente MQTT falso y tiempo virtual (sin broker)."""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
SCHEMAS = {"stream": "telemetry/stream-chunk", "status": "telemetry/device-status", "clock": "telemetry/clock-sync", "ack": "telemetry/command-ack"}


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
        return self.topic.split("/")[-2] if "/stream/" in self.topic else self.topic.rsplit("/", 1)[-1]


@dataclass
class Message:
    topic: str
    payload: bytes


class FakeClient:
    def __init__(self) -> None:
        self.published: list[Published] = []
        self.subscriptions: list[tuple[str, int]] = []
        self.queue: asyncio.Queue[Message] = asyncio.Queue()

    async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        self.published.append(Published(topic, json.loads(payload), qos, retain))

    async def subscribe(self, topic: str, qos: int) -> None:
        self.subscriptions.append((topic, qos))

    @property
    def messages(self):
        return self._messages()

    async def _messages(self):
        while True:
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
