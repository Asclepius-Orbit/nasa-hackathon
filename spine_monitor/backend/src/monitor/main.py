"""On-board app: bus ingestion, commands, engine calls and the UI API.

`create_app` is a factory (`uvicorn --factory monitor.main:create_app`), so the database is not opened
on import. The lifespan starts and stops the bus connection. Tests inject the MQTT client, the HTTP
transport of the engine and the clock; in production they come from `AppSettings` (SPINE_APP_*).
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiomqtt
import httpx
from fastapi import FastAPI

from monitor.analysis import Analysis
from monitor.api import ViewSettings, build_router, install_problem_handlers
from monitor.control import Control
from monitor.db import Store
from monitor.engine import EngineClient
from monitor.ingest import Bus, ClientFactory, Ingest
from monitor.settings import AppSettings

_log = logging.getLogger(__name__)


@dataclass
class Runtime:
    store: Store
    bus: Bus
    control: Control
    analysis: Analysis
    ingest: Ingest


def create_app(
    settings: AppSettings | None = None,
    *,
    mqtt_client_factory: ClientFactory | None = None,
    engine_transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    settings = settings or AppSettings()
    factory = mqtt_client_factory or (
        lambda: aiomqtt.Client(
            settings.mqtt_host,
            settings.mqtt_port,
            identifier=settings.mqtt_client_id,
            clean_session=False,  # persistent session: the broker keeps QoS 1 messages during an outage
        )
    )

    store = Store(settings.db_path)
    http = httpx.AsyncClient(base_url=settings.engine_url, timeout=settings.engine_timeout_s, transport=engine_transport)
    analysis = Analysis(store, EngineClient(http), window_days=settings.assess_window_days, clock=clock)
    bus = Bus(factory, reconnect_min_s=settings.mqtt_reconnect_min_s, reconnect_max_s=settings.mqtt_reconnect_max_s)
    control = Control(store, bus, analysis.process_session, ack_timeout_s=settings.ack_timeout_s, clock=clock)
    ingest = Ingest(store, control, store_unknown_session_chunks=settings.store_unknown_session_chunks)
    runtime = Runtime(store, bus, control, analysis, ingest)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        bus_task = asyncio.create_task(bus.run(ingest), name="mqtt-bus")
        try:
            yield
        finally:
            bus_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bus_task
            await control.idle()
            control.close()
            await http.aclose()
            store.close()

    app = FastAPI(title="Spine Monitor app", version="0.1.0", lifespan=lifespan)
    app.state.runtime = runtime
    install_problem_handlers(app)
    app.include_router(
        build_router(store, control, analysis, odi_interval_days=settings.weekly_odi_interval_days, views=ViewSettings.from_app(settings), clock=clock)
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "mqtt_connected": runtime.bus.connected.is_set(),
            "messages": runtime.ingest.counts,
        }

    return app

