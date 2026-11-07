"""StatusSource backed by the MCP server, used by the no-LLM fallback path.

It talks to the server with the plain `mcp` client over stdio, independently of the agent's own toolset, so a broken
model or agent run never takes the fallback down with it. One short-lived subprocess per call: the fallback path is
rare and two calls at most, so simplicity and "never leak a subprocess" win over reuse (the `async with` blocks
terminate the process on exit, also on timeout or cancellation).

Error policy: this source never raises into the fallback path. A tool error (`isError`, e.g. 404 for an unknown
astronaut), a missing structured result, a server that cannot start, dies or hangs past `timeout_s`, all return None,
which the fallback template renders as "I don't know". Cancellation (`asyncio.CancelledError`) is not an Exception
and propagates.
"""

import logging
import os
from typing import Any

import anyio
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from spine_agent.settings import AgentSettings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30.0


class McpStatusSource:
    """Implements `spine_agent.ports.StatusSource` with the `get_current_status` and `explain_assessment` MCP tools."""

    def __init__(self, settings: AgentSettings, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self._params = StdioServerParameters(command=settings.mcp_command, args=list(settings.mcp_args))
        self._timeout_s = timeout_s

    async def current_status(self, astronaut_id: str) -> dict[str, Any] | None:
        return await self._call("get_current_status", {"astronaut_id": astronaut_id})

    async def explanation(self, assessment_id: str) -> dict[str, Any] | None:
        return await self._call("explain_assessment", {"assessment_id": assessment_id})

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        try:
            # The server's stderr is discarded: it would interleave with the chat, and sys.stderr may be closed under
            # test capture. Opening os.devnull does no real I/O, so it does not block the event loop.
            with anyio.fail_after(self._timeout_s), open(os.devnull, "w") as errlog:
                async with Client(stdio_client(self._params, errlog=errlog)) as client:
                    result = await client.call_tool(tool, arguments)
        except Exception:
            logger.warning("MCP fallback call %s failed; reporting no data", tool, exc_info=True)
            return None
        if result.is_error:
            return None
        return result.structured_content
