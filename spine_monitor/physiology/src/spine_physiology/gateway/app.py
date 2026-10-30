"""Bucle async del gateway falso.

Dueño: D2. Debe implementar, para cada astronauta del config:
- Suscripción a `topics.cmd(astronaut_id, device_id)`; cada comando se valida con
  `spine_contracts.command_adapter` y se responde en `topics.ack(...)` (CommandAck, QoS 1):
  start_session -> `accepted` y empieza a publicar chunks con ese session_id (`rejected`/`busy` si ya hay
  una sesión); stop_session -> `accepted` y para (`rejected`/`no_session` si no hay o no coincide);
  mark_event -> `accepted` (solo con sesión activa); calibrate -> `accepted` y luego `completed` con
  `result.calibration_id`; comando inválido -> `rejected`/`invalid_command` si se puede leer su
  command_id (si no, se ignora).
- DeviceStatus retained en `topics.status(...)` al arrancar y cada `device.status_interval_s`; Last Will
  retained con `status: offline`. ClockSync en `topics.clock(...)` al arrancar, al empezar sesión y cada
  `device.clock_sync_interval_s`.
- Fondo (si `schedule.background.enabled`): chunks de fondo con su session_id, pausados durante una
  sesión (mismo stream_id, el seq sigue).
- Día de misión = `start_day` + (reloj de pared − arranque) × `speed` / 1 día, acotado a la misión.
Todo con un reloj inyectable y el cliente MQTT como Protocol, para testear sin broker.
"""

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Protocol

from spine_physiology.gateway.settings import GatewaySettings
from spine_physiology.generator import MissionGenerator
from spine_physiology.sinks.mqtt import MqttPublisher


class IncomingMessage(Protocol):
    """Lo que usa el gateway de `aiomqtt.Message`."""

    @property
    def topic(self) -> object: ...  # aiomqtt.Topic; str(topic) da el topic

    @property
    def payload(self) -> bytes | bytearray | str | int | float | None: ...


class MqttClient(MqttPublisher, Protocol):
    """Lo que usa el gateway de `aiomqtt.Client`."""

    async def subscribe(self, topic: str, qos: int) -> object: ...

    @property
    def messages(self) -> AsyncIterator[IncomingMessage]: ...


class FakeGateway:
    def __init__(self, generator: MissionGenerator, settings: GatewaySettings, *, clock: Callable[[], datetime]) -> None:
        """`clock` devuelve el instante UTC actual (aware)."""
        raise NotImplementedError

    def mission_day(self, now: datetime) -> int:
        raise NotImplementedError

    async def handle(self, client: MqttClient, topic: str, payload: bytes) -> None:
        """Procesa un comando recibido en un topic `.../cmd`."""
        raise NotImplementedError

    async def run(self, client: MqttClient) -> None:
        """Suscribe, publica estado inicial y atiende comandos y streaming hasta que se cancele."""
        raise NotImplementedError


async def serve(settings: GatewaySettings) -> None:
    """Carga el YAML, conecta con aiomqtt (Last Will offline) y ejecuta `FakeGateway.run`."""
    raise NotImplementedError
