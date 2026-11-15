# Starts the demo stack (sim + llm) and the desktop UI with the tokens fixed in app/.env.
# Usage (from anywhere): powershell -File app/start-demo.ps1 [-NoUi] [-SkipModelPull]
#   -SkipModelPull: do not check or download the Ollama model before starting (offline, model already cached).
#   -NoSeed: do not populate AST-01/AST-02 with ~60 days of fictitious history (the seed runs by itself on every start and
#     only fills astronauts that have no data yet).
# app/.env and app/mosquitto/auth/{passwd,acl} are generated on first run (or when incomplete) with app/mosquitto/setup.sh,
#   run through Git Bash on Windows; this needs docker running.
# SPINE_APP_TELEMETRY_SOURCE (env or app/.env) picks the single telemetry producer: simulator (default) | device.
#   simulator -> profile sim; device -> profile hardware (gateway + USB, Linux), or with SPINE_GATEWAY_DEMO=1
#   profile gateway-demo (serial emulator, no board). SPINE_GATEWAY_DEMO=1 with simulator is rejected.
param([switch]$NoUi, [switch]$SkipModelPull, [switch]$NoSeed)

$ErrorActionPreference = 'Stop'
$app = $PSScriptRoot
$root = Split-Path $app -Parent

function Get-Setting([string]$name, [string]$default) {
    $v = [Environment]::GetEnvironmentVariable($name)
    if (-not $v -and (Test-Path "$app/.env")) {
        $line = Get-Content "$app/.env" | Where-Object { $_ -match "^$name=" } | Select-Object -Last 1
        if ($line) { $v = ($line -replace "^$name=", '').Trim().Trim('"') }
    }
    if ($v) { return $v.Trim() } else { return $default }
}

function Test-NvidiaGpu {
    try {
        return [bool]@(Get-CimInstance Win32_VideoController -ErrorAction Stop |
            Where-Object { $_.PNPDeviceID -match '(?i)VEN_10DE' }).Count
    } catch { return $false }
}

function Show-NvidiaSetupGuide {
    Write-Host 'To check NVIDIA GPU access on Windows:'
    Write-Host '  1. Install an up-to-date NVIDIA Windows driver with WSL 2 support: https://www.nvidia.com/Download/index.aspx'
    Write-Host '  2. Run wsl --update in an administrator PowerShell and restart Windows if requested.'
    Write-Host '  3. In Docker Desktop Settings > General enable "Use the WSL 2 based engine", then apply and restart Docker Desktop.'
    Write-Host '  4. Follow https://docs.docker.com/desktop/features/gpu to verify GPU access, then run this script again.'
}

$source = Get-Setting 'SPINE_APP_TELEMETRY_SOURCE' 'simulator'
if ($source -notin 'simulator', 'device') {
    Write-Error "SPINE_APP_TELEMETRY_SOURCE='$source' is invalid; allowed values: simulator, device"
    exit 1
}
$demo = (Get-Setting 'SPINE_GATEWAY_DEMO' '0') -eq '1'
if ($demo -and $source -ne 'device') {
    Write-Error 'SPINE_GATEWAY_DEMO=1 requires SPINE_APP_TELEMETRY_SOURCE=device (never run simulator and gateway together)'
    exit 1
}
$producer = if ($source -eq 'simulator') { 'sim' } elseif ($demo) { 'gateway-demo' } else { 'hardware' }
$env:SPINE_APP_TELEMETRY_SOURCE = $source
$compose = @('compose', '-f', "$app/docker-compose.yml", '--profile', $producer, '--profile', 'llm')

function Get-EnvValue([string]$name) {
    $line = Get-Content "$app/.env" | Where-Object { $_ -match "^$name=" } | Select-Object -Last 1
    if (-not $line) { throw "$name missing in app/.env" }
    return ($line -replace "^$name=", '').Trim('"')
}

function Test-NonEmptyFile([string]$path) {
    return (Test-Path -LiteralPath $path -PathType Leaf) -and ((Get-Item -LiteralPath $path).Length -gt 0)
}

# True when app/.env, the broker password file or the ACL is missing or incomplete (setup.sh keeps existing passwords).
function Test-MqttSetupNeeded {
    $envFile = Join-Path $app '.env'
    if (-not (Test-NonEmptyFile $envFile)) { return $true }
    if (-not (Select-String -LiteralPath $envFile -Pattern '^SPINE_MQTT_SIM_PASSWORD=.+' -Quiet)) { return $true }
    foreach ($name in 'passwd', 'acl') {
        if (-not (Test-NonEmptyFile (Join-Path $app "mosquitto/auth/$name"))) { return $true }
    }
    return $false
}

# Git Bash on Windows (bash on PATH may be the WSL launcher in System32); bash elsewhere.
function Get-SetupBash {
    if ($env:OS -ne 'Windows_NT') {
        $cmd = Get-Command bash -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        throw 'bash is required to run app/mosquitto/setup.sh but was not found on PATH.'
    }
    $candidates = @()
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $execPath = (& $git.Source --exec-path 2>$null | Select-Object -First 1)
        if ($execPath) {
            # <git root>/(mingw64|ucrt64)/libexec/git-core -> <git root>/bin/bash.exe
            $gitRoot = Split-Path (Split-Path (Split-Path $execPath -Parent) -Parent) -Parent
            $candidates += Join-Path $gitRoot 'bin\bash.exe'
        }
    }
    foreach ($base in $env:ProgramFiles, ${env:ProgramFiles(x86)}, "$env:LOCALAPPDATA\Programs") {
        if ($base) { $candidates += Join-Path $base 'Git\bin\bash.exe' }
    }
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c -PathType Leaf) { return $c }
    }
    throw 'Git Bash was not found (bash.exe on PATH may be WSL, which cannot run app/mosquitto/setup.sh). Install Git for Windows (https://git-scm.com/download/win) and rerun.'
}

if (Test-MqttSetupNeeded) {
    $bash = Get-SetupBash
    Write-Host 'Generating the MQTT credentials (app/.env, app/mosquitto/auth)...'
    & $bash (Join-Path $app 'mosquitto/setup.sh')
    if ($LASTEXITCODE -ne 0) {
        throw "app/mosquitto/setup.sh failed (exit code $LASTEXITCODE); fix the error above and rerun. Docker must be running."
    }
    if (Test-MqttSetupNeeded) { throw 'app/mosquitto/setup.sh finished but app/.env or app/mosquitto/auth is still incomplete.' }
}

# First run on a machine: random tokens, kept in app/.env (git-ignored, never shared).
foreach ($name in 'SPINE_APP_UI_TOKEN', 'SPINE_AGENT_HTTP_TOKEN') {
    if (-not (Select-String -Path "$app/.env" -Pattern "^$name=.+" -Quiet)) {
        $bytes = New-Object byte[] 24
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        Add-Content -Path "$app/.env" -Value "$name=$(($bytes | ForEach-Object { $_.ToString('x2') }) -join '')"
    }
}
if (-not (Select-String -Path "$app/.env" -Pattern '^SPINE_APP_BASELINE_REQUIRED=' -Quiet)) {
    Add-Content -Path "$app/.env" -Value 'SPINE_APP_BASELINE_REQUIRED=1'
}

# Compose reads app/.env by itself (SPINE_APP_UI_TOKEN, SPINE_AGENT_HTTP_TOKEN); the UI needs the same values.
$env:ASCLEPIUS_BACKEND_TOKEN = Get-EnvValue 'SPINE_APP_UI_TOKEN'
$env:ASCLEPIUS_AGENT_TOKEN = Get-EnvValue 'SPINE_AGENT_HTTP_TOKEN'

# Never change the device allocation of an existing server while it may be serving a chat.
$existingOllama = docker @compose ps -q ollama
$preserveOllama = $LASTEXITCODE -eq 0 -and [bool]$existingOllama
$gpuSelected = $false
if ($preserveOllama) {
    Write-Host 'Ollama is already running; preserving it. Stop the demo before selecting hardware again.'
} else {
    # A one-off GPU-enabled server uses an ephemeral loopback port and the same model cache.
    # It is removed after measuring, without touching any existing Ollama process.
    $gpuCompose = @('compose', '-f', "$app/docker-compose.yml", '-f', "$app/docker-compose.nvidia.yml", '--profile', 'llm')
    $probe = $null
    try {
        # Capture native stderr without letting PowerShell 5.1 turn it into a terminating error.
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $probeOutput = @(docker @gpuCompose run -d --rm --no-deps -p '127.0.0.1::11434' ollama 2>&1)
            $probeExit = $LASTEXITCODE
        } finally { $ErrorActionPreference = $previousPreference }
        if ($probeExit -ne 0 -or -not $probeOutput) {
            $detail = ($probeOutput | ForEach-Object { "$_" }) -join ' '
            if ($detail) { Write-Warning "GPU container could not start: $detail" }
            if ((Test-NvidiaGpu) -and $detail -match '(?i)(could not select device driver.*gpu|unknown.*nvidia.*runtime|nvidia-container-cli|nvml error|driver error)') {
                Write-Warning 'NVIDIA GPU detected, but Docker could not enable it. CPU will be used for this run.'
                Show-NvidiaSetupGuide
            }
            throw 'GPU container unavailable'
        }
        $probe = $probeOutput[-1]
        $port = docker port $probe '11434/tcp'
        if ($LASTEXITCODE -ne 0 -or $port -notmatch ':(\d+)$') { throw 'GPU probe port unavailable' }
        $base = "http://127.0.0.1:$($Matches[1])"
        $model = Get-Setting 'SPINE_AGENT_MODEL' 'qwen3.5:4b'
        $ready = $false
        for ($i = 0; $i -lt 30; $i++) {
            try { $null = Invoke-RestMethod -Uri "$base/api/tags" -TimeoutSec 2; $ready = $true; break } catch { Start-Sleep -Seconds 1 }
        }
        if (-not $ready) { throw 'GPU probe did not start' }
        if (-not $SkipModelPull) {
            $null = docker exec $probe ollama show $model 2>$null
            if ($LASTEXITCODE -ne 0) {
                $null = docker exec $probe ollama pull $model
                if ($LASTEXITCODE -ne 0) { throw 'Model unavailable' }
            }
        }
        $choice = & "$app/select-ollama-hardware.ps1" -Model $model -BaseUrl $base
        $gpuSelected = $choice -eq 'GPU'
        if ($choice -eq 'GPU_UNAVAILABLE') {
            Write-Warning 'Ollama did not use GPU memory; possible causes include unsupported GPU, insufficient VRAM or Docker GPU setup. Using CPU.'
            if (Test-NvidiaGpu) { Show-NvidiaSetupGuide }
        } elseif ($choice -eq 'BENCHMARK_FAILED') {
            Write-Warning 'Ollama benchmark failed; using CPU.'
        } else {
            Write-Host "Ollama benchmark selected $(if ($gpuSelected) { 'GPU' } else { 'CPU' })."
        }
    } catch {
        Write-Warning 'GPU detection or benchmark failed; using CPU.'
    } finally {
        if ($probe) { $null = docker stop $probe }
    }
    if ($gpuSelected) { $compose = $gpuCompose + @('--profile', $producer) }
}

# The models live in the ollama-models volume. ollama-pull downloads only the ones missing from it (nothing, and no
# network, when they are cached), so this is a no-op after the first run. A failed pull (offline, first run) only warns:
# the stack still starts and the chat answers with the deterministic fallback until the model is there.
if (-not $SkipModelPull) {
    Write-Host 'Checking the local model (the first run downloads about 3.3 GB)...'
    if ($preserveOllama) { docker @compose --profile llm-prepare run --rm --no-deps ollama-pull }
    else { docker @compose --profile llm-prepare run --rm ollama-pull }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'The local model could not be prepared; the chat will not work until it is downloaded (see the agent README).'
    }
}

# Demo data: complete profiles and ~60 days of history for AST-01 and AST-02, built with the real engine. Idempotent (an
# astronaut that already has data is skipped), so it runs on every start. It runs before the backend so nothing else writes
# the database meanwhile. A failure only warns: the app starts, just without the populated history.
if (-not $NoSeed) {
    Write-Host 'Seeding the demo data (skipped for astronauts that already have data)...'
    docker @compose --profile seed run --rm --build seed
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'The demo data could not be seeded; the app starts with empty profiles. Rerun this script or see the seed service in app/docker-compose.yml.'
    }
}

if ($preserveOllama) {
    # Start missing dependencies in Compose order (and wait for their health checks),
    # then update the active services without asking Compose to recreate Ollama.
    docker @compose up -d --no-recreate
    if ($LASTEXITCODE -ne 0) { throw 'docker compose dependency startup failed' }
    $services = @(docker @compose config --services | Where-Object { $_ -ne 'ollama' })
    if ($LASTEXITCODE -ne 0 -or $services.Count -eq 0) { throw 'Could not enumerate demo services' }
    docker @compose up -d --build --no-deps @services
} else { docker @compose up -d --build }
if ($LASTEXITCODE -ne 0 -and $gpuSelected) {
    Write-Warning 'GPU startup failed; retrying with CPU.'
    $compose = @('compose', '-f', "$app/docker-compose.yml", '--profile', $producer, '--profile', 'llm')
    docker @compose up -d --build
}
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }

docker @compose ps
curl.exe -s http://127.0.0.1:8000/health
Write-Host ''

if (-not $NoUi) { pnpm -C "$root/app/ui" start }
