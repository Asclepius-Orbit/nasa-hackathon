"""Test de integración del servidor por stdio: se lanza `python -m spine_mcp_server` como subproceso y se habla MCP con él."""

import os
import sys

import anyio
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from spine_contracts.validation import validate

TOOLS = {"get_current_status", "get_history", "compare_periods", "explain_assessment", "simulate_scenario", "get_sessions", "get_self_reports"}
TIMEOUT_SECONDS = 30


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.integration
@pytest.mark.anyio
async def test_stdio_server_lists_the_seven_tools_and_answers_get_current_status():
    params = StdioServerParameters(command=sys.executable, args=["-m", "spine_mcp_server"])
    # El stderr del servidor se descarta: el predeterminado (`sys.stderr`) puede estar cerrado bajo la captura de pytest o de mutmut.
    with anyio.fail_after(TIMEOUT_SECONDS), open(os.devnull, "w") as errlog:
        async with Client(stdio_client(params, errlog=errlog)) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            result = await client.call_tool("get_current_status", {"astronaut_id": "AST-01"})
    assert tools == TOOLS
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["astronaut_id"] == "AST-01"
    validate("app/status", result.structured_content)
