import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pydantic_ai.models
import pytest

# Runs the real MCP server as `python -m spine_mcp_server`, but first writes its PID to the file named by argv[1],
# so a test can check that the subprocess is gone after the client exits.
PID_WRAPPER = (
    "import os, runpy, sys; open(sys.argv[1], 'w').write(str(os.getpid())); "
    "sys.argv = [sys.argv[0], '--dataset', 'examples']; runpy.run_module('spine_mcp_server', run_name='__main__')"
)

# Same, but first writes a marker line to stderr (the server's log channel).
NOISY_WRAPPER = (
    "import runpy, sys; sys.stderr.write('MCP-SERVER-LOG-LINE' + chr(10)); sys.stderr.flush(); "
    "sys.argv = [sys.argv[0], '--dataset', 'examples']; runpy.run_module('spine_mcp_server', run_name='__main__')"
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_real_model_requests() -> Iterator[None]:
    """The gate is offline: any request to a real LLM provider fails the test."""
    previous = pydantic_ai.models.ALLOW_MODEL_REQUESTS
    pydantic_ai.models.ALLOW_MODEL_REQUESTS = False
    try:
        yield
    finally:
        pydantic_ai.models.ALLOW_MODEL_REQUESTS = previous


def _pid_alive(pid: int) -> bool:
    """Whether a process with this PID is still running (no psutil in the workspace)."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT: not signalled yet
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def pid_alive() -> Callable[[int], bool]:
    return _pid_alive


@pytest.fixture
def pid_wrapper_args() -> Callable[[Path], list[str]]:
    """MCP args that launch the real server and record its PID in the given file."""
    return lambda pid_file: ["-c", PID_WRAPPER, str(pid_file)]


@pytest.fixture
def noisy_server_args() -> list[str]:
    """MCP args that launch the real server after it writes the line 'MCP-SERVER-LOG-LINE' to stderr."""
    return ["-c", NOISY_WRAPPER]
