import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from spine_agent.fallback import McpStatusSource
from spine_agent.ports import StatusSource
from spine_agent.settings import AgentSettings

ASSESSMENT_ID = "a55e55e0-0000-4000-8000-0000000000a1"


def _settings(command: str = sys.executable, args: list[str] | None = None) -> AgentSettings:
    return AgentSettings(mcp_command=command, mcp_args=args if args is not None else ["-m", "spine_mcp_server", "--dataset", "examples"])


@pytest.mark.integration
@pytest.mark.anyio
async def test_current_status_of_amber_astronaut_comes_from_the_real_server():
    source: StatusSource = McpStatusSource(_settings())
    status = await source.current_status("AST-01")
    assert status is not None
    assert status["astronaut_id"] == "AST-01"
    assert status["latest_assessment"]["level"] == "amber"
    assert status["latest_assessment"]["assessment_id"] == ASSESSMENT_ID


@pytest.mark.integration
@pytest.mark.anyio
async def test_unknown_astronaut_is_none():
    assert await McpStatusSource(_settings()).current_status("AST-99") is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_explanation_of_the_status_assessment():
    explanation = await McpStatusSource(_settings()).explanation(ASSESSMENT_ID)
    assert explanation is not None
    assert explanation["assessment"]["assessment_id"] == ASSESSMENT_ID


@pytest.mark.integration
@pytest.mark.anyio
async def test_unknown_assessment_is_none():
    assert await McpStatusSource(_settings()).explanation("00000000-0000-4000-8000-000000000000") is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_missing_server_executable_is_none_not_an_exception(tmp_path: Path):
    source = McpStatusSource(_settings(command=str(tmp_path / "no-such-server")))
    assert await source.current_status("AST-01") is None
    assert await source.explanation(ASSESSMENT_ID) is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_server_that_exits_immediately_is_none():
    source = McpStatusSource(_settings(args=["-c", "pass"]), timeout_s=10)
    assert await source.current_status("AST-01") is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_hung_server_times_out_to_none():
    source = McpStatusSource(_settings(args=["-c", "import time; time.sleep(30)"]), timeout_s=1)
    assert await source.current_status("AST-01") is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_no_subprocess_is_left_running(tmp_path: Path, pid_wrapper_args: Callable[[Path], list[str]], pid_alive: Callable[[int], bool]):
    pid_file = tmp_path / "server.pid"
    source = McpStatusSource(_settings(args=pid_wrapper_args(pid_file)))
    assert await source.current_status("AST-01") is not None
    assert not pid_alive(int(pid_file.read_text()))
