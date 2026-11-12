"""Comandos de sesión del gateway real: start_session, stop_session, max_duration_s y mark_event."""

import asyncio
from typing import Any

AST, DEV = "AST-01", "wb-01"
SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"
OTHER = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a02"


def go(coro) -> None:
    asyncio.run(coro)


def command(kind: str, n: int, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0", "command_id": f"c0ffee00-0000-4000-8000-{n:012d}", "astronaut_id": AST,
        "device_id": DEV, "issued_at": "2026-11-02T09:00:00Z", "type": kind, **fields,
    }  # fmt: skip


def cid(n: int) -> str:
    return f"c0ffee00-0000-4000-8000-{n:012d}"


def start(n: int, session_id: str = SESSION, **fields: Any) -> dict[str, Any]:
    fields.setdefault("session_type", "exercise")
    fields.setdefault("protocol_id", "lat_pulldown_standing_v1")
    return command("start_session", n, session_id=session_id, **fields)


def stop(n: int, session_id: str = SESSION) -> dict[str, Any]:
    return command("stop_session", n, session_id=session_id, reason="completed")


def mark(n: int, session_id: str = SESSION) -> dict[str, Any]:
    return command("mark_event", n, session_id=session_id, event_type="rep_start", rep_index=1)


def statuses_of(h, n: int) -> list[str]:
    return [a["status"] for a in h.client.acks(cid(n))]


def session_chunks(h, session_id: str = SESSION) -> list[dict[str, Any]]:
    return [c for c in h.client.payloads("stream") if c["session_id"] == session_id]


def seqs(chunks: list[dict[str, Any]]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for c in chunks:
        out.setdefault(c["stream_id"], []).append(c["seq"])
    return out


def test_start_session_streams_every_frame_of_its_streams_with_its_session_id(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.run_device(3.0)
            await h.send(start(1))
            assert statuses_of(h, 1) == ["accepted"]
            await h.run_device(0.5)
            session = session_chunks(h)
            # Un chunk por lote y stream, a su frecuencia nativa: EMG incluido (sin stream_ids = los tres).
            assert {(c["stream_id"], c["sample_rate_hz"], c["n_samples"]) for c in session} == {
                ("emg_lat_left", 1000.0, 100), ("imu_l3", 100.0, 10), ("stretch_lumbar", 20.0, 2),
            }  # fmt: skip
            assert len(session) == 15
            status = h.client.payloads("status")[-1]
            assert {s["stream_id"]: (s["state"], s.get("sample_rate_hz")) for s in status["streams"]} == {
                "emg_lat_left": ("active", 1000.0), "imu_l3": ("active", 100.0), "stretch_lumbar": ("active", 20.0),
            }  # fmt: skip

    go(scenario())


def test_a_session_of_some_streams_leaves_the_rest_in_background(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.send(start(1, stream_ids=["imu_l3"]))
            await h.run_device(2.0)
            session = session_chunks(h)
            assert {c["stream_id"] for c in session} == {"imu_l3"} and len(session) == 20
            background = [c for c in h.client.payloads("stream") if c["session_id"] != SESSION]
            assert {c["stream_id"] for c in background} == {"stretch_lumbar"} and all(c["sample_rate_hz"] == 10.0 for c in background)
            status = h.client.payloads("status")[-1]
            assert {s["stream_id"]: s["state"] for s in status["streams"]}["emg_lat_left"] == "disabled"

    go(scenario())


def test_an_empty_stream_list_means_every_stream(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.send(start(1, stream_ids=[]))
            await h.run_device(0.1)
            assert {c["stream_id"] for c in session_chunks(h)} == {"emg_lat_left", "imu_l3", "stretch_lumbar"}

    go(scenario())


def test_stop_completes_the_start_then_the_stop_and_background_resumes_with_continuous_seq(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.run_device(2.5)  # dos chunks de fondo y media ventana que se tira al empezar
            await h.send(start(1))
            await h.run_device(1.0)
            await h.send(stop(2))
            acks = [(a["command_id"], a["status"]) for a in h.client.payloads("ack")]
            assert acks == [(cid(1), "accepted"), (cid(2), "accepted"), (cid(1), "completed"), (cid(2), "completed")]
            await h.run_device(2.0)
            chunks = h.client.payloads("stream")
            # Fondo -> sesión -> fondo con el mismo stream_id y el seq seguido (la ventana parcial no consume seq).
            for stream_id, numbers in seqs([c for c in chunks if c["stream_id"] != "emg_lat_left"]).items():
                assert numbers == list(range(len(numbers))), stream_id
            assert [c["session_id"] == SESSION for c in chunks if c["stream_id"] == "imu_l3"] == [False] * 2 + [True] * 10 + [False] * 2
            assert h.client.payloads("status")[-1]["streams"][0]["state"] == "disabled"  # EMG fuera de sesión

    go(scenario())


def test_starting_and_ending_a_session_restarts_the_background_windows(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.run_device(1.5)  # ventana de fondo del cordón a medias (lotes de 1100 a 1500 ms)
            await h.send(start(1, stream_ids=["imu_l3"]))  # el cordón sigue de fondo, pero su ventana empieza de cero
            await h.run_device(1.5)
            await h.send(stop(2))
            await h.run_device(1.0)
            cord = [c["t0_device_ms"] for c in h.client.payloads("stream") if c["stream_id"] == "stretch_lumbar"]
            assert cord == [100, 1600, 3100]

    go(scenario())


def test_max_duration_ends_the_session_by_the_injected_clock(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.send(start(1, max_duration_s=3))
            await h.run_device(2.9)
            assert statuses_of(h, 1) == ["accepted"]
            await h.run_device(0.2)
            assert statuses_of(h, 1) == ["accepted", "completed"]
            completed = h.client.acks(cid(1))[-1]
            assert completed["timestamp"] == "2026-11-02T09:00:03Z"
            # El estado sale en el acto: el EMG vuelve a no participar y el resto a 10 Hz de fondo.
            status = h.client.payloads("status")[-1]
            assert status["timestamp"] == "2026-11-02T09:00:03Z"
            assert [(s["state"], s.get("sample_rate_hz")) for s in status["streams"]] == [("disabled", None), ("active", 10.0), ("active", 10.0)]
            assert max(c["received_at"] for c in session_chunks(h)) <= "2026-11-02T09:00:03Z"
            await h.send(start(2, OTHER))  # ya no está ocupado
            assert statuses_of(h, 2) == ["accepted"]

    go(scenario())


def test_stop_before_max_duration_cancels_its_timer(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.send(start(1, max_duration_s=3))
            await h.run_device(1.0)
            sleeping = h.time.pending()
            await h.send(stop(2))
            assert h.time.pending() == sleeping - 1  # su temporizador ya no espera
            await h.send(start(3, OTHER))
            await h.run_device(5.0)
            assert statuses_of(h, 1) == ["accepted", "completed"]
            assert statuses_of(h, 3) == ["accepted"]  # el temporizador de la primera no acaba la segunda

    go(scenario())


def test_start_session_anchors_the_clock_with_the_last_tick_or_the_next_one(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.run_device(2.5)
            before = len(h.client.payloads("clock"))
            await h.send(start(1))
            clock = h.client.payloads("clock")
            assert len(clock) == before + 1 and clock[-1]["device_ms"] == 2000 and clock[-1]["utc"] == "2026-11-02T09:00:02Z"
            await h.send(stop(2))
            # Arranque nuevo sin tick aún: el ancla queda pendiente hasta el primero (nunca se inventa).
            await h.hello(boot_id=8)
            clocks = len(h.client.payloads("clock"))
            await h.send(start(3, OTHER))
            assert len(h.client.payloads("clock")) == clocks
            h.device_ms = 1000
            await h.tick()
            assert h.client.payloads("clock")[-1]["boot_id"] == 8 and len(h.client.payloads("clock")) == clocks + 1

    go(scenario())


def test_start_session_rejections(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.send(start(1))
            await h.link_up()
            await h.send(start(2))
            assert h.client.acks(cid(1))[0]["error"] == {"code": "device_offline", "message": "the device is not connected"}
            assert h.client.acks(cid(2))[0]["error"]["code"] == "device_offline"
            await h.boot()
            await h.send(start(3, stream_ids=["imu_l3", "nope_a", "nope_b"]))
            assert h.client.acks(cid(3))[0]["error"] == {"code": "unknown_stream", "message": "unknown streams: nope_a, nope_b"}
            await h.send(start(4))
            await h.send(start(5, OTHER))
            assert h.client.acks(cid(5))[0]["error"] == {"code": "busy", "message": f"session {SESSION} is already running on this device"}
            for n in (1, 2, 3, 5):
                assert statuses_of(h, n) == ["rejected"]

    go(scenario())


def test_stop_and_mark_event_need_the_running_session(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.send(stop(1))
            await h.send(mark(2))
            await h.send(start(3))
            await h.send(stop(4, OTHER))
            await h.send(mark(5, OTHER))
            for n in (1, 2, 4, 5):
                (ack,) = h.client.acks(cid(n))
                assert ack["status"] == "rejected" and ack["error"]["code"] == "no_session"
            assert h.client.acks(cid(4))[0]["error"]["message"] == f"session {OTHER} is not running"
            await h.send(mark(6))
            assert statuses_of(h, 6) == ["accepted", "completed"]

    go(scenario())


def test_a_link_drop_keeps_the_session_but_new_device_commands_are_rejected(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.send(start(1))
            await h.run_device(1.0)
            await h.link_down()
            await h.send(start(2, OTHER))
            assert h.client.acks(cid(2))[0]["error"]["code"] == "busy"  # mismo orden que el gateway falso: busy antes que offline
            await h.send(mark(3))  # no necesita el dispositivo
            assert statuses_of(h, 3) == ["accepted", "completed"]
            await h.link_up()
            await h.hello(boot_id=8)
            await h.run_device(0.5)
            after = [c for c in session_chunks(h) if c["boot_id"] == 8]
            assert after and min(c["seq"] for c in after) == 0  # la sesión sigue con el arranque nuevo
            await h.send(stop(4))
            assert statuses_of(h, 1) == ["accepted", "completed"]

    go(scenario())
