"""Comandos inválidos, reentregas y errores inesperados: el gateway real responde lo que puede y nunca se cae."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

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


def start(n: int, **fields: Any) -> dict[str, Any]:
    return command("start_session", n, session_id=SESSION, session_type="exercise", protocol_id="lat_pulldown_standing_v1", **fields)


def stop(n: int) -> dict[str, Any]:
    return command("stop_session", n, session_id=SESSION, reason="completed")


def statuses_of(h, n: int) -> list[str]:
    return [a["status"] for a in h.client.acks(cid(n))]


def test_unreadable_commands_are_ignored_and_invalid_ones_rejected(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            before = len(h.client.published)
            await h.send(b"\xff not json")
            await h.send({"type": "start_session"})
            await h.send(b'{"padding": "' + b"x" * 20_000 + b'"}')
            assert len(h.client.published) == before
            missing = start(1)
            del missing["protocol_id"]
            await h.send(missing)
            await h.send(start(2, astronaut_id="AST-02"))
            await h.send(start(3, padding="x" * 20_000))
            for n in (1, 2, 3):
                (ack,) = h.client.acks(cid(n))
                assert ack["status"] == "rejected" and ack["error"]["code"] == "invalid_command" and ack["astronaut_id"] == AST
            await h.send(start(4))  # y sigue funcionando
            assert statuses_of(h, 4) == ["accepted"]

    go(scenario())


def test_messages_on_another_topic_are_ignored(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.client.inbox.put(_message(topics.cmd("AST-02", "wb-02"), start(1)))
            assert h.client.acks(cid(1)) == []

    go(scenario())


@dataclass
class _Message:
    topic: str
    payload: bytes


def _message(topic: str, payload: dict[str, Any]) -> _Message:
    return _Message(topic, json.dumps(payload).encode())


def test_redelivered_commands_get_the_same_acks_without_running_again(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.send(start(1))
            await h.send(start(1))  # DUP de QoS 1
            assert statuses_of(h, 1) == ["accepted", "accepted"]
            assert h.client.acks(cid(1))[0] == h.client.acks(cid(1))[1]
            await h.run_device(1.0)
            await h.send(stop(2))
            await h.send(stop(2))
            assert statuses_of(h, 2) == ["accepted", "completed"] * 2
            # El completed del start, anotado en su entrada, también se reenvía.
            await h.send(start(1))
            assert statuses_of(h, 1) == ["accepted", "accepted", "completed", "accepted", "completed"]

    go(scenario())


def test_the_handled_commands_memory_is_bounded_and_keeps_the_most_recent(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            for n in range(1, 260):
                await h.send(stop(n))  # no_session: cada uno anota su ack (a las 09:00:00)
            await h.time.advance(1.0)

            def stamps(n: int) -> list[str]:
                # Un ack reenviado es idéntico al original (09:00:00); uno nuevo lleva la hora de su ejecución.
                return [a["timestamp"][-3:-1] for a in h.client.acks(cid(n))]

            await h.send(stop(5))  # se reenvía (sigue en la memoria: 4..259) y pasa a ser el más reciente
            await h.send(stop(260))  # expulsa al 4
            await h.send(stop(3))  # olvidado: se ejecuta otra vez y expulsa al 6
            await h.send(stop(5))  # sigue en la memoria
            await h.send(stop(4))
            await h.send(stop(6))
            await h.send(stop(9))  # 4 y 6 han expulsado al 7 y al 8; el 9 sigue
            assert {n: stamps(n) for n in (3, 4, 5, 6, 9)} == {
                3: ["00", "01"], 4: ["00", "01"], 5: ["00", "00", "00"], 6: ["00", "01"], 9: ["00", "00"],
            }  # fmt: skip

    go(scenario())


def test_the_handled_commands_memory_holds_exactly_256(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            for n in range(1, 258):
                await h.send(stop(n))
            await h.time.advance(1.0)
            await h.send(stop(2))  # memoria: 2..257, sigue
            await h.send(stop(1))  # el 257.º más antiguo: olvidado
            assert [a["timestamp"] for a in h.client.acks(cid(2))] == ["2026-11-02T09:00:00Z"] * 2
            assert [a["timestamp"] for a in h.client.acks(cid(1))] == ["2026-11-02T09:00:00Z", "2026-11-02T09:00:01Z"]

    go(scenario())


def test_an_unexpected_error_is_acked_failed_internal_and_nothing_starts(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            failures = [RuntimeError("boom")]
            h.client.fault = lambda topic: failures.pop() if topic.endswith("/ack") and failures else None
            await h.send(start(1))
            assert h.client.acks(cid(1)) == [
                {
                    "schema_version": "1.0.0", "command_id": cid(1), "astronaut_id": AST, "device_id": DEV, "timestamp": "2026-11-02T09:00:00Z",
                    "status": "failed", "error": {"code": "internal", "message": "internal gateway error"},
                }
            ]  # fmt: skip
            await h.run_device(1.0)
            assert all(c["session_id"] != SESSION for c in h.client.payloads("stream"))
            await h.send(stop(2))  # sigue atendiendo comandos
            assert h.client.acks(cid(2))[0]["error"]["code"] == "no_session"

    go(scenario())


def test_a_command_whose_acks_never_left_runs_again_when_redelivered(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            h.client.fault = lambda topic: RuntimeError("broker gone") if topic.endswith("/ack") else None
            await h.send(start(1))
            assert h.client.acks(cid(1)) == []
            h.client.fault = None
            await h.send(start(1))  # el cliente reintenta con el mismo command_id
            assert statuses_of(h, 1) == ["accepted"]
            await h.run_device(0.1)
            assert any(c["session_id"] == SESSION for c in h.client.payloads("stream"))

    go(scenario())


def test_an_error_while_handling_a_serial_line_or_a_timer_never_stops_the_gateway(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            failures = {"/stream/": 1, "/status": 1}

            def fault(topic: str) -> BaseException | None:
                for part, left in failures.items():
                    if part in topic and left:
                        failures[part] -= 1
                        return RuntimeError(f"boom on {part}")
                return None

            h.client.fault = fault
            await h.run_device(45.0, kinds=(FrameKind.IMU, FrameKind.CORD))
            assert failures == {"/stream/": 0, "/status": 0}
            assert len(h.client.payloads("stream")) >= 80 and h.client.statuses()[-1] == "ok"

    go(scenario())


def test_an_error_inside_a_timer_task_does_not_stop_the_gateway(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            calibrate = command("calibrate", 1, session_id=SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"], duration_s=1)
            await h.send(calibrate)
            h.client.fault = lambda topic: RuntimeError("boom") if topic.endswith("/ack") else None
            await h.run_device(6.5, kinds=(FrameKind.IMU,))  # vence el plazo y su ack failed revienta
            h.client.fault = None
            await h.send(start(2))
            assert statuses_of(h, 2) == ["accepted"]
            await h.send(calibrate | {"command_id": cid(3)})  # la calibración anterior ya no ocupa el dispositivo
            assert statuses_of(h, 3) == ["accepted"]

    go(scenario())


def test_payloads_that_are_not_bytes_are_read_as_text(harness, fakes) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.client.inbox.put(fakes.Message(topics.cmd(AST, DEV), json.dumps(start(1))))
            await h.client.inbox.put(fakes.Message(topics.cmd(AST, DEV), None))
            await h.client.inbox.put(fakes.Message(topics.cmd(AST, DEV), bytearray(json.dumps(stop(2)).encode())))
            assert statuses_of(h, 1) == ["accepted", "completed"] and statuses_of(h, 2) == ["accepted", "completed"]

    go(scenario())


def test_an_accepted_ack_that_cannot_leave_changes_nothing(harness) -> None:
    async def scenario() -> None:
        import aiomqtt

        async def lost_ack(payload: dict[str, Any]) -> None:
            """El broker se cae justo al publicar el ``accepted``: la conexión se rehace con un cliente nuevo."""
            gone = h.client
            gone.fault = lambda topic: aiomqtt.MqttError("Disconnected") if topic.endswith("/ack") else None
            gone.inbox.queue.put_nowait(_message(topics.cmd(AST, DEV), payload))
            await h.run_device(1.5)
            assert h.client is not gone and h.client.acks(payload["command_id"]) == []

        other = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a02"
        calibrate = command("calibrate", 3, session_id=SESSION, calibration_type="stretch", stream_ids=["stretch_lumbar"], duration_s=60)
        async with harness() as h:
            await h.boot()
            await lost_ack(start(1))
            await lost_ack(calibrate)
            await h.send(start(2) | {"command_id": cid(2)})
            assert statuses_of(h, 2) == ["accepted"]  # el start perdido no dejó sesión
            await lost_ack(stop(4))
            await h.run_device(0.2)
            assert h.client.payloads("stream")[-1]["session_id"] == SESSION  # el stop perdido no la paró
            # Las reentregas se ejecutan de verdad (ningún ack llegó a salir).
            await h.send(calibrate)
            await h.send(stop(4))
            await h.send(start(5) | {"session_id": other})
            assert statuses_of(h, 3) == ["accepted"] and statuses_of(h, 4) == ["accepted", "completed"] and statuses_of(h, 5) == ["accepted"]

    go(scenario())
