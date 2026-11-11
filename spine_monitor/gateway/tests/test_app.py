"""Bucle del gateway real (``app.Gateway``): conexión, enlace serie, chunks, reloj, estado, reconexión y parada.

Sin broker ni puerto: cliente MQTT, fuente de líneas, reloj y ``sleep`` de mentira (``conftest.Harness``). Todo lo que se
publica se valida en el arnés contra su schema, topic, QoS y retain.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from spine_contracts import topics, validation
from spine_gateway.assembler import DiscardReason
from spine_gateway.protocol import BadLineReason, FrameKind

AST, DEV = "AST-01", "wb-01"
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def go(coro) -> None:
    asyncio.run(coro)


# --- conexión, Last Will y anuncio ---


def test_connects_with_its_last_will_subscribes_and_announces_offline_until_the_device_speaks(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            (attempt,) = h.connections.attempts
            assert (attempt["hostname"], attempt["port"], attempt["identifier"]) == ("broker.test", 1884, "spine-gateway-wb-01")
            will = attempt["will"]
            assert (will.topic, will.qos, will.retain) == (topics.status(AST, DEV), 1, True)
            payload = json.loads(will.payload)
            assert payload["status"] == "offline" and payload["source"] == "device"
            assert validation.errors("telemetry/device-status", payload) == []
            assert h.client.subscriptions == [(topics.cmd(AST, DEV), 1)]
            # Sin enlace serie todavía: estado offline (retained) y ningún ancla de reloj inventada.
            assert h.client.statuses() == ["offline"]
            assert h.client.on("clock") == []

    go(scenario())


def test_the_first_hello_and_tick_bring_the_device_online_with_its_clock_anchor(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.link_up()
            assert h.client.statuses() == ["offline"]  # puerto abierto, pero el dispositivo aún no ha hablado
            await h.hello(firmware="fw-2.1.0")
            status = h.client.payloads("status")[-1]
            assert status["status"] == "ok" and status["boot_id"] == 7 and status["firmware_version"] == "fw-2.1.0"
            assert status["link"] == {"type": "usb_serial"} and "battery_pct" not in status and "temperature_c" not in status
            assert h.client.on("clock") == []  # el hello no es ancla
            h.device_ms = 1000
            await h.time.advance(0.25)
            await h.tick()
            (clock,) = h.client.payloads("clock")
            assert clock == {
                "schema_version": "1.0.0", "astronaut_id": AST, "device_id": DEV, "boot_id": 7, "device_ms": 1000,
                "utc": "2026-11-02T09:00:00.250000Z", "uncertainty_ms": 20.0, "method": "gateway_receive_time",
            }  # fmt: skip

    go(scenario())


def test_shutdown_publishes_a_retained_offline_status(harness) -> None:
    async def scenario() -> None:
        h = harness()
        async with h:
            await h.boot()
            await h.run_device(1.0)
            assert h.client.statuses()[-1] == "ok"
        last = h.client.on("status")[-1]
        assert last.retain and last.payload["status"] == "offline"
        # Nada después del offline.
        assert h.client.published[-1] is last

    go(scenario())


def test_frames_become_chunks_on_their_stream_topics(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.run_device(1.0)
            chunks = h.client.payloads("stream")
            # Sin sesión: fondo de IMU y cordón (1 chunk de 1 s a 10 Hz cada 10 lotes); el EMG no se publica.
            assert sorted(c["stream_id"] for c in chunks) == ["imu_l3", "stretch_lumbar"]
            assert all(c["sample_rate_hz"] == 10.0 and c["seq"] == 0 and c["boot_id"] == 7 for c in chunks)
            assert all(not p.retain for p in h.client.on("stream"))

    go(scenario())


def test_background_session_id_comes_from_the_utc_day(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            # 23:59:57.5 UTC: los chunks de fondo se cierran a las 23:59:58.5, 23:59:59.5 y 00:00:00.5.
            h.time.now += timedelta(hours=14, minutes=59, seconds=57.5)
            await h.boot()
            await h.run_device(3.0)
            ids = [c["session_id"] for c in h.client.payloads("stream") if c["stream_id"] == "imu_l3"]
            assert len(ids) == 3 and ids[0] == ids[1] != ids[2]

    go(scenario())


# --- entrada serie ---


def test_bad_lines_are_counted_by_reason_and_never_stop_the_loop(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            good = h.frame_line(FrameKind.IMU)
            corrupted = good.data[:-2] + (b"00" if not good.data.endswith(b"00") else b"11")
            await h.feed(h.line(b"garbage"), h.line(corrupted), h.line(b"\xff\xfe"), h.line(b"garbage again"))
            assert h.gateway.bad_lines == {BadLineReason.NO_CRC: 2, BadLineReason.BAD_CRC: 1, BadLineReason.NOT_ASCII: 1}
            await h.run_device(1.0)
            assert [c["stream_id"] for c in h.client.payloads("stream")] == ["imu_l3", "stretch_lumbar"]

    go(scenario())


def test_frames_wait_for_the_device_to_speak_after_the_port_opens_and_a_new_boot_restarts_seq(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.link_up()
            await h.frames(*FrameKind)  # antes de ningún hello: nadie sabe de qué arranque son
            await h.boot()
            await h.run_device(2.0)
            first = [c for c in h.client.payloads("stream") if c["stream_id"] == "imu_l3"]
            assert [(c["boot_id"], c["seq"]) for c in first] == [(7, 0), (7, 1)]
            await h.link_down()
            await h.link_up()
            await h.run_device(1.0)  # el arranque anterior ya no vale hasta que el dispositivo hable
            assert len(h.client.payloads("stream")) == 4
            await h.hello(boot_id=8)
            await h.run_device(1.0)
            last = h.client.payloads("stream")[-1]
            assert (last["boot_id"], last["seq"]) == (8, 0)
            # Ancla del arranque nuevo con su primer tick.
            assert [c["boot_id"] for c in h.client.payloads("clock")] == [7, 8]
            assert h.gateway.discarded[DiscardReason.NO_BOOT] == 3 + 30

    go(scenario())


def test_a_tick_of_an_unknown_boot_is_a_new_boot_and_its_own_anchor(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            h.reboot(9)  # el gateway arrancó a mitad: no ve el hello, solo ticks
            h.device_ms = 5000
            await h.tick()
            assert h.client.payloads("clock")[-1]["boot_id"] == 9 and h.client.payloads("clock")[-1]["device_ms"] == 5000
            status = h.client.payloads("status")[-1]
            assert status["boot_id"] == 9 and status["uptime_ms"] == 5000 and "firmware_version" not in status

    go(scenario())


def test_status_last_seq_is_the_last_published_seq_of_the_current_boot(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.run_device(65.0)
            await h.hello(boot_id=8)
            await h.run_device(31.0)
            seen: dict[tuple[int, str], int] = {}
            checked = 0
            for p in h.client.published:
                if p.channel == "stream":
                    seen[(p.payload["boot_id"], p.payload["stream_id"])] = p.payload["seq"]
                elif p.channel == "status" and p.payload["status"] != "offline":
                    for s in p.payload["streams"]:
                        assert s.get("last_seq") == seen.get((p.payload["boot_id"], s["stream_id"])), (p.payload["timestamp"], s)
                        checked += "last_seq" in s
            assert checked >= 6

    go(scenario())


# --- estado del dispositivo ---


def states(status: dict) -> dict[str, str]:
    return {s["stream_id"]: s["state"] for s in status.get("streams", [])}


def test_a_missing_imu_degrades_the_device_until_its_frames_come_back(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            booted = h.client.payloads("status")[-1]
            # Recién arrancado nadie ha mandado lotes aún: margen de stall_timeout_s (disabled), estado ok.
            assert booted["status"] == "ok" and set(states(booted).values()) == {"disabled"}
            await h.run_device(1.0, kinds=(FrameKind.EMG, FrameKind.CORD))
            assert h.client.statuses()[-1] == "ok"
            await h.run_device(1.1, kinds=(FrameKind.EMG, FrameKind.CORD))  # pasan los 2 s sin un solo lote I
            degraded = h.client.on("status")[-1].payload
            assert degraded["status"] == "degraded"
            assert states(degraded) == {"emg_lat_left": "disabled", "imu_l3": "stalled", "stretch_lumbar": "active"}
            assert degraded["timestamp"] == "2026-11-02T09:00:02Z"  # en cuanto vence el plazo, no en el periódico
            await h.run_device(0.1)
            back = h.client.payloads("status")[-1]
            assert back["status"] == "ok" and states(back)["imu_l3"] == "active"

    go(scenario())


def test_a_stream_that_stops_is_stalled_after_the_timeout(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.run_device(3.0)  # pasado el margen de arranque
            published = len(h.client.payloads("status"))
            await h.run_device(1.9, kinds=(FrameKind.EMG, FrameKind.IMU))
            assert len(h.client.payloads("status")) == published  # aún dentro del plazo
            await h.run_device(0.2, kinds=(FrameKind.EMG, FrameKind.IMU))
            last = h.client.payloads("status")[-1]
            assert last["status"] == "degraded" and states(last)["stretch_lumbar"] == "stalled" and states(last)["imu_l3"] == "active"

    go(scenario())


def test_a_silent_device_is_noticed_by_the_timer(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.run_device(3.0)  # último lote a las 09:00:03
            await h.time.advance(5.0)  # ni lotes ni ticks: solo el temporizador puede verlo
            last = h.client.payloads("status")[-1]
            assert last["status"] == "degraded" and last["timestamp"] == "2026-11-02T09:00:05Z"
            assert states(last) == {"emg_lat_left": "disabled", "imu_l3": "stalled", "stretch_lumbar": "stalled"}

    go(scenario())


def test_link_down_is_published_offline_at_once_and_the_next_hello_brings_it_back(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.run_device(1.0)
            await h.link_down()
            offline = h.client.on("status")[-1]
            assert offline.retain and offline.payload["status"] == "offline" and "streams" not in offline.payload
            published = len(h.client.published)
            await h.link_up()
            await h.time.advance(5.0)  # sin hello: sigue offline y no hay nada nuevo que contar
            assert len(h.client.published) == published
            await h.hello(boot_id=8)
            assert h.client.statuses()[-1] == "ok"

    go(scenario())


def test_status_and_clock_follow_their_intervals(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.run_device(125.0)
            statuses = h.client.payloads("status")
            # Anuncio (offline), hello (ok, streams aún sin datos), fin del margen de arranque (active) y cada 30 s.
            assert h.client.statuses() == ["offline", "ok", "ok", "ok", "ok", "ok", "ok"]
            assert [s["timestamp"] for s in statuses] == [
                "2026-11-02T09:00:00Z", "2026-11-02T09:00:00Z", "2026-11-02T09:00:02Z",
                "2026-11-02T09:00:30Z", "2026-11-02T09:01:00Z", "2026-11-02T09:01:30Z", "2026-11-02T09:02:00Z",
            ]  # fmt: skip
            assert states(statuses[1]) == dict.fromkeys(["emg_lat_left", "imu_l3", "stretch_lumbar"], "disabled")
            assert states(statuses[2]) == {"emg_lat_left": "disabled", "imu_l3": "active", "stretch_lumbar": "active"}
            assert all(s["sample_rate_hz"] == 10.0 for s in statuses[2]["streams"] if s["state"] == "active")
            # El periódico ancla con el último tick conocido (a los 60 s aún no ha llegado el de 60 000 ms).
            clocks = h.client.payloads("clock")
            assert [(c["device_ms"], c["utc"]) for c in clocks] == [
                (0, "2026-11-02T09:00:00Z"), (59_000, "2026-11-02T09:00:59Z"), (119_000, "2026-11-02T09:01:59Z"),
            ]  # fmt: skip

    go(scenario())


def test_bad_lines_are_logged_without_flooding(harness, caplog) -> None:
    async def scenario() -> None:
        async with harness() as h:
            with caplog.at_level(logging.DEBUG, logger="spine_gateway.app"):
                await h.feed(*(h.line(f"noise {n}") for n in range(1, 6)))
            logged = [(r.levelno, r.getMessage()) for r in caplog.records if "bad serial line" in r.getMessage()]
            assert logged == [
                (logging.WARNING, "bad serial line (no_crc, 1 so far): noise 1"),
                (logging.WARNING, "bad serial line (no_crc, 2 so far): noise 2"),
                (logging.DEBUG, "bad serial line (no_crc): noise 3"),
                (logging.WARNING, "bad serial line (no_crc, 4 so far): noise 4"),
                (logging.DEBUG, "bad serial line (no_crc): noise 5"),
            ]

    go(scenario())
