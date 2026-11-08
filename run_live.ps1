[CmdletBinding()]
param(
    [string]$MqttHost = "10.42.1.1",
    [int]$MqttPort = 1883,
    [int]$WebPort = 8000
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"

if (-not (Test-Path $envFile)) {
    throw "Missing .env. Copy .env.example to .env, then set MQTT_HOST and DATABASE_URL."
}

$settings = Get-Content $envFile | Where-Object { $_ -match "=" }
$configuredHost = (($settings | Where-Object { $_ -match "^MQTT_HOST=" }) -replace "^MQTT_HOST=", "").Trim()
$allowSimulator = (($settings | Where-Object { $_ -match "^ALLOW_SIMULATOR_DATA=" }) -replace "^ALLOW_SIMULATOR_DATA=", "").Trim().ToLower()

if ($configuredHost -ne $MqttHost) {
    throw "MQTT_HOST in .env is '$configuredHost', expected '$MqttHost'. Update .env before launching live services."
}
if ($allowSimulator -in @("1", "true", "yes")) {
    throw "ALLOW_SIMULATOR_DATA must be false for a live-data run."
}
if (-not (Test-NetConnection $MqttHost -Port $MqttPort -InformationLevel Quiet)) {
    throw "Cannot reach MQTT broker at ${MqttHost}:${MqttPort}. Connect to FireNet and confirm the Pi broker is running."
}

$quotedRoot = "'" + $projectRoot.Replace("'", "''") + "'"
Start-Process powershell.exe -WorkingDirectory $projectRoot -ArgumentList "-NoExit", "-Command", "Set-Location $quotedRoot; uv run python ingest.py"
Start-Process powershell.exe -WorkingDirectory $projectRoot -ArgumentList "-NoExit", "-Command", "Set-Location $quotedRoot; uv run uvicorn ws_server:app --host 127.0.0.1 --port $WebPort"
Start-Process "http://127.0.0.1:$WebPort/api/sensors/latest"

Write-Host "Started live ingestion and WebSocket server. Simulator messages are ignored."
