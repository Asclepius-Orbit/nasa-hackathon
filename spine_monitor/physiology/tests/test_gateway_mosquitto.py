"""Integración del gateway falso con un broker MQTT real en 127.0.0.1:1883, con usuarios y ACL (#96).

Se salta solo si no hay nada escuchando ahí; con SPINE_REQUIRE_BROKER=1 (el gate) falla en vez de saltar. El broker es
el de `app/docker-compose.yml` (antes `sh app/mosquitto/setup.sh`). Credenciales por entorno, como en el servicio
`quality`: SPINE_TEST_MQTT_USERNAME (por defecto `spine-itest`, con los permisos del backend) y SPINE_TEST_MQTT_PASSWORD
para el observador, y SPINE_TEST_MQTT_DEVICE_PASSWORD para los dispositivos `itest-01` (AST-01) e `itest-02` (AST-02),
cada uno usuario con su ACL. Sin contraseñas, el test del gateway entra anónimo (broker sin auth) y los de autenticación
se saltan (o fallan con SPINE_REQUIRE_BROKER=1). Usa dispositivos propios y borra al final los retained que deja.
"""

import asyncio
import json
import os
import socket
import sys
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiomqtt
import pytest
import yaml
from pydantic import SecretStr
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


pytestmark = pytest.mark.mosquitto


def _skip_or_fail(reason: str) -> None:
    """Salta, salvo con SPINE_REQUIRE_BROKER=1 (el gate de Docker), donde falla."""
    if os.environ.get("SPINE_REQUIRE_BROKER") == "1":
        pytest.fail(f"SPINE_REQUIRE_BROKER=1 but {reason}: the mosquitto test must not be skipped in the gate")
    pytest.skip(reason)


def _require_broker() -> None:
    if not _broker_up():
        _skip_or_fail(f"no MQTT broker on {HOST}:{PORT}")


def _require_auth() -> None:
    _require_broker()
    if not (os.environ.get("SPINE_TEST_MQTT_PASSWORD") and os.environ.get("SPINE_TEST_MQTT_DEVICE_PASSWORD")):
        _skip_or_fail("SPINE_TEST_MQTT_PASSWORD / SPINE_TEST_MQTT_DEVICE_PASSWORD are not set")


def _observer_auth() -> dict[str, str | None]:
    """Usuario con los permisos del backend (lee los canales de todo dispositivo, publica comandos)."""
    password = os.environ.get("SPINE_TEST_MQTT_PASSWORD") or None
    return {"username": os.environ.get("SPINE_TEST_MQTT_USERNAME", "spine-itest") if password else None, "password": password}


def _device_auth(device_id: str) -> dict[str, str | None]:
    password = os.environ.get("SPINE_TEST_MQTT_DEVICE_PASSWORD") or None
    return {"username": device_id if password else None, "password": password}


def _client(name: str, *, username: str | None = None, password: str | None = None) -> aiomqtt.Client:
    return aiomqtt.Client(HOST, PORT, identifier=f"itest-{name}-{uuid.uuid4().hex[:8]}", username=username, password=password)


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None  # paho necesita add_reader
    return asyncio.run(coro, loop_factory=loop_factory)


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
    device_password = os.environ.get("SPINE_TEST_MQTT_DEVICE_PASSWORD") or None
    settings = GatewaySettings(
        config=EXAMPLE, speed=0.0, start_day=4, mqtt_host=HOST, mqtt_port=PORT, mqtt_client_id=f"itest-{uuid.uuid4().hex[:8]}",
        mqtt_username="{device_id}" if device_password else None, mqtt_password=SecretStr(device_password) if device_password else None,
    )  # fmt: skip
    gateway = asyncio.create_task(_serve(MissionGenerator(config), settings, client_factory=aiomqtt.Client, clock=lambda: datetime.now(UTC)))
    received: list[tuple[str, dict, bool]] = []

    async with _client("observer", **_observer_auth()) as observer:
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
            await observer.publish(
                topics.cmd("AST-01", device_id), qos=1, payload=_command("stop_session", 2, device_id, session_id=SESSION, reason="completed")
            )
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

    # Limpia los retained que deja el gateway (estado, anclaje y calibraciones de los dispositivos de prueba); solo
    # cada dispositivo puede escribir en sus topics.
    for member in config.crew:
        a, d = member.astronaut_id, member.device_id
        async with _client("cleanup", **_device_auth(d)) as device:
            for topic in (topics.status(a, d), topics.clock(a, d), *(topics.calibration(a, d, s.stream_id) for s in config.device.streams)):
                await device.publish(topic, payload=b"", qos=1, retain=True)
    assert clock_topic in {t for t, _, _ in received}


def test_gateway_against_a_real_broker():
    _require_broker()
    _run(_scenario())


# --- autenticación y ACL ------------------------------------------------------------------------------------------


async def _connect(*, username: str | None = None, password: str | None = None) -> None:
    async with _client("auth", username=username, password=password) as client:
        await client.subscribe(topics.SUBSCRIBE_ALL_STATUS, qos=1)


def test_an_anonymous_connection_is_refused():
    _require_auth()
    with pytest.raises(aiomqtt.MqttError):
        _run(_connect())


def test_a_wrong_password_is_refused():
    _require_auth()
    with pytest.raises(aiomqtt.MqttError):
        _run(_connect(username=_observer_auth()["username"], password="not-the-password"))


def test_the_test_credentials_are_accepted():
    _require_auth()
    _run(_connect(**_observer_auth()))
    _run(_connect(**_device_auth("itest-01")))


async def _until_topic(client: aiomqtt.Client, last: str) -> list[str]:
    """Topics que llegan hasta `last`. Un mismo publicador entrega en orden: lo publicado antes y no recibido, se denegó."""
    received: list[str] = []

    async def wait() -> None:
        async for message in client.messages:
            received.append(str(message.topic))
            if str(message.topic) == last:
                return

    await asyncio.wait_for(wait(), timeout=TIMEOUT_S)
    return received


async def _publish_scenario() -> list[str]:
    own = topics.ack("AST-01", "itest-01")
    foreign = [topics.ack("AST-02", "itest-02"), topics.ack("AST-02", "itest-01")]  # otro dispositivo; otro astronauta
    async with _client("observer", **_observer_auth()) as observer:
        for topic in (own, *foreign):
            await observer.subscribe(topic, qos=1)
        async with _client("device", **_device_auth("itest-01")) as device:
            for topic in (*foreign, own):  # lo denegado primero: el broker lo descarta en silencio
                await device.publish(topic, payload=b"{}", qos=1)
        return await _until_topic(observer, own)


def test_a_device_cannot_publish_on_the_topics_of_another_device_or_astronaut():
    _require_auth()
    assert _run(_publish_scenario()) == [topics.ack("AST-01", "itest-01")]


async def _command_scenario() -> list[str]:
    own = topics.cmd("AST-01", "itest-01")
    foreign = topics.cmd("AST-02", "itest-02")
    async with _client("device", **_device_auth("itest-01")) as device:
        await device.subscribe(foreign, qos=1)  # denegada: no le llega nada
        await device.subscribe(own, qos=1)
        async with _client("observer", **_observer_auth()) as observer:
            await observer.publish(foreign, payload=b"{}", qos=1)
            await observer.publish(own, payload=b"{}", qos=1)
        return await _until_topic(device, own)


def test_a_device_cannot_read_the_commands_of_another_device():
    _require_auth()
    assert _run(_command_scenario()) == [topics.cmd("AST-01", "itest-01")]
