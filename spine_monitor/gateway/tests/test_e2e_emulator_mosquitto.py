"""E2E sin placa: emulador del Arduino -> ``SerialLineSource`` (``socket://``) -> ``app.main`` -> Mosquitto real -> observador.

Cadena completa y a ritmo real (nada de relojes de mentira): el emulador es un servidor TCP (``spine_gateway.emulator``), el
gateway lee su puerto serie por ``socket://127.0.0.1:<puerto>`` y publica en el broker real de 127.0.0.1:1883; un observador
aiomqtt suscrito a ``asclepius/v1/crew/<astronauta>/device/<dispositivo>/#`` (QoS 1) acumula lo que llega. Los comandos los
publica el propio observador como lo haría el backend: modelos de ``spine_contracts``, JSON compacto, QoS 1, sin retain.

Todo lo recibido se valida con el schema completo de ``spine_contracts.validation``; los ids del payload deben casar con los del
topic (regla del ingest del backend) y el QoS es 1. El flag retain de un mensaje en vivo no sirve para comprobar si un topic es
retained (MQTT 3.1.1 lo pone a 0 a quien ya estaba suscrito): para eso un suscriptor tardío ve qué entrega el broker retained.

Se salta si no hay broker, salvo con SPINE_REQUIRE_BROKER=1 (el gate de Docker), donde falla. Cada test usa un dispositivo propio
(``itest-e2e-*``) bajo el astronauta ``AST-98`` y borra al final sus retained. Se espera con eventos y ``asyncio.wait_for``, nunca con sleeps fijos.
"""

import asyncio
import contextlib
import json
import os
import socket
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiomqtt
import pytest
from pydantic import SecretStr
from spine_contracts import (
    SCHEMA_VERSION,
    CalibrateCommand,
    MarkEventCommand,
    StartSessionCommand,
    StopSessionCommand,
    dump,
    topics,
    validation,
)
from spine_gateway import app, emulator, status
from spine_gateway.settings import GatewaySettings

pytestmark = pytest.mark.mosquitto

HOST, PORT = "127.0.0.1", 1883
TIMEOUT_S = 25.0
OBSERVER_AUTH = {"username": "spine-gwtest", "password": os.environ["SPINE_TEST_MQTT_PASSWORD"]} if os.getenv("SPINE_TEST_MQTT_PASSWORD") else {}
DEVICE_PASSWORD = os.getenv("SPINE_TEST_MQTT_DEVICE_PASSWORD")
STRETCH, EMG, IMU = "stretch_lumbar", "emg_lat_left", "imu_l3"
ASTRONAUT = "AST-98"
"""Astronauta propio de estos tests: no se mezclan con los datos de AST-01 del broker compartido."""
SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"
SCHEMAS = {
    "stream": "telemetry/stream-chunk",
    "calibration": "domain/calibration",
    "status": "telemetry/device-status",
    "clock": "telemetry/clock-sync",
    "ack": "telemetry/command-ack",
    "cmd": "telemetry/command",
}
RETAINED = {"status", "clock", "calibration"}


def _broker_up() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _require_broker() -> None:
    """Sin broker: salta, salvo con SPINE_REQUIRE_BROKER=1 (el gate de Docker), donde falla."""
    if _broker_up():
        return
    reason = f"no MQTT broker on {HOST}:{PORT}"
    if os.environ.get("SPINE_REQUIRE_BROKER") == "1":
        pytest.fail(f"SPINE_REQUIRE_BROKER=1 but {reason}: the mosquitto test must not be skipped in the gate")
    pytest.skip(reason)


def channel(topic: str) -> str:
    """Tipo de topic: stream, calibration, status, clock, ack o cmd."""
    parts = topic.split("/")
    return parts[-2] if parts[-2] in ("stream", "calibration") else parts[-1]


def expected_topic(kind: str, payload: dict[str, Any]) -> str:
    """El topic que le corresponde a un payload según sus propios ids (``topics`` valida los ids)."""
    a, d = payload["astronaut_id"], payload["device_id"]
    match kind:
        case "stream":
            return topics.stream(a, d, payload["stream_id"])
        case "calibration":
            return topics.calibration(a, d, payload["stream_id"])
        case "status":
            return topics.status(a, d)
        case "clock":
            return topics.clock(a, d)
        case "ack":
            return topics.ack(a, d)
        case _:
            return topics.cmd(a, d)


@dataclass(frozen=True)
class Seen:
    topic: str
    payload: dict[str, Any]
    qos: int
    retain: bool

    @property
    def kind(self) -> str:
        return channel(self.topic)


class Bench:
    """Emulador + gateway + observador de un dispositivo ``itest-e2e-*``; ``async with`` lo arranca y al salir lo limpia."""

    def __init__(self, settings: GatewaySettings, **emulator_options: Any) -> None:
        self.device_id = "itest-e2e"
        self.emulator_options = {"seed": 7, **emulator_options}
        self.settings = settings.model_copy(
            update={
                "astronaut_id": ASTRONAUT,
                "device_id": self.device_id,
                "mqtt_host": HOST,
                "mqtt_port": PORT,
                "mqtt_client_id": "itest-e2e",
                "mqtt_username": self.device_id if DEVICE_PASSWORD else None,
                "mqtt_password": SecretStr(DEVICE_PASSWORD) if DEVICE_PASSWORD else None,
                "serial_reconnect_initial_s": 0.2,
            }
        )
        self.astronaut_id = self.settings.astronaut_id
        self.root = f"{topics.ROOT}/crew/{self.astronaut_id}/device/{self.device_id}"
        self.received: list[Seen] = []
        self.problems: list[str] = []
        self.boot_id = emulator.initial_boot_id(self.emulator_options["seed"])
        self._changed = asyncio.Event()
        self._stack = contextlib.AsyncExitStack()
        self._observer: aiomqtt.Client | None = None
        self._consumer: asyncio.Task[None] | None = None
        self.gateway: asyncio.Task[None] | None = None
        self._emulator: asyncio.Task[None] | None = None

    # --- ciclo de vida ---

    async def __aenter__(self) -> Bench:
        self._observer = await self._stack.enter_async_context(aiomqtt.Client(HOST, PORT, identifier=f"itest-observer-{uuid.uuid4().hex[:8]}", **OBSERVER_AUTH))
        await self._observer.subscribe(f"{self.root}/#", qos=1)
        self._consumer = asyncio.create_task(self._consume(self._observer))
        ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._emulator = asyncio.create_task(emulator.serve("127.0.0.1", 0, on_listening=ready.set_result, **self.emulator_options))
        port = await asyncio.wait_for(ready, TIMEOUT_S)
        self.settings = self.settings.model_copy(update={"serial_url": f"socket://127.0.0.1:{port}"})
        self.gateway = asyncio.create_task(app.main(self.settings))
        return self

    async def __aexit__(self, *exc: object) -> None:
        try:
            await self.stop_gateway()
            if self._emulator is not None:
                self._emulator.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait_for(self._emulator, TIMEOUT_S)
        finally:
            await self._clear_retained()
            if self._consumer is not None:
                self._consumer.cancel()
                await asyncio.gather(self._consumer, return_exceptions=True)
            await self._stack.aclose()

    async def stop_gateway(self) -> None:
        """Cancela el gateway (como SIGTERM) y espera a que acabe: ``serve`` publica ``offline`` retained antes de salir."""
        task = self.gateway
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, TIMEOUT_S)
        if task.done() and not task.cancelled() and task.exception() is not None:
            raise AssertionError("the gateway died on its own") from task.exception()

    async def _clear_retained(self) -> None:
        """Deja el broker como estaba: vacía los retained (status, clock, calibration) que dejó este dispositivo."""
        client = self._observer
        assert client is not None
        for topic in sorted({s.topic for s in self.received if s.kind in RETAINED}):
            await client.publish(topic, payload=b"", qos=1, retain=True)

    # --- observador ---

    async def _consume(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            payload = message.payload if isinstance(message.payload, bytes | bytearray) else b""
            seen = Seen(str(message.topic), json.loads(payload) if payload else {}, message.qos, message.retain)
            self.problems += self._check(seen)
            self.received.append(seen)
            self._changed.set()

    def _check(self, seen: Seen) -> list[str]:
        """Contrato de un mensaje recibido: schema completo, ids del payload = ids del topic, QoS 1 y retain solo donde toca."""
        if not seen.payload:
            return []  # el vaciado de retained de la limpieza
        problems = [f"{seen.topic}: {e}" for e in validation.errors(SCHEMAS[seen.kind], seen.payload)]
        if seen.topic != expected_topic(seen.kind, seen.payload):
            problems.append(f"{seen.topic}: ids of the payload do not match the topic")
        if seen.qos != 1:
            problems.append(f"{seen.topic}: received with QoS {seen.qos}")
        if seen.kind not in RETAINED and seen.retain:
            problems.append(f"{seen.topic}: delivered with the retain flag but it must not be retained")
        return problems

    def assert_contract(self) -> None:
        assert self.problems == []
        assert self.received, "nothing was received"

    async def until(self, predicate: Callable[[], bool], what: str, timeout: float = TIMEOUT_S) -> None:
        async def wait() -> None:
            while True:
                self._changed.clear()
                if predicate():
                    return
                await self._changed.wait()

        try:
            await asyncio.wait_for(wait(), timeout)
        except TimeoutError:
            summary = {kind: sum(1 for s in self.received if s.kind == kind) for kind in SCHEMAS}
            raise AssertionError(f"timed out after {timeout} s waiting for {what}; received {summary}") from None

    async def retained_snapshot(self, expected: set[str]) -> dict[str, Seen]:
        """Qué entrega el broker retained a un suscriptor tardío (el flag retain solo vale en la primera entrega).

        Un mensaje marcador, publicado tras suscribirse (fuera de ``root``: el observador no lo ve), llega después de todos los
        retained que el broker entrega al suscribir: al verlo ya se sabe cuáles son, sin esperar tiempo.
        """
        late: dict[str, Seen] = {}
        marker = f"{topics.ROOT}/itest-marker/{uuid.uuid4().hex}"
        async with aiomqtt.Client(HOST, PORT, identifier=f"itest-late-{uuid.uuid4().hex[:8]}", **OBSERVER_AUTH) as client:
            await client.subscribe(marker, qos=1)
            await client.subscribe(f"{self.root}/#", qos=1)
            await client.publish(marker, b"x", qos=1)

            async def collect() -> None:
                async for message in client.messages:
                    if str(message.topic) == marker:
                        return
                    payload = message.payload if isinstance(message.payload, bytes | bytearray) else b""
                    if message.retain:
                        late[str(message.topic)] = Seen(str(message.topic), json.loads(payload) if payload else {}, message.qos, True)

            await asyncio.wait_for(collect(), TIMEOUT_S)
        assert expected <= set(late), f"the broker did not deliver as retained: {sorted(expected - set(late))}"
        assert {s.kind for s in late.values()} <= RETAINED  # ningún stream, ack ni cmd se queda retained
        assert all(s.qos == 1 for s in late.values())
        return late

    # --- vistas de lo recibido ---

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [s.payload for s in self.received if s.kind == kind and s.payload]

    def chunks(self, stream_id: str | None = None, session_id: str | None = None) -> list[dict[str, Any]]:
        return [p for p in self.of("stream") if (stream_id is None or p["stream_id"] == stream_id) and (session_id is None or p["session_id"] == session_id)]

    def statuses(self) -> list[dict[str, Any]]:
        return self.of("status")

    def acks(self, command_id: str) -> list[str]:
        return [p["status"] for p in self.of("ack") if p["command_id"] == command_id]

    def index_of(self, predicate: Callable[[Seen], bool]) -> int:
        return next(i for i, s in enumerate(self.received) if predicate(s))

    # --- comandos (como el backend) ---

    async def send(self, model: type[Any], **fields: Any) -> str:
        """Publica un comando por ``topics.cmd`` (JSON compacto, QoS 1, sin retain) y devuelve su ``command_id``."""
        command_id = str(uuid.uuid4())
        command = model(
            schema_version=SCHEMA_VERSION,
            command_id=command_id,
            astronaut_id=self.astronaut_id,
            device_id=self.device_id,
            issued_at=datetime.now(UTC),
            **fields,
        )
        payload = json.dumps(dump(command), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert self._observer is not None
        await self._observer.publish(topics.cmd(self.astronaut_id, self.device_id), payload, qos=1, retain=False)
        return command_id

    async def start_session(self, session_id: str = SESSION) -> str:
        command_id = await self.send(
            StartSessionCommand, type="start_session", session_id=session_id, session_type="exercise", protocol_id="lat_pulldown_standing_v1"
        )
        await self.until(lambda: "accepted" in self.acks(command_id), "the start_session ack")
        return command_id

    async def stop_session(self, session_id: str = SESSION) -> str:
        return await self.send(StopSessionCommand, type="stop_session", session_id=session_id, reason="completed")


def run(scenario: Callable[[], Any]) -> None:
    _require_broker()
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None  # paho necesita add_reader
    asyncio.run(scenario(), loop_factory=loop_factory)


def consecutive(chunks: list[dict[str, Any]]) -> bool:
    seqs = [c["seq"] for c in chunks]
    return seqs == list(range(seqs[0], seqs[0] + len(seqs)))


# --- 1. arranque ---


def test_startup_publishes_status_clock_and_background_chunks(settings: GatewaySettings) -> None:
    async def scenario() -> None:
        async with Bench(settings) as bench:
            await bench.until(lambda: any(s["status"] == "ok" and "firmware_version" in s for s in bench.statuses()), "the retained status ok")
            await bench.until(lambda: bool(bench.of("clock")), "the retained clock")
            await bench.until(lambda: len(bench.chunks(IMU)) >= 2 and len(bench.chunks(STRETCH)) >= 2, "two background chunks of the IMU and the cord")
            ok = next(s for s in bench.statuses() if s["status"] == "ok" and "firmware_version" in s)
            assert ok["boot_id"] == bench.boot_id and ok["source"] == "device" and ok["link"] == {"type": "usb_serial"}
            assert ok["firmware_version"] == emulator.FIRMWARE_VERSION
            clock = bench.of("clock")[0]
            assert (clock["method"], clock["boot_id"], clock["device_ms"] % 1000) == ("gateway_receive_time", bench.boot_id, 0)
            for stream_id, n_samples in ((IMU, 10), (STRETCH, 10)):
                chunks = bench.chunks(stream_id)
                assert consecutive(chunks) and chunks[0]["seq"] == 0
                for chunk in chunks:
                    day = datetime.fromisoformat(chunk["received_at"])
                    assert chunk["session_id"] == status.background_session_id(bench.settings, day)  # el session_id de fondo
                    assert (chunk["sample_rate_hz"], chunk["n_samples"], chunk["source"], chunk["boot_id"]) == (10.0, n_samples, "device", bench.boot_id)
            assert bench.chunks(EMG) == []  # sin sesión no hay EMG
            retained = await bench.retained_snapshot({topics.status(bench.astronaut_id, bench.device_id), topics.clock(bench.astronaut_id, bench.device_id)})
            assert retained[topics.status(bench.astronaut_id, bench.device_id)].payload["status"] == "ok"
            bench.assert_contract()

    run(scenario)


# --- 2. sesión ---


def test_session_lifecycle_streams_chunks_and_acks_in_order(settings: GatewaySettings) -> None:
    async def scenario() -> None:
        async with Bench(settings) as bench:
            await bench.until(lambda: any(s["status"] == "ok" for s in bench.statuses()) and bool(bench.of("clock")), "the device online")
            clocks_before = len(bench.of("clock"))
            start_id = await bench.start_session()
            await bench.until(lambda: all(len(bench.chunks(s, SESSION)) >= 8 for s in (EMG, IMU, STRETCH)), "8 session chunks per stream")
            await bench.until(lambda: len(bench.of("clock")) > clocks_before, "a new clock anchor after the start")
            for stream_id, rate, n in ((EMG, 1000.0, 100), (IMU, 100.0, 10), (STRETCH, 20.0, 2)):
                chunks = bench.chunks(stream_id, SESSION)
                assert consecutive(chunks)
                assert {(c["sample_rate_hz"], c["n_samples"], c["source"], c["boot_id"]) for c in chunks} == {(rate, n, "device", bench.boot_id)}
            mark_id = await bench.send(MarkEventCommand, type="mark_event", session_id=SESSION, event_type="rep_start", rep_index=1)
            await bench.until(lambda: bench.acks(mark_id) == ["accepted", "completed"], "the mark_event acks")
            mark = bench.index_of(lambda s: s.kind == "ack" and s.payload["command_id"] == mark_id and s.payload["status"] == "completed")
            stop_id = await bench.stop_session()
            await bench.until(lambda: "completed" in bench.acks(stop_id), "the stop_session acks")
            tail = [(p["command_id"], p["status"]) for s in bench.received[mark + 1 :] if s.kind == "ack" and (p := s.payload)]
            assert tail == [(stop_id, "accepted"), (start_id, "completed"), (stop_id, "completed")]
            assert bench.acks(start_id) == ["accepted", "completed"]
            late_clock = bench.of("clock")[-1]
            assert (late_clock["boot_id"], late_clock["method"]) == (bench.boot_id, "gateway_receive_time")
            await bench.retained_snapshot({topics.status(bench.astronaut_id, bench.device_id), topics.clock(bench.astronaut_id, bench.device_id)})
            bench.assert_contract()

    run(scenario)


# --- 3. calibraciones ---


def test_calibrations_are_retained_before_their_completed_ack_and_stamp_the_cord_chunks(settings: GatewaySettings) -> None:
    async def scenario() -> None:
        async with Bench(settings) as bench:
            await bench.until(lambda: any(s["status"] == "ok" for s in bench.statuses()), "the device online")
            await bench.start_session()
            await bench.until(lambda: len(bench.chunks(STRETCH, SESSION)) >= 2, "cord chunks of the session")
            before = len(bench.chunks(STRETCH))
            assert all("calibration_id" not in c for c in bench.chunks(STRETCH))

            results: dict[str, str] = {}
            for calibration_type, stream_id in (("stretch", STRETCH), ("emg_mvc", EMG)):
                command_id = await bench.send(
                    CalibrateCommand, type="calibrate", session_id=SESSION, calibration_type=calibration_type, stream_ids=[stream_id], duration_s=1
                )
                await bench.until(lambda c=command_id: bool({"completed", "failed"} & set(bench.acks(c))), f"the {calibration_type} calibration to end")
                assert bench.acks(command_id) == ["accepted", "completed"]
                completed = next(s for s in bench.received if s.kind == "ack" and s.payload["command_id"] == command_id and s.payload["status"] == "completed")
                calibration_id = completed.payload["result"]["calibration_id"]
                results[stream_id] = calibration_id
                topic = topics.calibration(bench.astronaut_id, bench.device_id, stream_id)
                retained = bench.index_of(lambda s, t=topic, c=calibration_id: s.topic == t and s.payload["calibration_id"] == c)
                assert retained < bench.received.index(completed)  # la calibración sale antes que el ack que la cita
                assert bench.received[retained].payload["calibration_type"] == calibration_type

            await bench.until(lambda: any("calibration_id" in c for c in bench.chunks(STRETCH)), "a cord chunk with the calibration")
            stamped = [c for c in bench.chunks(STRETCH)[before:] if "calibration_id" in c]
            assert stamped and all(c["calibration_id"] == results[STRETCH] and "length_mm" in c["data"] for c in stamped)
            retained_topics = {topics.calibration(bench.astronaut_id, bench.device_id, s) for s in (STRETCH, EMG)}
            late = await bench.retained_snapshot(retained_topics | {topics.status(bench.astronaut_id, bench.device_id)})
            assert {late[t].payload["calibration_id"] for t in retained_topics} == set(results.values())
            bench.assert_contract()

    run(scenario)


# --- 4. fallos ---


def gap_index(chunks: list[dict[str, Any]]) -> int | None:
    """Primer chunk (no el primero de la lista) que declara muestras perdidas."""
    return next((i for i, c in enumerate(chunks) if i > 0 and "dropped_samples" in c), None)


def test_dropped_batches_show_up_as_a_gap_with_dropped_samples(settings: GatewaySettings) -> None:
    async def scenario() -> None:
        async with Bench(settings, drop_every=5) as bench:
            await bench.until(lambda: any(s["status"] == "ok" for s in bench.statuses()), "the device online")
            await bench.start_session()
            await bench.until(lambda: all(gap_index(bench.chunks(s, SESSION)) is not None for s in (EMG, IMU, STRETCH)), "a chunk after a gap per stream")
            for stream_id, lost in ((EMG, 100), (IMU, 10), (STRETCH, 2)):
                chunks = bench.chunks(stream_id, SESSION)
                i = gap_index(chunks)
                assert i is not None
                assert chunks[i]["dropped_samples"] == lost  # un lote perdido
                assert chunks[i]["seq"] == chunks[i - 1]["seq"] + 2  # su seq salta el hueco
                assert "packet_gap" in {flag for q in chunks[i]["quality"].values() for flag in q["flags"]}
            bench.assert_contract()

    run(scenario)


def test_a_device_reboot_changes_boot_id_restarts_seq_and_anchors_a_new_clock(settings: GatewaySettings) -> None:
    async def scenario() -> None:
        async with Bench(settings, reboot_after_s=3.0) as bench:
            second = emulator.next_boot_id(bench.boot_id)
            await bench.until(lambda: any(c["boot_id"] == second for c in bench.of("clock")), "the clock anchor of the new boot", timeout=40)
            await bench.until(lambda: all(any(c["boot_id"] == second for c in bench.chunks(s)) for s in (IMU, STRETCH)), "a chunk of the new boot per stream")
            assert {c["boot_id"] for c in bench.of("clock")} <= {bench.boot_id, second}
            for stream_id in (IMU, STRETCH):
                after = [c for c in bench.chunks(stream_id) if c["boot_id"] == second]
                assert after[0]["seq"] == 0 and consecutive(after)
                assert after[0]["t0_device_ms"] == 0  # el reloj del dispositivo vuelve a contar desde cero
            assert any(s["status"] == "ok" and s["boot_id"] == second for s in bench.statuses())
            retained = await bench.retained_snapshot({topics.clock(bench.astronaut_id, bench.device_id)})
            assert retained[topics.clock(bench.astronaut_id, bench.device_id)].payload["boot_id"] == second
            bench.assert_contract()

    run(scenario)


def test_a_device_without_imu_is_degraded_with_the_imu_stream_stalled(settings: GatewaySettings) -> None:
    async def scenario() -> None:
        async with Bench(settings, imu_present=False) as bench:

            def imu_stalled() -> bool:
                return any(
                    s["status"] == "degraded" and any(st["stream_id"] == IMU and st["state"] == "stalled" for st in s["streams"]) for s in bench.statuses()
                )

            await bench.until(imu_stalled, "the status degraded with the IMU stalled")
            await bench.until(lambda: len(bench.chunks(STRETCH)) >= 1, "the cord keeps flowing")
            assert bench.chunks(IMU) == []
            retained = await bench.retained_snapshot({topics.status(bench.astronaut_id, bench.device_id)})
            assert retained[topics.status(bench.astronaut_id, bench.device_id)].payload["status"] == "degraded"
            bench.assert_contract()

    run(scenario)


# --- 5. cierre ---


def test_stopping_the_gateway_leaves_a_retained_offline_status(settings: GatewaySettings) -> None:
    async def scenario() -> None:
        async with Bench(settings) as bench:
            await bench.until(lambda: any(s["status"] == "ok" for s in bench.statuses()), "the device online")
            await bench.stop_gateway()
            await bench.until(lambda: bench.statuses()[-1]["status"] == "offline", "the offline status")
            assert any(s["status"] == "ok" for s in bench.statuses()[:-1])
            retained = await bench.retained_snapshot({topics.status(bench.astronaut_id, bench.device_id)})
            assert retained[topics.status(bench.astronaut_id, bench.device_id)].payload["status"] == "offline"
            bench.assert_contract()

    run(scenario)
