# Starts the demo stack (sim + llm) and the desktop UI with the tokens fixed in app/.env.
# Usage (from anywhere): powershell -File app/start-demo.ps1 [-NoUi] [-SkipModelPull]
#   -SkipModelPull: do not check or download the Ollama model before starting (offline, model already cached).
# app/.env and app/mosquitto/auth/{passwd,acl} are generated on first run (or when incomplete) with app/mosquitto/setup.sh,
#   run through Git Bash on Windows; this needs docker running.
# SPINE_APP_TELEMETRY_SOURCE (env or app/.env) picks the single telemetry producer: simulator (default) | device.
#   simulator -> profile sim; device -> profile hardware (gateway + USB, Linux), or with SPINE_GATEWAY_DEMO=1
#   profile gateway-demo (serial emulator, no board). SPINE_GATEWAY_DEMO=1 with simulator is rejected.
param([switch]$NoUi, [switch]$SkipModelPull)

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

# The models live in the ollama-models volume. ollama-pull downloads only the ones missing from it (nothing, and no
# network, when they are cached), so this is a no-op after the first run. A failed pull (offline, first run) only warns:
# the stack still starts and the chat answers with the deterministic fallback until the model is there.
if (-not $SkipModelPull) {
    Write-Host 'Checking the local model (the first run downloads about 3.3 GB)...'
    docker @compose --profile llm-prepare run --rm ollama-pull
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'The local model could not be prepared; the chat will not work until it is downloaded (see the agent README).'
    }
}

docker @compose up -d --build
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }

docker @compose ps
curl.exe -s http://127.0.0.1:8000/health
Write-Host ''

if (-not $NoUi) { pnpm -C "$root/app/ui" start }
