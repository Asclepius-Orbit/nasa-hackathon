"""`uv run spine-sim-gateway`: starts the fake gateway with the SPINE_SIM_* configuration."""

import asyncio
import logging
import sys

from spine_physiology.gateway.app import serve
from spine_physiology.gateway.settings import GatewaySettings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = GatewaySettings()  # pyright: ignore[reportCallIssue]  # pydantic-settings: required fields come from the environment
    # aiomqtt (paho) needs add_reader/add_writer, which the Windows Proactor loop lacks.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        asyncio.run(serve(settings), loop_factory=loop_factory)
    except KeyboardInterrupt:
        pass  # Ctrl+C: `serve` already published the offline status when cancelled


if __name__ == "__main__":
    main()
