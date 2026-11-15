"""Asclepius Orbit MCP server (stdio). By default it uses the real app and engine over http; `--dataset` selects demo or examples."""

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
    """ISO 8601 with a timezone (e.g. `2026-11-13T12:00:00Z`); without a zone it is ambiguous and rejected."""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 timestamp: {text!r}") from error
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError(f"timestamp needs a timezone (e.g. 'Z'): {text!r}")
    return moment.astimezone(UTC)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Options of `python -m spine_mcp_server`."""
    parser = argparse.ArgumentParser(prog="spine_mcp_server", description="Asclepius Orbit MCP server over stdio.")
    parser.add_argument(
        "--dataset",
        choices=("demo", "examples", "http"),
        default="http",
        help=f"http (default): the app read API at {APP_URL_ENV} (default {DEFAULT_APP_URL}) and the engine at {ENGINE_URL_ENV} "
        f"(default {DEFAULT_ENGINE_URL}); demo: 14 days of AST-01 relative to the clock; examples: the single snapshot of the contract examples.",
    )
    parser.add_argument("--now", type=_aware_iso, default=None, help="Fix the clock of the demo dataset (ISO 8601 with a timezone, e.g. 2026-11-13T12:00:00Z).")
    return parser.parse_args(argv)


def build_server(argv: Sequence[str] | None = None) -> MCPServer:
    """MCP server with the dataset chosen in `argv`; the demo uses the wall clock unless `--now` fixes it."""
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
    """Starts the MCP server over stdio (`argv` defaults to `sys.argv[1:]`)."""
    build_server(argv).run("stdio")
