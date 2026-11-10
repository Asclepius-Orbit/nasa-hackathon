"""On-board app: bus ingestion, commands, engine calls and the UI API.

`create_app` is a factory (`uvicorn --factory monitor.main:create_app`), so the database is not opened
on import. The lifespan starts and stops the bus connection and the ground sender. Tests inject the MQTT
client, the HTTP transport of the engine, the ground transport and the clock; in production they come from
`AppSettings` (SPINE_APP_*), and the ground transport is the simulated one, the only adapter so far.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import aiomqtt
import httpx
from fastapi import FastAPI

from monitor.analysis import Analysis
from monitor.api import ViewSettings, build_router, install_problem_handlers
from monitor.control import Control
from monitor.db import Store
from monitor.engine import EngineClient
from monitor.ground.enqueue import enqueue_ground_day, enqueue_ground_summary
from monitor.ground.sender import GroundSender
from monitor.ground.transport import FakeGroundTransport, GroundTransport
from monitor.ingest import Bus, ClientFactory, Ingest
from monitor.live.stream import RepStream, closing_on_exit_signal
from monitor.live.tracker import RepHub
from monitor.settings import AppSettings

_log = logging.getLogger(__name__)


@dataclass
class Runtime:
    store: Store
    bus: Bus
    control: Control
    analysis: Analysis
    ingest: Ingest
    hub: RepHub
    reps: RepStream
    ground: GroundTransport
    sender: GroundSender


def create_app(
    settings: AppSettings | None = None,
    *,
    mqtt_client_factory: ClientFactory | None = None,
    engine_transport: httpx.AsyncBaseTransport | None = None,
    ground_transport: GroundTransport | None = None,
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
    ground = ground_transport or FakeGroundTransport(clock)
    sender = GroundSender(
        store,
        ground,
        settings.ground_windows,
        clock=clock,
        retry_min_s=settings.ground_retry_min_s,
        retry_max_s=settings.ground_retry_max_s,
        tick_s=settings.ground_tick_s,
        send_timeout_s=settings.ground_send_timeout_s,
    )

    def queue_for_ground(assessment: dict[str, Any]) -> None:
        enqueue_ground_summary(store, assessment, clock())
        sender.wake()

    def queue_day_for_ground(astronaut_id: str, day: date) -> None:
        enqueue_ground_day(store, astronaut_id, day, clock())
        sender.wake()

    analysis = Analysis(store, EngineClient(http), window_days=settings.assess_window_days, clock=clock, on_assessment=queue_for_ground)
    hub = RepHub(store, clock, no_signal_s=settings.live_no_signal_s)
    bus = Bus(factory, reconnect_min_s=settings.mqtt_reconnect_min_s, reconnect_max_s=settings.mqtt_reconnect_max_s)
    control = Control(store, bus, analysis.process_session, ack_timeout_s=settings.ack_timeout_s, clock=clock, on_session_closed=hub.finish)
    ingest = Ingest(store, control, store_unknown_session_chunks=settings.store_unknown_session_chunks, live=hub)
    reps = RepStream(hub, tick_s=settings.live_tick_s, heartbeat_s=settings.live_heartbeat_s)
    runtime = Runtime(store, bus, control, analysis, ingest, hub, reps, ground, sender)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        bus_task = asyncio.create_task(bus.run(ingest), name="mqtt-bus")
        sender_task = asyncio.create_task(sender.run(), name="ground-sender")
        loop = asyncio.get_running_loop()
        try:
            # A signal handler runs between two bytecodes of whatever the loop was doing: the hub is closed from the loop.
            with closing_on_exit_signal(lambda: loop.call_soon_threadsafe(hub.close)):
                yield
        finally:
            hub.close()
            # A send in flight stays queued. An analysis still running below may queue more: the next start sends it.
            for task in (sender_task, bus_task):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await control.idle()
            control.close()
            await http.aclose()
            store.close()

    app = FastAPI(title="Spine Monitor app", version="0.1.0", lifespan=lifespan)
    app.state.runtime = runtime
    install_problem_handlers(app)
    app.include_router(
        build_router(
            store,
            control,
            analysis,
            odi_interval_days=settings.weekly_odi_interval_days,
            views=ViewSettings.from_app(settings),
            reps=reps,
            clock=clock,
            queue_day=queue_day_for_ground,
        )
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "mqtt_connected": runtime.bus.connected.is_set(),
            "messages": runtime.ingest.counts,
        }

    return app

