"""MQTT bus ingestion: subscription to `topics.SUBSCRIBE_ALL_*`, validation and storage.

Every message is validated with `spine_contracts.validation` (the full JSON Schema, including the
conditional rules Pydantic does not capture) and the ids of the payload must match those of the
topic: a message cannot write on behalf of another astronaut or device. Whatever fails is stored in
`rejected_messages` (bounded) and logged; ingestion never crashes because of a message.

`Bus` keeps the connection (reconnection with backoff, persistent session so QoS 1 messages are not
lost during an outage) and publishes the commands of `Control`.
"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

import aiomqtt
from jsonschema.exceptions import ValidationError
from spine_contracts import topics, validation

from monitor.control import Control
from monitor.db import Store

_log = logging.getLogger(__name__)

SUBSCRIPTIONS = (
    topics.SUBSCRIBE_ALL_STREAMS,
    topics.SUBSCRIBE_ALL_STATUS,
    topics.SUBSCRIBE_ALL_ACKS,
    topics.SUBSCRIBE_ALL_CLOCKS,
    topics.SUBSCRIBE_ALL_CALIBRATIONS,
)

_DEVICE = rf"{re.escape(topics.ROOT)}/crew/(?P<astronaut_id>{topics.ASTRONAUT_ID.pattern})/device/(?P<device_id>{topics.DEVICE_ID.pattern})"
_TOPIC = re.compile(rf"{_DEVICE}/(?:(?P<kind>stream|calibration)/(?P<stream_id>{topics.STREAM_ID.pattern})|(?P<leaf>status|ack|clock))")

SCHEMAS = {
    "stream": "telemetry/stream-chunk",
    "calibration": "domain/calibration",
    "status": "telemetry/device-status",
    "ack": "telemetry/command-ack",
    "clock": "telemetry/clock-sync",
}


class Rejected(Exception):
    pass


class Ingest:
    def __init__(self, store: Store, control: Control, *, store_unknown_session_chunks: bool = True) -> None:
        self._store = store
        self._control = control
        self._store_unknown = store_unknown_session_chunks
        self.counts: dict[str, int] = {"accepted": 0, "duplicate": 0, "rejected": 0, "dropped": 0}

    def handle(self, topic: str, payload: bytes) -> None:
        try:
            result = self._handle(topic, payload)
        except Rejected as exc:
            self.counts["rejected"] += 1
            _log.warning("rejected message on %s: %s", topic, exc)
            self._store.record_rejected(topic, str(exc), payload)
            return
        except Exception as exc:
            self.counts["rejected"] += 1
            _log.exception("error handling message on %s", topic)
            self._store.record_rejected(topic, f"internal: {exc!r}", payload)
            return
        self.counts[result] += 1

    def _handle(self, topic: str, payload: bytes) -> str:
        match = _TOPIC.fullmatch(topic)
        if match is None:
            raise Rejected("unknown topic")
        kind = match["kind"] or match["leaf"]
        try:
            message = json.loads(payload)
        except ValueError as exc:
            raise Rejected(f"invalid JSON: {exc}") from None
        try:
            validation.validate(SCHEMAS[kind], message)
        except ValidationError as exc:
            raise Rejected(str(exc.message)) from None
        expected = {"astronaut_id": match["astronaut_id"], "device_id": match["device_id"]}
        if match["stream_id"] is not None:
            expected["stream_id"] = match["stream_id"]
        mismatched = sorted(k for k, v in expected.items() if message.get(k) != v)
        if mismatched:
            raise Rejected(f"payload does not match topic: {', '.join(mismatched)}")
        return getattr(self, f"_on_{kind}")(message)

    def _on_stream(self, chunk: dict[str, Any]) -> str:
        session = self._store.session(chunk["session_id"])
        if session is None and not self._store_unknown:
            return "dropped"
        if session is not None and (session.payload["astronaut_id"], session.payload["device_id"]) != (chunk["astronaut_id"], chunk["device_id"]):
            raise Rejected("chunk of a session that belongs to another device")
        return "accepted" if self._store.add_chunk(chunk) else "duplicate"

    def _on_status(self, status: dict[str, Any]) -> str:
        return "accepted" if self._store.put_device_status(status) else "duplicate"

    def _on_clock(self, anchor: dict[str, Any]) -> str:
        self._store.add_clock_sync(anchor)
        return "accepted"

    def _on_calibration(self, calibration: dict[str, Any]) -> str:
        self._store.put_calibration(calibration)
        if (session_id := calibration.get("session_id")) is not None:
            self._control.link_calibration(session_id, calibration["stream_id"], calibration["calibration_id"])
        return "accepted"

    def _on_ack(self, ack: dict[str, Any]) -> str:
        self._control.on_ack(ack)
        return "accepted"


# --- MQTT connection --------------------------------------------------------------------------------


class IncomingMessage(Protocol):
    @property
    def topic(self) -> object: ...
    @property
    def payload(self) -> bytes | bytearray | str | int | float | None: ...


class MqttClient(Protocol):
    """What the app uses of `aiomqtt.Client`. Plain `def` + Awaitable: aiomqtt returns coroutines."""

    def subscribe(self, topic: str, qos: int) -> Awaitable[object]: ...

    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> Awaitable[object]: ...

    @property
    def messages(self) -> AsyncIterator[IncomingMessage]: ...


type ClientFactory = Callable[[], AbstractAsyncContextManager[MqttClient]]


def _as_bytes(payload: bytes | bytearray | str | int | float | None) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    return str(payload).encode()


class Bus:
    """Connection to the broker. `publish` fails (and the command becomes `send_failed`) when disconnected."""

    def __init__(
        self,
        client_factory: ClientFactory,
        *,
        reconnect_min_s: float = 1.0,
        reconnect_max_s: float = 30.0,
    ) -> None:
        self._factory = client_factory
        self._min = reconnect_min_s
        self._max = reconnect_max_s
        self._client: MqttClient | None = None
        self.connected = asyncio.Event()

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        if self._client is None:
            raise ConnectionError("not connected to the MQTT broker")
        body = json.dumps(payload, separators=(",", ":")).encode()
        await self._client.publish(topic, body, qos=1, retain=False)

    async def run(self, ingest: Ingest) -> None:
        delay = self._min
        while True:
            try:
                async with self._factory() as client:
                    for subscription in SUBSCRIPTIONS:
                        await client.subscribe(subscription, qos=1)
                    self._client = client
                    self.connected.set()
                    delay = self._min
                    _log.info("connected to the MQTT broker")
                    async for message in client.messages:
                        ingest.handle(str(message.topic), _as_bytes(message.payload))
            except aiomqtt.MqttError as exc:
                _log.warning("MQTT connection lost (%s); retrying in %.1f s", exc, delay)
            finally:
                self._client = None
                self.connected.clear()
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._max)
