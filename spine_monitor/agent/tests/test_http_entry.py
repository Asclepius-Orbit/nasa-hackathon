"""Entry points of the chat HTTP server: the routes against the contract, the token, the settings and `main`."""

import os
import stat
import sys
from pathlib import Path
from typing import Any

import pydantic_ai
import pytest
import yaml
from pydantic_ai.models.test import TestModel
from spine_agent import http
from spine_agent.agent import build_agent
from spine_agent.settings import AgentSettings
from spine_contracts import openapi_drift

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "agent-openapi.yaml"
TOKEN = "test-token"


def settings(**overrides: Any) -> AgentSettings:
    values: dict[str, Any] = {"mcp_command": sys.executable, "mcp_args": ["-m", "spine_mcp_server", "--dataset", "examples"], "http_token": TOKEN}
    return AgentSettings(**{**values, **overrides})


def contract() -> dict[str, Any]:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


@pytest.mark.contract
def test_routes_match_the_contract():
    s = settings()
    app = http.create_app(s, agent=build_agent(s, model=TestModel(), toolsets=[]))
    assert openapi_drift.diff(contract(), app.openapi()) == []


@pytest.mark.unit
def test_the_token_comes_from_the_settings_then_the_file_else_a_new_file(tmp_path: Path):
    file = tmp_path / "secrets" / "agent-token"
    assert http.token_of(settings(http_token="direct", http_token_file=file)) == "direct"
    generated = http.token_of(settings(http_token=None, http_token_file=file))
    assert len(generated) >= 32 and file.read_text(encoding="utf-8") == generated
    if os.name == "posix":
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert http.token_of(settings(http_token=None, http_token_file=file)) == generated
    file.write_text("  from-file\n", encoding="utf-8")
    assert http.token_of(settings(http_token=None, http_token_file=file)) == "from-file"


@pytest.mark.unit
def test_http_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    for name in ("TOKEN", "TOKEN_FILE", "HOST", "PORT", "MAX_CONVERSATIONS", "IDLE_TTL_S", "KEEPALIVE_S"):
        monkeypatch.delenv(f"SPINE_AGENT_HTTP_{name}", raising=False)
    s = AgentSettings()
    assert (s.http_host, s.http_port, s.http_token, s.http_token_file) == ("127.0.0.1", 8003, None, None)
    assert (s.http_max_conversations, s.http_idle_ttl_s, s.http_keepalive_s) == (16, 3600.0, 15.0)


@pytest.mark.unit
def test_main_serves_the_app_on_the_configured_address(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPINE_AGENT_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("SPINE_AGENT_HTTP_PORT", "9123")
    monkeypatch.setattr(pydantic_ai, "BANNER_ENABLED", True)
    served: dict[str, Any] = {}

    def run(app, **options: Any) -> None:
        served.update(options, app=app)

    assert http.main(run=run) == 0
    assert (served["host"], served["port"]) == ("127.0.0.1", 9123)
    assert served["timeout_graceful_shutdown"] > 0
    assert pydantic_ai.BANNER_ENABLED is False  # the server log carries no banner
    assert openapi_drift.diff(contract(), served["app"].openapi()) == []


@pytest.mark.unit
def test_the_default_token_file_is_in_the_home_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    token = http.token_of(settings(http_token=None))
    assert (tmp_path / ".spine-agent" / "http-token").read_text(encoding="utf-8") == token


@pytest.mark.unit
def test_an_empty_token_file_gets_a_new_token(tmp_path: Path):
    file = tmp_path / "agent-token"
    file.write_text("\n", encoding="utf-8")
    token = http.token_of(settings(http_token=None, http_token_file=file))
    assert token and file.read_text(encoding="utf-8") == token


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "unreachable"),
    [
        (ConnectionRefusedError(), True),
        (ExceptionGroup("tasks", [BrokenPipeError(), FileNotFoundError()]), True),
        (ExceptionGroup("tasks", [BrokenPipeError(), ValueError()]), False),
        (RuntimeError("Client failed to connect"), False),
        (KeyError("bug"), False),
    ],
)
def test_only_failures_to_reach_the_tool_server_are_unavailable(failure: BaseException, unreachable: bool):
    assert http._unreachable(failure) is unreachable  # pyright: ignore[reportPrivateUsage]


@pytest.mark.unit
def test_a_wrapped_os_error_is_unavailable():
    try:
        try:
            raise FileNotFoundError("spine-mcp-server")
        except FileNotFoundError as cause:
            raise RuntimeError("Client failed to connect") from cause
    except RuntimeError as failure:
        assert http._unreachable(failure)  # pyright: ignore[reportPrivateUsage]
