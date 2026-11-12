"""Comando calibrate del gateway real: grabación de stretch y emg_mvc con los lotes del dispositivo."""

import asyncio
from typing import Any

import pytest
from spine_contracts import topics
from spine_gateway.protocol import FrameKind

AST, DEV = "AST-01", "wb-01"
SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"
FIRST_ID = "ca11b000-0000-4000-8000-000000000001"


def go(coro) -> None:
    asyncio.run(coro)


def cid(n: int) -> str:
    return f"c0ffee00-0000-4000-8000-{n:012d}"


def command(kind: str, n: int, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0", "command_id": cid(n), "astronaut_id": AST, "device_id": DEV,
        "issued_at": "2026-11-02T09:00:00Z", "type": kind, **fields,
    }  # fmt: skip


def calibrate(n: int, calibration_type: str, stream_ids: list[str], **fields: Any) -> dict[str, Any]:
    return command("calibrate", n, session_id=SESSION, calibration_type=calibration_type, stream_ids=stream_ids, **fields)


def start(n: int, **fields: Any) -> dict[str, Any]:
    return command("start_session", n, session_id=SESSION, session_type="spine_check", protocol_id="spine_length_rest_v1", **fields)


def acks(h, n: int) -> list[dict[str, Any]]:
    return h.client.acks(cid(n))


def test_stretch_calibration_is_published_retained_before_its_completed_ack_and_feeds_length(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.run_device(1.0)
            await h.send(calibrate(1, "stretch", ["stretch_lumbar"], duration_s=2))
            assert [a["status"] for a in acks(h, 1)] == ["accepted"]
            await h.run_device(1.9)
            assert h.client.on("calibration") == []
            background_during = [c for c in h.client.payloads("stream") if c["stream_kind"] == "stretch"]
            assert len(background_during) == 2  # los chunks siguen saliendo durante la calibración
            await h.run_device(0.2)
            relevant = [p for p in h.client.published if p.channel in ("calibration", "ack")]
            calibration, completed = relevant[-2:]
            assert calibration.topic == topics.calibration(AST, DEV, "stretch_lumbar") and calibration.retain
            body = calibration.payload
            assert body["calibration_id"] == FIRST_ID and body["calibration_type"] == "stretch" and body["session_id"] == SESSION
            assert body["rest_resistance_ohm"] == pytest.approx(10000 * 512 / (1023 - 512))
            assert completed.payload["status"] == "completed" and completed.payload["result"] == {"calibration_id": FIRST_ID}
            assert completed.payload["timestamp"] == "2026-11-02T09:00:03Z"
            await h.run_device(1.0)
            cord = h.client.payloads("stream")[-1]
            assert cord["stream_kind"] == "stretch" and cord["calibration_id"] == FIRST_ID
            assert cord["data"]["length_mm"] == pytest.approx([100.0] * 10)  # en reposo: la longitud de reposo

    go(scenario())


def test_emg_mvc_calibration_completes_during_a_session(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.send(start(1))
            await h.send(calibrate(2, "emg_mvc", ["emg_lat_left"], duration_s=1))
            await h.run_device(1.1)
            (calibration,) = h.client.on("calibration")
            assert calibration.topic == topics.calibration(AST, DEV, "emg_lat_left") and calibration.retain
            assert calibration.payload["mvc_rms_mv"] == pytest.approx(300 / 1023 * 5000)
            assert calibration.payload["muscle"] == "latissimus_dorsi" and calibration.payload["window_ms"] == 250
            assert [a["status"] for a in acks(h, 2)] == ["accepted", "completed"]
            assert acks(h, 2)[-1]["result"]["calibration_id"] == calibration.payload["calibration_id"]
            assert [a["status"] for a in acks(h, 1)] == ["accepted"]  # la sesión sigue

    go(scenario())


def test_calibrate_without_duration_uses_the_default(harness) -> None:
    async def scenario() -> None:
        async with harness(calibration_default_duration_s=3.0) as h:
            await h.boot()
            await h.send(calibrate(1, "stretch", ["stretch_lumbar"]))
            await h.run_device(2.9)
            assert [a["status"] for a in acks(h, 1)] == ["accepted"]
            await h.run_device(0.2)
            assert [a["status"] for a in acks(h, 1)] == ["accepted", "completed"]

    go(scenario())


@pytest.mark.parametrize(
    ("calibration_type", "stream_id", "kind", "value", "message"),
    [
        ("emg_mvc", "emg_lat_left", FrameKind.EMG, 0, "flat_signal: the maximum RMS is 0 mV"),
        ("stretch", "stretch_lumbar", FrameKind.CORD, 0, "rest_resistance_not_computable: mean 0.0 counts is at an ADC rail"),
    ],
)
def test_a_calibration_that_cannot_be_computed_fails(harness, calibration_type: str, stream_id: str, kind: FrameKind, value: int, message: str) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            h.values[kind] = value
            await h.send(calibrate(1, calibration_type, [stream_id], duration_s=1))
            await h.run_device(1.1)
            assert [a["status"] for a in acks(h, 1)] == ["accepted", "failed"]
            assert acks(h, 1)[-1]["error"] == {"code": "calibration_failed", "message": message}
            assert h.client.on("calibration") == []
            await h.send(calibrate(2, calibration_type, [stream_id], duration_s=1))  # ya no está ocupado
            assert [a["status"] for a in acks(h, 2)] == ["accepted"]

    go(scenario())


def test_a_calibration_without_data_times_out_five_seconds_after_its_duration(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.send(calibrate(1, "stretch", ["stretch_lumbar"], duration_s=2))
            await h.run_device(6.9, kinds=(FrameKind.EMG, FrameKind.IMU))
            assert [a["status"] for a in acks(h, 1)] == ["accepted"]
            await h.run_device(0.1, kinds=(FrameKind.EMG, FrameKind.IMU))
            assert [a["status"] for a in acks(h, 1)] == ["accepted", "failed"]
            failed = acks(h, 1)[-1]
            assert failed["error"] == {"code": "calibration_failed", "message": "calibration_timeout: no complete data within 7.0 s"}
            assert failed["timestamp"] == "2026-11-02T09:00:07Z"
            await h.run_device(3.0)  # los lotes que llegan después ya no calibran nada
            assert h.client.on("calibration") == []

    go(scenario())


def test_a_completed_calibration_cancels_its_timeout(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.send(calibrate(1, "stretch", ["stretch_lumbar"], duration_s=1))
            sleeping = h.time.pending()
            await h.run_device(1.1)
            assert h.time.pending() == sleeping - 1
            await h.run_device(7.0)
            assert [a["status"] for a in acks(h, 1)] == ["accepted", "completed"]

    go(scenario())


def test_calibrate_rejections(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.send(calibrate(1, "stretch", ["stretch_lumbar"]))
            await h.boot()
            await h.send(calibrate(2, "imu", ["imu_l3"]))
            await h.send(calibrate(3, "stretch", ["stretch_lumbar", "nope_a"]))
            await h.send(calibrate(4, "stretch", ["imu_l3"]))
            await h.send(calibrate(5, "emg_mvc", ["stretch_lumbar"]))
            await h.send(calibrate(6, "stretch", ["stretch_lumbar"]))
            await h.send(calibrate(7, "emg_mvc", ["emg_lat_left"]))
            errors = {n: acks(h, n)[0].get("error") for n in range(1, 8)}
            assert errors == {
                1: {"code": "device_offline", "message": "the device is not connected"},
                2: {"code": "invalid_command", "message": "imu calibration is not supported by this gateway"},
                3: {"code": "unknown_stream", "message": "unknown streams: nope_a"},
                4: {"code": "unknown_stream", "message": "not stretch streams: imu_l3"},
                5: {"code": "unknown_stream", "message": "not emg_mvc streams: stretch_lumbar"},
                6: None,
                7: {"code": "busy", "message": f"calibration {cid(6)} is already running on this device"},
            }

    go(scenario())


def test_calibrate_is_rejected_while_the_link_is_down_even_during_a_session(harness) -> None:
    async def scenario() -> None:
        async with harness() as h:
            await h.boot()
            await h.send(start(1))
            await h.link_down()
            await h.send(calibrate(2, "stretch", ["stretch_lumbar"]))
            assert acks(h, 2)[0]["error"]["code"] == "device_offline"

    go(scenario())
