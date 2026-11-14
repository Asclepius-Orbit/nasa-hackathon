"""Composición del gateway real: ``serve`` (SIGTERM), ``build_source`` (puerto serie) y ``python -m spine_gateway``."""

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest
from spine_gateway import __main__ as entry
from spine_gateway import app
from spine_gateway.ports import LinkUp
from spine_gateway.settings import GatewaySettings


def go(coro) -> None:
    asyncio.run(coro)


def _serve_task(harness, fakes):
    h = harness()
    task = asyncio.create_task(app.serve(h.settings, source=h.source, client_factory=h.connections, clock=h.time, sleep=h.time.sleep, new_id=lambda: "unused"))
    return h, task


def test_serve_runs_the_gateway_and_a_cancellation_propagates_after_the_offline_status(harness, fakes) -> None:
    async def scenario() -> None:
        h, task = _serve_task(harness, fakes)
        await h.time.advance(0.0)
        await h.boot()
        assert h.client.statuses()[-1] == "ok"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert h.client.statuses()[-1] == "offline" and h.source.closed

    go(scenario())


@pytest.mark.skipif(sys.platform == "win32", reason="el bucle de Windows no admite manejadores de señales")
def test_sigterm_stops_serve_cleanly(harness, fakes) -> None:
    async def scenario() -> None:
        h, task = _serve_task(harness, fakes)
        await h.time.advance(0.0)
        signal.raise_signal(signal.SIGTERM)
        await asyncio.wait_for(task, 5)  # vuelve sin excepción: la parada fue pedida
        assert h.client.statuses()[-1] == "offline"
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL  # el manejador se quita al salir

    go(scenario())


def test_build_source_opens_the_configured_serial_port(settings: GatewaySettings) -> None:
    async def scenario() -> None:
        # loop:// es un puerto de pyserial en memoria: abre sin hardware.
        source = app.build_source(settings.model_copy(update={"serial_url": "loop://", "serial_baud": 9600}))
        events = source.events()
        first = await asyncio.wait_for(anext(events), 5)
        assert isinstance(first, LinkUp) and first.at.tzinfo is not None
        aclose = events.aclose
        await aclose()

    go(scenario())


def test_build_source_passes_the_reconnect_settings(settings: GatewaySettings) -> None:
    from spine_gateway.serial_source import SerialLineSource

    custom = settings.model_copy(update={"serial_url": "COM9", "serial_baud": 115200})
    source = app.build_source(custom)
    assert isinstance(source, SerialLineSource)
    assert vars(source)["_url"] == "COM9" and vars(source)["_baud"] == 115200
    assert (vars(source)["_initial"], vars(source)["_factor"], vars(source)["_max"]) == (
        custom.serial_reconnect_initial_s,
        custom.serial_reconnect_factor,
        custom.serial_reconnect_max_s,
    )


def test_utc_now_is_aware_utc() -> None:
    now = app.utc_now()
    assert now.tzinfo is not None and now.utcoffset() is not None and now.utcoffset().total_seconds() == 0


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in [n for n in os.environ if n.startswith("SPINE_GATEWAY_")]:
        monkeypatch.delenv(name)


def test_main_reads_the_settings_from_the_environment_and_runs(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("SPINE_GATEWAY_DEVICE_ID", "wb-07")
    seen: list[tuple[GatewaySettings, type]] = []

    async def run(settings: GatewaySettings) -> None:
        seen.append((settings, type(asyncio.get_running_loop())))

    entry.main(run)
    ((settings, loop),) = seen
    assert settings.device_id == "wb-07"
    if sys.platform == "win32":
        assert issubclass(loop, asyncio.SelectorEventLoop)  # aiomqtt (paho) necesita add_reader


def test_main_exits_quietly_on_ctrl_c(clean_env: None) -> None:
    async def run(settings: GatewaySettings) -> None:
        raise KeyboardInterrupt

    entry.main(run)  # no lanza


def test_new_uuid_is_a_random_uuid() -> None:
    import uuid

    first, second = app.new_uuid(), app.new_uuid()
    assert uuid.UUID(first).version == 4 and first != second


def test_the_clock_must_be_aware_utc(settings: GatewaySettings) -> None:
    from datetime import datetime, timedelta, timezone

    for wrong in (datetime(2026, 11, 2, 9, 0), datetime(2026, 11, 2, 9, 0, tzinfo=timezone(timedelta(hours=1)))):
        with pytest.raises(ValueError, match="aware UTC"):
            app.Gateway(settings, clock=lambda wrong=wrong: wrong)


def test_python_m_spine_gateway_runs_main(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    import runpy

    from pydantic import ValidationError

    monkeypatch.setenv("SPINE_GATEWAY_MQTT_PORT", "0")  # falla al leer los ajustes: no llega a abrir nada
    monkeypatch.delitem(sys.modules, "spine_gateway.__main__", raising=False)
    with pytest.raises(ValidationError):
        runpy.run_module("spine_gateway", run_name="__main__")
