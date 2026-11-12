"""Conexión MQTT del gateway real: reconexión con backoff, re-anuncio, calibraciones tras reconectar y parada."""

import asyncio
from typing import Any

import pytest
from spine_contracts import topics
from spine_gateway.protocol import FrameKind

AST, DEV = "AST-01", "wb-01"
SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"


def go(coro) -> None:
    asyncio.run(coro)


def cid(n: int) -> str:
    return f"c0ffee00-0000-4000-8000-{n:012d}"


def command(kind: str, n: int, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0", "command_id": cid(n), "astronaut_id": AST, "device_id": DEV,
        "issued_at": "2026-11-02T09:00:00Z", "type": kind, **fields,
    }  # fmt: skip


def start(n: int) -> dict[str, Any]:
    return command("start_session", n, session_id=SESSION, session_type="spine_check", protocol_id="spine_length_rest_v1")


def calibrate_stretch(n: int) -> dict[str, Any]:
    return command("calibrate", n, session_id=SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"], duration_s=1)


def test_a_dropped_connection_reconnects_reannounces_and_the_session_goes_on(harness, fakes) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.send(calibrate_stretch(1))
            await h.run_device(1.5)
            await h.send(start(2))
            await h.run_device(0.5)
            first = h.client
            calibration_id = first.payloads("calibration")[0]["calibration_id"]
            await h.disconnect()
            await h.run_device(0.9)  # sin conexión: los chunks de este rato se pierden (nadie los recibe)
            assert len(h.connections.attempts) == 1
            await h.run_device(0.2)  # pasa el backoff inicial (1 s) y reconecta
            second = h.client
            assert second is not first and len(h.connections.attempts) == 2
            assert second.subscriptions == [(topics.cmd(AST, DEV), 1)]
            # Ancla y estado antes que nada (el Last Will dejó offline), y la calibración antes del primer chunk que la cita.
            assert [p.channel for p in second.published[:2]] == ["clock", "status"]
            assert second.payloads("status")[0]["status"] == "ok"
            channels = [p.channel for p in second.published]
            citing = next(i for i, p in enumerate(second.published) if p.channel == "stream" and "calibration_id" in p.payload)
            assert channels.index("calibration") == citing - 1
            assert second.payloads("calibration")[0]["calibration_id"] == calibration_id
            cord = [c for c in second.payloads("stream") if c["stream_kind"] == "stretch"]
            assert cord and all(c["calibration_id"] == calibration_id and c["session_id"] == SESSION for c in cord)
            # Se republica una sola vez por conexión.
            await h.run_device(1.0)
            assert len(second.on("calibration")) == 1
            # last_seq solo cuenta lo publicado: el hueco de la desconexión no aparece como publicado.
            published = max(c["seq"] for c in first.payloads("stream") + second.payloads("stream") if c["stream_id"] == "imu_l3")
            status = second.payloads("status")[0]
            assert {s["stream_id"]: s.get("last_seq") for s in status["streams"]}["imu_l3"] < published

    go(scenario())


def test_reconnect_backoff_grows_caps_and_resets_after_connecting(harness, fakes) -> None:
    async def scenario() -> None:
        clients: dict[int, Any] = {}

        def plan(n: int) -> Any:
            if n <= 5 or n == 7:
                return None  # el broker rechaza la conexión
            clients[n] = fakes.FakeClient()
            return clients[n]

        async with harness(fakes.Connections(plan), mqtt_reconnect_initial_s=1.5, mqtt_reconnect_factor=2.0, mqtt_reconnect_max_s=5.0) as h:
            await h.time.advance(20.0)
            assert len(h.connections.attempts) == 6
            await h.disconnect()  # a los 20 s; tras conectar, la espera vuelve a la inicial
            await h.time.advance(10.0)
            offsets = [(a["at"] - h.connections.attempts[0]["at"]).total_seconds() for a in h.connections.attempts]
            assert offsets == [0.0, 1.5, 4.5, 9.5, 14.5, 19.5, 21.5, 24.5]
            assert all(a["will"].retain and a["will"].topic == topics.status(AST, DEV) for a in h.connections.attempts)
            assert h.client is clients[8] and h.client.statuses() == ["offline"]

    go(scenario())


def test_a_failed_publish_drops_the_connection_and_reconnects(harness, fakes) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.run_device(3.0)
            first = h.client
            first.fail_publish = True
            await h.run_device(1.0)  # el chunk de fondo de este segundo (seq 3) falla al publicarse
            assert len(h.connections.attempts) == 1
            await h.run_device(1.0)  # backoff de 1 s y conexión nueva
            assert len(h.connections.attempts) == 2 and h.client is not first
            imu = [c["seq"] for c in first.payloads("stream") + h.client.payloads("stream") if c["stream_id"] == "imu_l3"]
            assert imu == [0, 1, 2, 4]  # el 3 se perdió
            status = h.client.payloads("status")[0]
            assert {s["stream_id"]: s.get("last_seq") for s in status["streams"]}["imu_l3"] == 2

    go(scenario())


def test_cancelling_while_waiting_to_reconnect_stops_cleanly(harness, fakes) -> None:
    async def scenario() -> None:
        h = harness(fakes.Connections(lambda n: None))
        async with h:
            await h.time.advance(3.0)
            assert len(h.connections.attempts) == 3  # a los 0, 1 y 3 s
        assert h.source.closed and h.time.pending() == 0

    go(scenario())


def test_stopping_closes_the_line_source_and_leaves_no_task_behind(harness, fakes) -> None:
    async def scenario() -> None:
        h = harness()
        async with h:
            await h.boot()
            await h.send(start(1))
            await h.send(calibrate_stretch(2))
            await h.run_device(0.5)
        assert h.source.closed and h.time.pending() == 0
        assert h.client.statuses()[-1] == "offline"

    go(scenario())


def test_stopping_in_the_middle_of_an_event_still_closes_the_line_source(harness, fakes) -> None:
    async def scenario() -> None:
        class SlowBroker(fakes.FakeClient):
            async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
                if "/stream/" in topic:
                    await asyncio.Event().wait()  # el broker no confirma nunca: el consumidor queda a medio evento
                await super().publish(topic, payload, qos, retain)

        h = harness(fakes.Connections(lambda n: SlowBroker()))
        async with h:
            await h.boot()
            for _ in range(10):  # el décimo cierra la ventana de fondo y su publish no vuelve
                h.source.feed.queue.put_nowait(h.frame_line(FrameKind.IMU))
            await h.time.advance(0.0)
            assert h.source.feed.queue.empty() and not h.source.closed
        assert h.source.closed  # cerrado al parar, no cuando el recolector se acuerde

    go(scenario())


@pytest.mark.parametrize("error", [RuntimeError("broker gone"), OSError("socket closed")])
def test_shutdown_survives_a_broker_that_is_already_gone(harness, fakes, error: Exception) -> None:
    async def scenario() -> None:
        h = harness()
        async with h:
            await h.boot()
            h.client.fault = lambda topic: error if topic.endswith("/status") else None
        assert h.client.statuses()[-1] == "ok"  # el offline no salió, pero la parada fue limpia

    go(scenario())


def test_frames_while_disconnected_are_not_published_nor_counted(harness, fakes) -> None:
    async def scenario() -> None:
        clients = [fakes.FakeClient(), None, fakes.FakeClient()]
        async with harness(fakes.Connections(lambda n: clients[n - 1])) as h:
            await h.boot()
            await h.run_device(1.0, kinds=(FrameKind.IMU,))
            await h.disconnect()
            await h.run_device(2.5, kinds=(FrameKind.IMU,))
            assert len(h.connections.attempts) == 2  # rechazada a los 2 s; la siguiente, a los 4 s
            await h.run_device(1.0, kinds=(FrameKind.IMU,))
            assert h.client is clients[2]
            assert [c["seq"] for c in clients[0].payloads("stream")] == [0]
            assert [c["seq"] for c in h.client.payloads("stream")] == [3]  # los de 2 s y 3 s no salieron

    go(scenario())


def test_a_message_stream_that_ends_is_a_lost_connection(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            first = h.client
            first.inbox.queue.put_nowait("end")  # sin esperar: nadie vuelve a pedir mensajes de esta conexión
            await h.time.advance(1.0)
            assert h.client is not first and len(h.connections.attempts) == 2

    go(scenario())


def test_a_source_without_aclose_is_consumed_too(harness) -> None:
    async def scenario() -> None:
        from spine_gateway.ports import LinkUp

        class Plain:
            def __init__(self) -> None:
                self.events_left = [LinkUp(h.time.now)]

            def events(self) -> Plain:
                return self

            def __aiter__(self) -> Plain:
                return self

            async def __anext__(self) -> LinkUp:
                if self.events_left:
                    return self.events_left.pop()
                await asyncio.Event().wait()
                raise StopAsyncIteration

        h = harness()
        h.source = Plain()
        async with h:
            pass
        assert h.client.statuses()[-1] == "offline"

    go(scenario())
