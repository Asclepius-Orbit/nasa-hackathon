import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from spine_physiology import MissionGenerator
from spine_physiology.envelope import MQTT_KINDS, Envelope, EnvelopeKind
from spine_physiology.sinks.jsonl import write_jsonl
from spine_physiology.sinks.mqtt import MqttSink, encode, publish_all
from spine_physiology.sinks.pacing import Pacer, paced

class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes, int, bool]] = []

    async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        self.published.append((topic, payload, qos, retain))

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("_tiny_mission", Path(__file__).with_name("test_generator.py"))
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
tiny_config = _module.tiny_config


T0 = datetime(2030, 1, 1, tzinfo=UTC)


def env(seconds: float) -> Envelope:
    return Envelope(kind=EnvelopeKind.SESSION, topic=None, payload={"s": seconds}, at=T0 + timedelta(seconds=seconds), retained=False, astronaut_id="AST-01", day=0)


class FakeTime:
    """Reloj y sleep falsos: dormir avanza el reloj."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def collect(messages, pacer):
    return [m async for m in paced(messages, pacer)]


def test_batch_never_sleeps():
    ft = FakeTime()
    out = asyncio.run(collect([env(0), env(5), env(900)], Pacer(None, clock=ft.clock, sleep=ft.sleep)))
    assert len(out) == 3 and ft.sleeps == []


@pytest.mark.parametrize(("speed", "expected"), [(1.0, [5.0, 5.0]), (10.0, [0.5, 0.5]), (0.5, [10.0, 10.0])])
def test_speed_scales_simulated_gaps(speed, expected):
    ft = FakeTime()
    asyncio.run(collect([env(0), env(5), env(10)], Pacer(speed, clock=ft.clock, sleep=ft.sleep)))
    assert ft.sleeps == pytest.approx(expected)


def test_slow_consumer_gets_shorter_sleep_and_never_negative():
    ft = FakeTime()
    pacer = Pacer(1.0, clock=ft.clock, sleep=ft.sleep)

    async def run() -> None:
        await pacer.wait_for(T0)
        ft.now += 3.0  # el consumidor tardó 3 s
        await pacer.wait_for(T0 + timedelta(seconds=5))
        ft.now += 50.0  # muy por detrás: ni espera ni valores negativos
        await pacer.wait_for(T0 + timedelta(seconds=6))

    asyncio.run(run())
    assert ft.sleeps == pytest.approx([2.0])


def test_order_and_objects_untouched():
    messages = [env(0), env(0), env(1)]
    ft = FakeTime()
    assert asyncio.run(collect(messages, Pacer(2.0, clock=ft.clock, sleep=ft.sleep))) == messages


def test_invalid_speed():
    for bad in (0, -1.0):
        with pytest.raises(ValueError):
            Pacer(bad)


def test_payloads_identical_across_jsonl_mqtt_and_paced(tmp_path):
    config = tiny_config()
    messages = list(MissionGenerator(config).iter_messages())
    direct = FakeClient()
    asyncio.run(publish_all(messages, MqttSink(direct)))
    ft = FakeTime()
    paced_client = FakeClient()
    stream = paced(MissionGenerator(config).iter_messages(), Pacer(3600.0, clock=ft.clock, sleep=ft.sleep))
    asyncio.run(publish_all(stream, MqttSink(paced_client)))
    assert paced_client.published == direct.published
    assert ft.sleeps  # realmente esperó
    assert len(direct.published) == len([m for m in messages if m.kind in MQTT_KINDS])
    write_jsonl(messages, tmp_path, config=config, seed=config.seed)
    # Los payloads de chunk del JSONL coinciden byte a byte con los encode() que van a MQTT.
    records = []
    for path in sorted(tmp_path.rglob("chunks.jsonl")):
        records += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    jsonl_chunks = sorted(json.dumps(r["payload"], separators=(",", ":"), ensure_ascii=False) for r in records)
    expected = sorted(encode(m).decode("utf-8") for m in messages if m.kind is EnvelopeKind.STREAM_CHUNK)
    assert jsonl_chunks == expected
