"""Servidor MCP de Asclepius Orbit (stdio). Habla con datos de demo o con los ejemplos de los contratos, según `--dataset`."""

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime

from mcp.server.mcpserver import MCPServer

from spine_mcp_server.demo_data import build_demo_app_reader, build_demo_assess_base, build_demo_simulator
from spine_mcp_server.mocks import ExampleAppReader, ExampleAssessBaseSource, ExampleSimulator
from spine_mcp_server.server import create_server


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
        choices=("demo", "examples"),
        default="demo",
        help="demo: 14 días de AST-01 relativos al reloj (por defecto); examples: la foto única de los ejemplos de los contratos.",
    )
    parser.add_argument("--now", type=_aware_iso, default=None, help="Fija el reloj del dataset demo (ISO 8601 con zona, p. ej. 2026-11-13T12:00:00Z).")
    return parser.parse_args(argv)


def build_server(argv: Sequence[str] | None = None) -> MCPServer:
    """Servidor MCP con el dataset elegido en `argv`; el demo usa el reloj de pared salvo que `--now` lo fije."""
    args = parse_args(argv)
    if args.dataset == "examples":
        return create_server(ExampleAppReader(), ExampleSimulator(), ExampleAssessBaseSource())
    now = args.now or datetime.now(UTC)
    return create_server(build_demo_app_reader(now), build_demo_simulator(now), build_demo_assess_base(now))


def main(argv: Sequence[str] | None = None) -> None:
    """Arranca el servidor MCP por stdio (`argv` por defecto: `sys.argv[1:]`)."""
    build_server(argv).run("stdio")
