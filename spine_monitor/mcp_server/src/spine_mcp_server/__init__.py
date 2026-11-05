"""Servidor MCP de Asclepius Orbit (stdio). Por ahora habla con mocks basados en los ejemplos de los contratos."""

from spine_mcp_server.mocks import ExampleAppReader, ExampleAssessBaseSource, ExampleSimulator
from spine_mcp_server.server import create_server


def main() -> None:
    """Arranca el servidor MCP por stdio con los mocks de ejemplo."""
    create_server(ExampleAppReader(), ExampleSimulator(), ExampleAssessBaseSource()).run("stdio")
