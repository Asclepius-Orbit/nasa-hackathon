"""`uv run spine-sim-gateway`: arranca el gateway falso con la configuración de SPINE_SIM_*."""

import asyncio

from spine_physiology.gateway.app import serve
from spine_physiology.gateway.settings import GatewaySettings


def main() -> None:
    settings = GatewaySettings()  # los campos obligatorios vienen del entorno
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
