"""Agent settings: local LLM endpoint, sampling, MCP server launch and the chat HTTP server. SPINE_AGENT_* variables or `.env`."""

import sys
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPINE_AGENT_", env_file=".env", extra="ignore")

    # Local LLM served through an OpenAI-compatible API (Ollama by default).
    ollama_base_url: str = "http://localhost:11434/v1"
    model: str = "qwen3.5:4b"  # chosen with `python -m evals.run_live`; see the model note in the agent README
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    request_timeout_s: float = Field(default=60.0, gt=0.0)

    # How the agent launches the MCP server (stdio).
    mcp_command: str = sys.executable
    mcp_args: list[str] = ["-m", "spine_mcp_server"]
    mcp_log_file: Path | None = None  # where the server subprocess writes its stderr; None: the chat CLI discards it

    # Chat HTTP+SSE server (`spine-agent-http`, contracts/agent-openapi.yaml). Loopback only by default.
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8003, ge=1, le=65535)
    # Bearer token shared with the Electron main process. Unset: read from `http_token_file`, created with a random
    # token when missing (default ~/.spine-agent/http-token).
    http_token: SecretStr | None = None
    http_token_file: Path | None = None
    http_max_conversations: int = Field(default=16, ge=1)  # conversations kept in memory; the least recently used idle one goes first
    http_idle_ttl_s: float = Field(default=3600.0, gt=0.0)  # an idle conversation is forgotten after this long
    http_keepalive_s: float = Field(default=15.0, gt=0.0)  # SSE comment sent after this long without an event
