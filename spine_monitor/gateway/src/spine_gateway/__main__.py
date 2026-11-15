"""``uv run spine-gateway`` (or ``python -m spine_gateway``): the real gateway with the SPINE_GATEWAY_* configuration."""

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
    # aiomqtt (paho) needs add_reader/add_writer, which the Windows Proactor loop does not have.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        asyncio.run(run(settings), loop_factory=loop_factory)
    except KeyboardInterrupt:
        pass  # Ctrl+C: `serve` already published the offline status when cancelled


if __name__ == "__main__":
    main()
