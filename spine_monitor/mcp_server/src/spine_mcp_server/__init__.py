"""Servidor MCP de Asclepius Orbit (stdio). Por defecto usa la app y el motor reales por http; `--dataset` elige demo o ejemplos."""

import argparse
import os
from collections.abc import Sequence
from datetime import UTC, datetime

from mcp.server.mcpserver import MCPServer

from spine_mcp_server.demo_data import build_demo_app_reader, build_demo_assess_base, build_demo_simulator
from spine_mcp_server.http_client import HttpAppReader, HttpAssessBaseSource, HttpSimulator
from spine_mcp_server.mocks import ExampleAppReader, ExampleAssessBaseSource, ExampleSimulator
from spine_mcp_server.server import create_server

APP_URL_ENV = "SPINE_APP_URL"
DEFAULT_APP_URL = "http://127.0.0.1:8002"
ENGINE_URL_ENV = "SPINE_ENGINE_URL"
DEFAULT_ENGINE_URL = "http://127.0.0.1:8001"


def _aware_iso(text: str) -> datetime:
    """ISO 8601 con zona horaria (p. ej. `2026-11-13T12:00:00Z`); sin zona es ambiguo y se rechaza."""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 timestamp: {text!r}") from error
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError(f"timestamp needs a timezone (e.g. 'Z'): {text!r}")
    return moment.astimezone(UTC)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Opciones de `python -m spine_mcp_server`."""
    parser = argparse.ArgumentParser(prog="spine_mcp_server", description="Servidor MCP de Asclepius Orbit por stdio.")
    parser.add_argument(
        "--dataset",
        choices=("demo", "examples", "http"),
        default="http",
        help=f"http (por defecto): la API de lectura de la app en {APP_URL_ENV} (por defecto {DEFAULT_APP_URL}) y el motor en {ENGINE_URL_ENV} "
        f"(por defecto {DEFAULT_ENGINE_URL}); demo: 14 días de AST-01 relativos al reloj; examples: la foto única de los ejemplos de los contratos.",
    )
    parser.add_argument("--now", type=_aware_iso, default=None, help="Fija el reloj del dataset demo (ISO 8601 con zona, p. ej. 2026-11-13T12:00:00Z).")
    return parser.parse_args(argv)


def build_server(argv: Sequence[str] | None = None) -> MCPServer:
    """Servidor MCP con el dataset elegido en `argv`; el demo usa el reloj de pared salvo que `--now` lo fije."""
    args = parse_args(argv)
    if args.dataset == "examples":
        return create_server(ExampleAppReader(), ExampleSimulator(), ExampleAssessBaseSource())
    if args.dataset == "http":
        app_url = os.environ.get(APP_URL_ENV, "").strip() or DEFAULT_APP_URL
        return create_server(
            HttpAppReader(app_url),
            HttpSimulator(os.environ.get(ENGINE_URL_ENV, "").strip() or DEFAULT_ENGINE_URL),
            HttpAssessBaseSource(app_url),
        )
    now = args.now or datetime.now(UTC)
    return create_server(build_demo_app_reader(now), build_demo_simulator(now), build_demo_assess_base(now))


def main(argv: Sequence[str] | None = None) -> None:
    """Arranca el servidor MCP por stdio (`argv` por defecto: `sys.argv[1:]`)."""
    build_server(argv).run("stdio")
