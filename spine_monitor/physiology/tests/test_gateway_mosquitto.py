"""Integración del gateway falso con un broker MQTT real en 127.0.0.1:1883.

Se salta solo si no hay nada escuchando ahí (p. ej. `docker run -p 1883:1883 eclipse-mosquitto:2
mosquitto -c /mosquitto-no-auth.conf`). Usa dispositivos propios (`itest-*`) y borra al final los
mensajes retained que deja, para no ensuciar un entorno de desarrollo con el simulador corriendo.
"""

import asyncio
import json
import socket
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiomqtt
import pytest
import yaml
from spine_contracts import topics, validation
from spine_physiology import MissionGenerator
from spine_physiology.config import MissionConfig
from spine_physiology.gateway.app import _serve
from spine_physiology.gateway.settings import GatewaySettings

HOST, PORT = "127.0.0.1", 1883
EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"
TIMEOUT_S = 20.0


def _broker_up() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.mosquitto,
    pytest.mark.skipif(not _broker_up(), reason=f"no MQTT broker on {HOST}:{PORT}"),
]


def _config() -> MissionConfig:
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    for n, member in enumerate(raw["crew"], start=1):
        member["device_id"] = f"itest-{n:02d}"
    raw["faults"]["device"]["reboot"]["probability_per_hour"] = 0.0
    return MissionConfig.model_validate(raw)


def _command(kind: str, n: int, device_id: str, **fields: object) -> bytes:
    payload = {
        "schema_version": "1.0.0", "command_id": f"c0ffee00-0000-4000-8000-{n:012d}", "astronaut_id": "AST-01",
        "device_id": device_id, "issued_at": datetime.now(UTC).isoformat(), "type": kind, **fields,
    }  # fmt: skip
    return json.dumps(payload).encode()


def _schema(topic: str) -> str:
    if "/stream/" in topic:
        return "telemetry/stream-chunk"
    if "/calibration/" in topic:
        return "domain/calibration"
    return {"status": "telemetry/device-status", "clock": "telemetry/clock-sync", "ack": "telemetry/command-ack"}[topic.rsplit("/", 1)[-1]]


async def _scenario() -> None:
    config = _config()
    device_id = config.member("AST-01").device_id
    status_topic, clock_topic, ack_topic = topics.status("AST-01", device_id), topics.clock("AST-01", device_id), topics.ack("AST-01", device_id)
    settings = GatewaySettings(config=EXAMPLE, speed=0.0, start_day=4, mqtt_host=HOST, mqtt_port=PORT, mqtt_client_id=f"itest-{uuid.uuid4().hex[:8]}")
    gateway = asyncio.create_task(_serve(MissionGenerator(config), settings, client_factory=aiomqtt.Client, clock=lambda: datetime.now(UTC)))
    received: list[tuple[str, dict, bool]] = []

    async with aiomqtt.Client(HOST, PORT, identifier=f"itest-observer-{uuid.uuid4().hex[:8]}") as observer:
        device_root = f"{topics.ROOT}/crew/AST-01/device/{device_id}"
        for topic in (status_topic, clock_topic, ack_topic, f"{device_root}/stream/+", f"{device_root}/calibration/+"):
            await observer.subscribe(topic, qos=1)

        async def until(predicate) -> None:
            async def wait() -> None:
                if predicate():
                    return
                async for message in observer.messages:
                    received.append((str(message.topic), json.loads(message.payload) if message.payload else {}, message.retain))
                    if predicate():
                        return

            await asyncio.wait_for(wait(), timeout=TIMEOUT_S)

        def acks(status: str) -> list[dict]:
            return [p for t, p, _ in received if t == ack_topic and p.get("status") == status]

        def session_chunks() -> list[dict]:
            return [p for t, p, _ in received if "/stream/" in t and p.get("session_id") == SESSION]

        try:
            await until(lambda: any(t == status_topic and p.get("status") == "ok" for t, p, _ in received))
            await observer.publish(
                topics.cmd("AST-01", device_id), qos=1,
                payload=_command("start_session", 1, device_id, session_id=SESSION, session_type="exercise", protocol_id="lat_pulldown_standing_v1"),
            )  # fmt: skip
            await until(lambda: len(session_chunks()) >= 40)
            assert acks("accepted")
            await observer.publish(topics.cmd("AST-01", device_id), qos=1, payload=_command("stop_session", 2, device_id, session_id=SESSION, reason="completed"))
            await until(lambda: len(acks("completed")) >= 2)
            await observer.publish(topics.cmd("AST-01", device_id), qos=1, payload=b"not json at all")
        finally:
            gateway.cancel()
            with pytest.raises(asyncio.CancelledError):
                await gateway
        await until(lambda: any(t == status_topic and p.get("status") == "offline" for t, p, _ in received))

        streams = {p["stream_id"] for p in session_chunks()}
        assert streams == set(config.protocol("lat_pulldown_standing_v1").streams)
        assert all(p["source"] == "simulator" for p in session_chunks())
        for topic, payload, _ in received:
            if payload:  # un retained vacío de una ejecución anterior que no limpió
                assert validation.errors(_schema(topic), payload) == [], topic
        # Todo chunk que cita una calibración llega después de esa calibración (retained) en su topic.
        calibrations = {p["calibration_id"] for t, p, _ in received if "/calibration/" in t and p}
        cited = {p["calibration_id"] for t, p, _ in received if "/stream/" in t and "calibration_id" in p}
        assert cited and cited <= calibrations

        # Limpia los retained que deja el gateway (estado, anclaje y calibraciones de los dispositivos de prueba).
        for member in config.crew:
            a, d = member.astronaut_id, member.device_id
            for topic in (topics.status(a, d), topics.clock(a, d), *(topics.calibration(a, d, s.stream_id) for s in config.device.streams)):
                await observer.publish(topic, payload=b"", qos=1, retain=True)
    assert clock_topic in {t for t, _, _ in received}


def test_gateway_against_a_real_broker():
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None  # paho necesita add_reader
    asyncio.run(_scenario(), loop_factory=loop_factory)
