"""Integración del gateway real con un broker MQTT real en 127.0.0.1:1883 (aiomqtt de verdad, Arduino de mentira).

Se salta si no hay broker, salvo con SPINE_REQUIRE_BROKER=1 (el gate de Docker), donde falla. Usa un dispositivo propio
(``itest-gw-*``) y borra al final los retained que deja.
"""

import asyncio
import json
import os
import socket
import sys
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

import aiomqtt
import pytest
from pydantic import SecretStr
from spine_contracts import topics, validation
from spine_gateway import app
from spine_gateway.assembler import SAMPLES_PER_BATCH
from spine_gateway.ports import LinkUp, SerialLine
from spine_gateway.protocol import Frame, FrameKind, Hello, Tick, encode_frame, encode_hello, encode_tick
from spine_gateway.settings import GatewaySettings

pytestmark = pytest.mark.mosquitto

HOST, PORT = "127.0.0.1", 1883
TIMEOUT_S = 20.0
OBSERVER_AUTH = {"username": "spine-gwtest", "password": os.environ["SPINE_TEST_MQTT_PASSWORD"]} if os.getenv("SPINE_TEST_MQTT_PASSWORD") else {}
DEVICE_PASSWORD = os.getenv("SPINE_TEST_MQTT_DEVICE_PASSWORD")
FIRMWARE_PERIOD_S = 0.1
SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"
SAMPLE: dict[FrameKind, Any] = {FrameKind.EMG: 300, FrameKind.IMU: (0, 0, 8192, 0, 0, 0), FrameKind.CORD: 512}


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


def _schema(topic: str) -> str:
    if "/stream/" in topic:
        return "telemetry/stream-chunk"
    if "/calibration/" in topic:
        return "domain/calibration"
    return {"status": "telemetry/device-status", "clock": "telemetry/clock-sync", "ack": "telemetry/command-ack"}[topic.rsplit("/", 1)[-1]]


def _line(text: str) -> SerialLine:
    return SerialLine(text.encode("ascii"), datetime.now(UTC))


async def _scenario(settings: GatewaySettings, source: Any) -> None:
    a, d = settings.astronaut_id, settings.device_id
    status_topic, clock_topic, ack_topic, cmd_topic = topics.status(a, d), topics.clock(a, d), topics.ack(a, d), topics.cmd(a, d)
    gateway = asyncio.create_task(app.serve(settings, source=source, client_factory=aiomqtt.Client, clock=app.utc_now))
    received: list[tuple[str, dict[str, Any], bool]] = []

    def command(kind: str, n: int, **fields: Any) -> bytes:
        body = {
            "schema_version": "1.0.0", "command_id": f"c0ffee00-0000-4000-8000-{n:012d}", "astronaut_id": a, "device_id": d,
            "issued_at": datetime.now(UTC).isoformat(), "type": kind, **fields,
        }  # fmt: skip
        return json.dumps(body).encode()

    async with aiomqtt.Client(HOST, PORT, identifier=f"itest-observer-{uuid.uuid4().hex[:8]}", **OBSERVER_AUTH) as observer:
        root = f"{topics.ROOT}/crew/{a}/device/{d}"
        for topic in (status_topic, clock_topic, ack_topic, f"{root}/stream/+", f"{root}/calibration/+"):
            await observer.subscribe(topic, qos=1)

        async def until(predicate) -> None:
            async def wait() -> None:
                if predicate():
                    return
                async for message in observer.messages:
                    payload = message.payload if isinstance(message.payload, bytes | bytearray) else b""
                    received.append((str(message.topic), json.loads(payload) if payload else {}, message.retain))
                    if predicate():
                        return

            await asyncio.wait_for(wait(), timeout=TIMEOUT_S)

        def acks(status: str) -> list[dict[str, Any]]:
            return [p for t, p, _ in received if t == ack_topic and p.get("status") == status]

        def statuses() -> list[str]:
            return [p.get("status", "") for t, p, _ in received if t == status_topic]

        def session_chunks() -> list[dict[str, Any]]:
            return [p for t, p, _ in received if "/stream/" in t and p.get("session_id") == SESSION]

        try:
            await until(lambda: "offline" in statuses())  # anuncio: aún sin dispositivo
            await source.feed.put(LinkUp(datetime.now(UTC)), _line(encode_hello(Hello(1, 41, "fw-itest", 0))), _line(encode_tick(Tick(41, 0))))
            await until(lambda: "ok" in statuses() and any(t == clock_topic for t, _, _ in received))
            start = command("start_session", 1, session_id=SESSION, session_type="exercise", protocol_id="lat_pulldown_v1")
            await observer.publish(cmd_topic, qos=1, payload=start)
            await until(lambda: len(acks("accepted")) >= 1)
            for seq in range(3):
                frames = [Frame(kind, seq, 100 * seq, tuple([SAMPLE[kind]] * SAMPLES_PER_BATCH[kind])) for kind in FrameKind]
                await source.feed.put(*(_line(encode_frame(f)) for f in frames))
            await until(lambda: len(session_chunks()) >= 9)
            await observer.publish(cmd_topic, qos=1, payload=command("stop_session", 2, session_id=SESSION, reason="completed"))
            await until(lambda: len(acks("completed")) >= 2)
        finally:
            gateway.cancel()
            with pytest.raises(asyncio.CancelledError):
                await gateway
        await until(lambda: statuses()[-1:] == ["offline"])

        assert {p["stream_id"] for p in session_chunks()} == {settings.emg_stream_id, settings.imu_stream_id, settings.stretch_stream_id}
        assert all(p["source"] == "device" and p["boot_id"] == 41 for p in session_chunks())
        for topic, payload, _ in received:
            if payload:  # un retained vacío de una ejecución anterior que no limpió
                assert validation.errors(_schema(topic), payload) == [], topic

        # Limpia los retained que deja el gateway.
        for topic in (status_topic, clock_topic):
            await observer.publish(topic, payload=b"", qos=1, retain=True)


def test_gateway_against_a_real_broker(settings: GatewaySettings, fakes) -> None:
    _require_broker()
    device_id = "itest-gw"
    custom = settings.model_copy(
        update={
            "device_id": device_id,
            "mqtt_host": HOST,
            "mqtt_port": PORT,
            "mqtt_client_id": "itest",
            "mqtt_username": device_id if DEVICE_PASSWORD else None,
            "mqtt_password": SecretStr(DEVICE_PASSWORD) if DEVICE_PASSWORD else None,
        }
    )
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None  # paho necesita add_reader
    asyncio.run(_scenario(custom, fakes.FakeLineSource()), loop_factory=loop_factory)


class FakeArduino(threading.Thread):
    """Arduino de mentira tras un ``socket://``: socket bloqueante en un hilo, sin asyncio (nada de medios cierres).

    Como el firmware, repite sus líneas periódicamente (el real manda un tick por segundo; aquí cada ``FIRMWARE_PERIOD_S``):
    lo que llega antes de que pyserial acabe de abrir el puerto se pierde, porque ``socket://`` vacía el buffer de entrada
    al abrir, igual que un Uno, que se reinicia al abrirse el puerto. El ritmo lo marca el timeout del ``recv``, que es
    también donde se ve el EOF del gateway al cerrar su puerto serie (``port_closed``).
    """

    def __init__(self, lines: bytes) -> None:
        super().__init__(daemon=True)
        self.lines = lines
        self.server = socket.create_server(("127.0.0.1", 0))
        self.server.settimeout(TIMEOUT_S)
        self.port = self.server.getsockname()[1]
        self.port_closed = threading.Event()
        self.stop = threading.Event()

    def run(self) -> None:
        with self.server, self.server.accept()[0] as conn:
            conn.settimeout(FIRMWARE_PERIOD_S)
            while not self.stop.is_set():
                try:
                    conn.sendall(self.lines)
                    if conn.recv(64) == b"":
                        break
                except TimeoutError:
                    continue
                except OSError:
                    break
            self.port_closed.set()


async def _main_scenario(settings: GatewaySettings) -> None:
    """``app.main`` tal cual: el puerto serie es ``socket://`` hacia un ``FakeArduino``."""
    arduino = FakeArduino(f"{encode_hello(Hello(1, 77, 'fw-e2e', 0))}\r\n{encode_tick(Tick(77, 1000))}\n".encode("ascii"))
    arduino.start()
    custom = settings.model_copy(update={"serial_url": f"socket://127.0.0.1:{arduino.port}"})
    a, d = custom.astronaut_id, custom.device_id
    status_topic, clock_topic = topics.status(a, d), topics.clock(a, d)
    received: list[tuple[str, dict[str, Any]]] = []
    async with aiomqtt.Client(HOST, PORT, identifier=f"itest-observer-{uuid.uuid4().hex[:8]}", **OBSERVER_AUTH) as observer:
        await observer.subscribe(status_topic, qos=1)
        await observer.subscribe(clock_topic, qos=1)

        async def until(predicate) -> None:
            async def wait() -> None:
                async for message in observer.messages:
                    payload = message.payload if isinstance(message.payload, bytes | bytearray) else b""
                    received.append((str(message.topic), json.loads(payload) if payload else {}))
                    if predicate():
                        return

            if not predicate():
                await asyncio.wait_for(wait(), timeout=TIMEOUT_S)

        gateway = asyncio.create_task(app.main(custom))
        try:
            await until(lambda: any(t == status_topic and p.get("status") == "ok" for t, p in received))
            await until(lambda: any(t == clock_topic and p.get("boot_id") == 77 for t, p in received))
        finally:
            gateway.cancel()
            with pytest.raises(asyncio.CancelledError):
                await gateway
        await until(lambda: [p.get("status") for t, p in received if t == status_topic][-1:] == ["offline"])
        assert await asyncio.to_thread(arduino.port_closed.wait, TIMEOUT_S)  # el gateway cierra su puerto al pararse
        clocks = [p for t, p in received if t == clock_topic and p]
        assert clocks and all((c["boot_id"], c["device_ms"], c["method"]) == (77, 1000, "gateway_receive_time") for c in clocks)
        ok = next(p for t, p in received if t == status_topic and p.get("status") == "ok")
        assert ok["boot_id"] == 77 and ok["source"] == "device" and ok["link"] == {"type": "usb_serial"}
        for topic, payload in received:
            if payload:
                assert validation.errors(_schema(topic), payload) == [], topic
        for topic in (status_topic, clock_topic):
            await observer.publish(topic, payload=b"", qos=1, retain=True)
    arduino.stop.set()
    arduino.join(TIMEOUT_S)


def test_main_reads_the_arduino_from_the_serial_url_and_publishes_to_the_broker(settings: GatewaySettings) -> None:
    _require_broker()
    device_id = "itest-gw"
    custom = settings.model_copy(
        update={
            "device_id": device_id,
            "mqtt_host": HOST,
            "mqtt_port": PORT,
            "mqtt_client_id": "itest",
            "mqtt_username": device_id if DEVICE_PASSWORD else None,
            "mqtt_password": SecretStr(DEVICE_PASSWORD) if DEVICE_PASSWORD else None,
        }
    )
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(_main_scenario(custom), loop_factory=loop_factory)
