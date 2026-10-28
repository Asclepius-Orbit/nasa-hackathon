"""`uv run spine-engine`: arranca la API en 127.0.0.1:8001 (SPINE_ENGINE_HOST / SPINE_ENGINE_PORT)."""

import uvicorn

from spine_engine.api.app import create_default_app
from spine_engine.settings import EngineSettings


def main() -> None:
    settings = EngineSettings()
    uvicorn.run(create_default_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
