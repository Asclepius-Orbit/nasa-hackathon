import sys

import pytest
from pydantic import ValidationError
from spine_agent.settings import AgentSettings, mcp_server_env


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    for name in ("OLLAMA_BASE_URL", "MODEL", "TEMPERATURE", "REQUEST_TIMEOUT_S", "MCP_COMMAND", "MCP_ARGS", "MCP_LOG_FILE"):
        monkeypatch.delenv(f"SPINE_AGENT_{name}", raising=False)
    monkeypatch.chdir(tmp_path)  # no stray .env


@pytest.mark.unit
def test_defaults():
    s = AgentSettings()
    assert s.ollama_base_url == "http://localhost:11434/v1"
    assert s.model == "qwen3.5:4b"
    assert s.temperature == 0.2
    assert s.request_timeout_s == 60.0
    assert s.mcp_command == sys.executable
    assert s.mcp_args == ["-m", "spine_mcp_server", "--dataset", "demo"]
    assert s.mcp_log_file is None


@pytest.mark.unit
def test_env_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPINE_AGENT_OLLAMA_BASE_URL", "http://gpu:1234/v1")
    monkeypatch.setenv("SPINE_AGENT_MODEL", "llama3.2:3b")
    monkeypatch.setenv("SPINE_AGENT_TEMPERATURE", "0.7")
    monkeypatch.setenv("SPINE_AGENT_REQUEST_TIMEOUT_S", "5")
    monkeypatch.setenv("SPINE_AGENT_MCP_COMMAND", "uv")
    monkeypatch.setenv("SPINE_AGENT_MCP_ARGS", '["run", "spine-mcp-server"]')
    s = AgentSettings()
    assert s.ollama_base_url == "http://gpu:1234/v1"
    assert s.model == "llama3.2:3b"
    assert s.temperature == 0.7
    assert s.request_timeout_s == 5.0
    assert s.mcp_command == "uv"
    assert s.mcp_args == ["run", "spine-mcp-server"]


@pytest.mark.unit
def test_mcp_log_file_override(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("SPINE_AGENT_MCP_LOG_FILE", str(tmp_path / "mcp.log"))
    assert AgentSettings().mcp_log_file == tmp_path / "mcp.log"


@pytest.mark.unit
@pytest.mark.parametrize("value", ["-0.1", "2.1"])
def test_temperature_out_of_range_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv("SPINE_AGENT_TEMPERATURE", value)
    with pytest.raises(ValidationError):
        AgentSettings()


@pytest.mark.unit
@pytest.mark.parametrize("value", ["0", "2"])
def test_temperature_bounds_are_accepted(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv("SPINE_AGENT_TEMPERATURE", value)
    assert AgentSettings().temperature == float(value)


@pytest.mark.unit
@pytest.mark.parametrize("value", ["0", "-1"])
def test_non_positive_timeout_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv("SPINE_AGENT_REQUEST_TIMEOUT_S", value)
    with pytest.raises(ValidationError):
        AgentSettings()


@pytest.mark.unit
def test_the_mcp_server_inherits_the_app_and_engine_urls_that_are_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPINE_APP_URL", "http://read-api:8002")
    monkeypatch.setenv("SPINE_ENGINE_URL", "http://engine:8001")
    monkeypatch.setenv("SPINE_AGENT_HTTP_TOKEN", "secret")  # nothing else of this process is forwarded
    assert mcp_server_env() == {"SPINE_APP_URL": "http://read-api:8002", "SPINE_ENGINE_URL": "http://engine:8001"}


@pytest.mark.unit
def test_an_unset_url_is_not_forwarded_so_the_server_keeps_its_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SPINE_APP_URL", raising=False)
    monkeypatch.setenv("SPINE_ENGINE_URL", "http://engine:8001")
    assert mcp_server_env() == {"SPINE_ENGINE_URL": "http://engine:8001"}
    monkeypatch.delenv("SPINE_ENGINE_URL")
    assert mcp_server_env() == {}
