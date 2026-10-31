"""`uv run spine-sim-gateway`: arranca el gateway falso con la configuración de SPINE_SIM_*."""

import asyncio
import logging
import sys

from spine_physiology.gateway.app import serve
from spine_physiology.gateway.settings import GatewaySettings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = GatewaySettings()  # los campos obligatorios vienen del entorno
    # aiomqtt (paho) necesita add_reader/add_writer, que el bucle Proactor de Windows no tiene.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        asyncio.run(serve(settings), loop_factory=loop_factory)
    except KeyboardInterrupt:
        pass  # Ctrl+C: `serve` ya publicó el estado offline al cancelarse


if __name__ == "__main__":
    main()
