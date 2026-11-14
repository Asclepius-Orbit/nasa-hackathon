# Starts the demo stack (sim + llm) and the desktop UI with the tokens fixed in app/.env.
# Usage (from anywhere): powershell -File app/start-demo.ps1 [-NoUi]
param([switch]$NoUi)

$ErrorActionPreference = 'Stop'
$app = $PSScriptRoot
$root = Split-Path $app -Parent
$compose = @('compose', '-f', "$app/docker-compose.yml", '--profile', 'sim', '--profile', 'llm')

function Get-EnvValue([string]$name) {
    $line = Get-Content "$app/.env" | Where-Object { $_ -match "^$name=" } | Select-Object -Last 1
    if (-not $line) { throw "$name missing in app/.env" }
    return ($line -replace "^$name=", '').Trim('"')
}

if (-not (Test-Path "$app/.env") -or -not (Select-String -Path "$app/.env" -Pattern '^SPINE_MQTT_SIM_PASSWORD=.+' -Quiet)) {
    bash "$app/mosquitto/setup.sh"
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

docker @compose up -d --build
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }

docker @compose ps
curl.exe -s http://127.0.0.1:8000/health
Write-Host ''

if (-not $NoUi) { pnpm -C "$root/app/ui" start }
