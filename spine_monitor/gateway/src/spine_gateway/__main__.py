"""``uv run spine-gateway`` (o ``python -m spine_gateway``): gateway real con la configuración de SPINE_GATEWAY_*."""

import asyncio
import logging
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from spine_gateway import app
from spine_gateway.settings import GatewaySettings


def main(run: Callable[[GatewaySettings], Coroutine[Any, Any, None]] = app.main) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = GatewaySettings()
    # aiomqtt (paho) necesita add_reader/add_writer, que el bucle Proactor de Windows no tiene.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        asyncio.run(run(settings), loop_factory=loop_factory)
    except KeyboardInterrupt:
        pass  # Ctrl+C: `serve` ya publicó el estado offline al cancelarse


if __name__ == "__main__":
    main()
