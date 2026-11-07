"""Agent settings: local LLM endpoint, sampling and MCP server launch. SPINE_AGENT_* variables or `.env`."""

import sys
from pathlib import Path

from pydantic import Field
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
