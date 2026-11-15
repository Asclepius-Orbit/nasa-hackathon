"""Exercise demo startup without running Docker or touching the real app/.env."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[3] / "app"
PWSH = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.unit
@pytest.mark.parametrize("skip,pull_exit", [(False, 0), (False, 1), (True, 0)])
def test_startup_continues_after_model_preparation(tmp_path, skip, pull_exit):
    if not PWSH:
        pytest.skip("PowerShell is unavailable")
    app = tmp_path / "app"
    app.mkdir()
    shutil.copy(APP / "start-demo.ps1", app)
    (app / ".env").write_text(
        "SPINE_MQTT_SIM_PASSWORD=test\nSPINE_APP_UI_TOKEN=test\n"
        "SPINE_AGENT_HTTP_TOKEN=test\nSPINE_APP_BASELINE_REQUIRED=1\n"
    )
    (app / "mosquitto" / "auth").mkdir(parents=True)
    (app / "mosquitto" / "auth" / "passwd").write_text("x")
    (app / "mosquitto" / "auth" / "acl").write_text("x")
    harness = tmp_path / "run.ps1"
    harness.write_text(
        "function docker {\n"
        "  Add-Content -LiteralPath $env:DEMO_TEST_LOG -Value ($args -join ' ')\n"
        "  if ($args -contains 'ollama-pull') { $global:LASTEXITCODE = [int]$env:DEMO_TEST_PULL_EXIT }\n"
        "  else { $global:LASTEXITCODE = 0 }\n"
        "}\n"
        "function curl.exe {}\n"
        "if ($env:DEMO_TEST_SKIP -eq '1') { & $env:DEMO_TEST_SCRIPT -NoUi -SkipModelPull }\n"
        "else { & $env:DEMO_TEST_SCRIPT -NoUi }\n"
    )
    env = os.environ.copy()
    env.update(DEMO_TEST_LOG=str(tmp_path / "calls"), DEMO_TEST_PULL_EXIT=str(pull_exit),
               DEMO_TEST_SKIP=str(int(skip)), DEMO_TEST_SCRIPT=str(app / "start-demo.ps1"),
               SPINE_APP_TELEMETRY_SOURCE="simulator", SPINE_GATEWAY_DEMO="0")
    result = subprocess.run([PWSH, "-NoProfile", "-File", str(harness)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    pulls = [i for i, call in enumerate(calls) if "ollama-pull" in call]
    up = next(i for i, call in enumerate(calls) if "up -d --build" in call)
    assert bool(pulls) is not skip
    if pulls:
        assert pulls[0] < up
    if pull_exit:
        assert "could not be prepared" in result.stdout


@pytest.mark.unit
def test_pull_prepares_active_model_and_extra_models(tmp_path):
    git = shutil.which("git")
    git_bash = Path(git).resolve().parents[1] / "bin" / "bash.exe" if git and os.name == "nt" else None
    bash = str(git_bash) if git_bash and git_bash.exists() else shutil.which("bash")
    if not bash:
        pytest.skip("bash is unavailable")
    compose = (APP / "docker-compose.yml").read_text()
    service = compose.split("  ollama-pull:\n", 1)[1].split("    depends_on:", 1)[0]
    command = service.split("      - |\n", 1)[1].split("    profiles:", 1)[0]
    command = "\n".join(line[8:] for line in command.splitlines()).replace("$$", "$")
    fake = tmp_path / "ollama"
    fake.write_text('#!/bin/sh\n[ "$1" = show ] && exit 1\nprintf "%s\\n" "$2" >> "$DEMO_TEST_LOG"\n')
    fake.chmod(0o755)
    env = os.environ.copy()
    shell_tmp = "/" + tmp_path.drive[0].lower() + tmp_path.as_posix()[2:] if os.name == "nt" else str(tmp_path)
    env.update(PATH=str(tmp_path) + os.pathsep + env["PATH"], DEMO_TEST_LOG=shell_tmp + "/models",
               SPINE_AGENT_MODEL="custom:1", SPINE_AGENT_LLM_MODELS="extra:2")
    result = subprocess.run([bash, "-c", command], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "models").read_text().splitlines() == ["custom:1", "extra:2"]
