"""Exercise demo startup without running Docker or touching the real app/.env."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[3] / "app"
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def _write_broker_auth(app):
    auth = app / "mosquitto" / "auth"
    auth.mkdir(parents=True, exist_ok=True)
    (auth / "passwd").write_text("x")
    (auth / "acl").write_text("x")


@pytest.mark.unit
@pytest.mark.parametrize("gpu,error,expected_guidance", [
    (True, "could not select device driver with capabilities: [[gpu]]", True),
    (False, "could not select device driver with capabilities: [[gpu]]", False),
    (True, "failed to pull image: network unavailable", False),
])
def test_gpu_setup_guidance_only_for_nvidia_runtime_failure(tmp_path, gpu, error, expected_guidance):
    if not PWSH:
        pytest.skip("PowerShell is unavailable")
    app = tmp_path / "app"
    app.mkdir()
    shutil.copy(APP / "start-demo.ps1", app)
    (app / ".env").write_text("SPINE_MQTT_SIM_PASSWORD=x\nSPINE_APP_UI_TOKEN=x\nSPINE_AGENT_HTTP_TOKEN=x\n")
    _write_broker_auth(app)
    harness = tmp_path / "run.ps1"
    harness.write_text(
        "function Get-CimInstance { if ($env:TEST_GPU -eq '1') { @{ PNPDeviceID='PCI\\VEN_10DE&DEV_1234' } } }\n"
        "function docker {\n"
        " Add-Content $env:TEST_LOG ($args -join ' ')\n"
        " $global:LASTEXITCODE = 0\n"
        " if (($args -contains 'run') -and ($args -contains '-d')) {\n"
        "   $global:LASTEXITCODE = 1; Write-Output $env:TEST_ERROR\n"
        " }\n"
        "}\n"
        "function curl.exe {}\n"
        "& $env:TEST_SCRIPT -NoUi -SkipModelPull\n"
    )
    env = os.environ.copy()
    env.update(TEST_SCRIPT=str(app / "start-demo.ps1"), TEST_LOG=str(tmp_path / "calls"),
               TEST_GPU=str(int(gpu)), TEST_ERROR=error, SPINE_APP_TELEMETRY_SOURCE="simulator",
               SPINE_GATEWAY_DEMO="0")
    result = subprocess.run([PWSH, "-NoProfile", "-File", str(harness)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "using CPU" in result.stdout
    assert ("https://docs.docker.com/desktop/features/gpu" in result.stdout) is expected_guidance
    if expected_guidance:
        assert "wsl --update" in result.stdout
        assert "WSL 2" in result.stdout
    assert any("up -d --build" in line for line in (tmp_path / "calls").read_text().splitlines())


@pytest.mark.unit
@pytest.mark.parametrize("cpu,gpu,vram,expected", [(100, 30, 100, "GPU"), (30, 100, 100, "CPU"), (100, 30, 0, "GPU_UNAVAILABLE"), (30, 30, 100, "CPU")])
def test_hardware_selection_uses_measured_latency_and_requires_gpu_memory(tmp_path, cpu, gpu, vram, expected):
    if not PWSH:
        pytest.skip("PowerShell is unavailable")
    script = APP / "select-ollama-hardware.ps1"
    harness = tmp_path / "select.ps1"
    harness.write_text(
        "function Invoke-RestMethod {\n"
        "  param($Uri, $Method, $ContentType, $Body, $TimeoutSec)\n"
        "  if ($Uri -like '*/api/ps') { return @{ models = @(@{ name='qwen3.5:4b'; size_vram=[int]$env:TEST_VRAM }) } }\n"
        "  $request = $Body | ConvertFrom-Json\n"
        "  if ($request.options.num_gpu -eq 0) { $ms = [int]$env:TEST_CPU } else { $ms = [int]$env:TEST_GPU }\n"
        "  return @{ prompt_eval_duration=($ms * 1000000); eval_duration=($ms * 1000000); eval_count=16 }\n"
        "}\n"
        "& $env:TEST_SCRIPT -Model 'qwen3.5:4b'\n"
    )
    env = os.environ.copy()
    env.update(TEST_SCRIPT=str(script), TEST_CPU=str(cpu), TEST_GPU=str(gpu), TEST_VRAM=str(vram))
    result = subprocess.run([PWSH, "-NoProfile", "-File", str(harness)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == expected


@pytest.mark.unit
def test_hardware_selection_falls_back_if_ollama_fails(tmp_path):
    if not PWSH:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "select.ps1"
    harness.write_text("function Invoke-RestMethod { throw 'unavailable' }\n& $env:TEST_SCRIPT -Model 'qwen3.5:4b'\n")
    env = os.environ.copy()
    env["TEST_SCRIPT"] = str(APP / "select-ollama-hardware.ps1")
    result = subprocess.run([PWSH, "-NoProfile", "-File", str(harness)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "BENCHMARK_FAILED"


@pytest.mark.unit
def test_startup_explains_gpu_not_used_by_ollama(tmp_path):
    if not PWSH:
        pytest.skip("PowerShell is unavailable")
    app = tmp_path / "app"
    app.mkdir()
    for name in ("start-demo.ps1", "select-ollama-hardware.ps1"):
        shutil.copy(APP / name, app)
    (app / ".env").write_text("SPINE_MQTT_SIM_PASSWORD=x\nSPINE_APP_UI_TOKEN=x\nSPINE_AGENT_HTTP_TOKEN=x\n")
    _write_broker_auth(app)
    harness = tmp_path / "run.ps1"
    harness.write_text(
        "function Get-CimInstance { @{ PNPDeviceID='PCI\\VEN_10DE&DEV_1234' } }\n"
        "function docker {\n"
        " Add-Content $env:TEST_LOG ($args -join ' '); $global:LASTEXITCODE=0\n"
        " if ($args -contains 'run') { 'probe-id' }\n"
        " if ($args[0] -eq 'port') { '127.0.0.1:12345' }\n"
        "}\n"
        "function Invoke-RestMethod {\n"
        " param($Uri, $Method, $ContentType, $Body, $TimeoutSec)\n"
        " if ($Uri -like '*/api/tags') { return @{} }\n"
        " if ($Uri -like '*/api/ps') { return @{ models=@(@{ name='qwen3.5:4b'; size_vram=0 }) } }\n"
        " return @{ prompt_eval_duration=100; eval_duration=100; eval_count=16 }\n"
        "}\n"
        "function curl.exe {}\n"
        "& $env:TEST_SCRIPT -NoUi -SkipModelPull\n"
    )
    env = os.environ.copy()
    env.update(TEST_SCRIPT=str(app / "start-demo.ps1"), TEST_LOG=str(tmp_path / "calls"),
               SPINE_APP_TELEMETRY_SOURCE="simulator", SPINE_GATEWAY_DEMO="0")
    result = subprocess.run([PWSH, "-NoProfile", "-File", str(harness)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "did not use GPU memory" in result.stdout
    assert "VRAM" in result.stdout
    assert "https://docs.docker.com/desktop/features/gpu" in result.stdout
    calls = (tmp_path / "calls").read_text().splitlines()
    assert any("up -d --build" in call and "nvidia" not in call for call in calls)


@pytest.mark.unit
def test_running_ollama_is_not_restarted_for_hardware_detection(tmp_path):
    if not PWSH:
        pytest.skip("PowerShell is unavailable")
    app = tmp_path / "app"
    app.mkdir()
    shutil.copy(APP / "start-demo.ps1", app)
    (app / ".env").write_text("SPINE_MQTT_SIM_PASSWORD=x\nSPINE_APP_UI_TOKEN=x\nSPINE_AGENT_HTTP_TOKEN=x\n")
    _write_broker_auth(app)
    harness = tmp_path / "run.ps1"
    harness.write_text(
        "function docker {\n"
        " Add-Content $env:DEMO_TEST_LOG ($args -join ' ')\n"
        " $global:LASTEXITCODE = 0\n"
        " if (($args -contains 'ps') -and ($args -contains '-q')) { 'existing-id' }\n"
        " if (($args -contains 'config') -and ($args -contains '--services')) { 'ollama'; 'backend'; 'agent' }\n"
        "}\n"
        "function curl.exe {}\n"
        "& $env:DEMO_TEST_SCRIPT -NoUi -SkipModelPull\n"
    )
    env = os.environ.copy()
    env.update(DEMO_TEST_LOG=str(tmp_path / "calls"), DEMO_TEST_SCRIPT=str(app / "start-demo.ps1"),
               SPINE_APP_TELEMETRY_SOURCE="simulator", SPINE_GATEWAY_DEMO="0")
    result = subprocess.run([PWSH, "-NoProfile", "-File", str(harness)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "already running" in result.stdout
    calls = (tmp_path / "calls").read_text().splitlines()
    assert any("up -d --no-recreate" in call for call in calls)
    assert any("up -d --build --no-deps" in call for call in calls)
    updated = next(call for call in calls if "up -d --build --no-deps" in call)
    assert updated.split("--no-deps", 1)[1].split() == ["backend", "agent"]


@pytest.mark.unit
def test_hardware_benchmark_uses_distinct_prompts(tmp_path):
    if not PWSH:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "select.ps1"
    harness.write_text(
        "function Invoke-RestMethod {\n"
        " param($Uri, $Method, $ContentType, $Body, $TimeoutSec)\n"
        " if ($Uri -like '*/api/ps') { return @{ models=@(@{ name='qwen3.5:4b'; size_vram=100 }) } }\n"
        " $r=$Body | ConvertFrom-Json; Add-Content $env:TEST_LOG $r.prompt\n"
        " return @{ prompt_eval_duration=100; eval_duration=100; eval_count=16 }\n"
        "}\n"
        "& $env:TEST_SCRIPT -Model 'qwen3.5:4b'\n"
    )
    env = os.environ.copy()
    env.update(TEST_SCRIPT=str(APP / "select-ollama-hardware.ps1"), TEST_LOG=str(tmp_path / "prompts"))
    result = subprocess.run([PWSH, "-NoProfile", "-File", str(harness)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    prompts = (tmp_path / "prompts").read_text().splitlines()
    assert len(prompts) == 4
    assert len(set(prompts)) == 4


@pytest.mark.unit
@pytest.mark.parametrize("gpu_start_fails", [False, True])
def test_startup_benchmarks_isolated_server_and_recovers_from_gpu_failure(tmp_path, gpu_start_fails):
    if not PWSH:
        pytest.skip("PowerShell is unavailable")
    app = tmp_path / "app"
    app.mkdir()
    for name in ("start-demo.ps1", "select-ollama-hardware.ps1"):
        shutil.copy(APP / name, app)
    (app / ".env").write_text("SPINE_MQTT_SIM_PASSWORD=x\nSPINE_APP_UI_TOKEN=x\nSPINE_AGENT_HTTP_TOKEN=x\n")
    _write_broker_auth(app)
    harness = tmp_path / "run.ps1"
    harness.write_text(
        "function docker {\n"
        " Add-Content $env:DEMO_TEST_LOG ($args -join ' ')\n"
        " $global:LASTEXITCODE = 0\n"
        " if ($args -contains 'run') { 'probe-id' }\n"
        " if ($args[0] -eq 'port') { '127.0.0.1:12345' }\n"
        " if (($args -contains 'up') -and (($args -join ' ') -like '*nvidia*') -and $env:TEST_FAIL -eq '1') { $global:LASTEXITCODE=1 }\n"
        "}\n"
        "function Invoke-RestMethod {\n"
        " param($Uri, $Method, $ContentType, $Body, $TimeoutSec)\n"
        " if ($Uri -like '*/api/tags') { return @{} }\n"
        " if ($Uri -like '*/api/ps') { return @{ models=@(@{ name='qwen3.5:4b'; size_vram=100 }) } }\n"
        " $r=$Body | ConvertFrom-Json; $n=if ($r.options.num_gpu -eq 0) { 100 } else { 10 }\n"
        " return @{ prompt_eval_duration=$n; eval_duration=$n; eval_count=16 }\n"
        "}\n"
        "function curl.exe {}\n"
        "& $env:DEMO_TEST_SCRIPT -NoUi -SkipModelPull\n"
    )
    env = os.environ.copy()
    env.update(DEMO_TEST_LOG=str(tmp_path / "calls"), DEMO_TEST_SCRIPT=str(app / "start-demo.ps1"),
               TEST_FAIL=str(int(gpu_start_fails)), SPINE_APP_TELEMETRY_SOURCE="simulator", SPINE_GATEWAY_DEMO="0")
    result = subprocess.run([PWSH, "-NoProfile", "-File", str(harness)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    assert any("run -d --rm --no-deps" in call for call in calls)
    assert any("stop probe-id" in call for call in calls)
    ups = [call for call in calls if "up -d --build" in call]
    assert "nvidia" in ups[0]
    assert len(ups) == 1 + int(gpu_start_fails)
    if gpu_start_fails:
        assert "nvidia" not in ups[-1]


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


def _run_startup(tmp_path, extra_args, seed_exit):
    app = tmp_path / "app"
    app.mkdir()
    shutil.copy(APP / "start-demo.ps1", app)
    (app / ".env").write_text("SPINE_MQTT_SIM_PASSWORD=x\nSPINE_APP_UI_TOKEN=x\nSPINE_AGENT_HTTP_TOKEN=x\n")
    _write_broker_auth(app)
    harness = tmp_path / "run.ps1"
    harness.write_text(
        "function docker {\n"
        " Add-Content $env:TEST_LOG ($args -join ' ')\n"
        " $global:LASTEXITCODE = 0\n"
        " if ($args -contains 'seed') { $global:LASTEXITCODE = [int]$env:TEST_SEED_EXIT }\n"
        "}\n"
        "function curl.exe {}\n"
        "& $env:TEST_SCRIPT -NoUi -SkipModelPull " + extra_args + "\n"
    )
    env = os.environ.copy()
    env.update(TEST_SCRIPT=str(app / "start-demo.ps1"), TEST_LOG=str(tmp_path / "calls"),
               TEST_SEED_EXIT=str(seed_exit), SPINE_APP_TELEMETRY_SOURCE="simulator", SPINE_GATEWAY_DEMO="0")
    result = subprocess.run([PWSH, "-NoProfile", "-File", str(harness)], env=env, capture_output=True, text=True)
    calls = (tmp_path / "calls").read_text().splitlines()
    return result, calls


@pytest.mark.unit
def test_demo_seed_runs_by_itself_before_the_stack_starts(tmp_path):
    if not PWSH:
        pytest.skip("PowerShell is unavailable")
    result, calls = _run_startup(tmp_path, "", 0)
    assert result.returncode == 0, result.stderr
    seed = [i for i, line in enumerate(calls) if "--profile seed run --rm --build seed" in line]
    up = [i for i, line in enumerate(calls) if "up -d" in line]
    assert len(seed) == 1 and up and seed[0] < up[0]


@pytest.mark.unit
def test_no_seed_switch_skips_the_demo_seed(tmp_path):
    if not PWSH:
        pytest.skip("PowerShell is unavailable")
    result, calls = _run_startup(tmp_path, "-NoSeed", 0)
    assert result.returncode == 0, result.stderr
    assert not any(" seed" in line for line in calls)


@pytest.mark.unit
def test_failed_demo_seed_only_warns_and_the_stack_still_starts(tmp_path):
    if not PWSH:
        pytest.skip("PowerShell is unavailable")
    result, calls = _run_startup(tmp_path, "", 1)
    assert result.returncode == 0, result.stderr
    assert "could not be seeded" in (result.stdout + result.stderr)
    assert any("up -d" in line for line in calls)
