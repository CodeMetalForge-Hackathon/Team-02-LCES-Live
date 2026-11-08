[CmdletBinding()]
param(
    [string]$PiUser = "thunder",
    [string]$PiHost = "10.42.1.1",
    [string]$RemotePath = "/home/thunder/fire",
    [switch]$SkipDependencySync
)

# Offline FireNet deployment: source/data are copied directly from this laptop.
# It never copies or replaces the Pi-only .env file.
$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$target = "$PiUser@$PiHost"

if ($PiUser -notmatch '^[A-Za-z0-9_-]+$' -or $PiHost -notmatch '^[A-Za-z0-9.-]+$' -or $RemotePath -notmatch '^/[A-Za-z0-9_./-]+$') {
    throw "PiUser, PiHost, or RemotePath contains unsupported characters."
}
foreach ($command in "ssh", "scp", "uv", "npm") {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Required command '$command' was not found." }
}
if (-not (Test-Path (Join-Path $projectRoot "dashboard\package.json"))) { throw "Run this script from the repository root." }

Write-Host "Checking Pi SSH connectivity at $PiHost..."
if (-not (Test-NetConnection $PiHost -Port 22 -InformationLevel Quiet)) {
    throw "Cannot reach ${PiHost}:22. Connect this laptop to FireNet, make sure Pi SSH is enabled, then retry."
}

Write-Host "Building dashboard locally (no network download)..."
Push-Location (Join-Path $projectRoot "dashboard")
try { npm run build } finally { Pop-Location }
if (-not (Test-Path (Join-Path $projectRoot "dashboard\dist\index.html"))) { throw "Dashboard build did not create dashboard/dist/index.html." }

Write-Host "Preparing Pi directory..."
& ssh $target "mkdir -p '$RemotePath/dashboard' '$RemotePath/logs'"
if ($LASTEXITCODE -ne 0) { throw "Could not create the deployment directory on the Pi." }

$rootFiles = @(
    "config.py", "engine.py", "fire_model.py", "margin.py", "ingest.py", "ws_server.py",
    "simulator.py", "build_graph.py", "test_margin.py", "pyproject.toml", "uv.lock",
    "schema.sql", "run_live.sh", "mosquitto.conf", ".env.example"
) | ForEach-Object { Join-Path $projectRoot $_ }

Write-Host "Copying application source, dashboard build, and Yarnell data to the Pi..."
& scp @rootFiles "${target}:$RemotePath/"
if ($LASTEXITCODE -ne 0) { throw "Source copy failed." }
& scp -r (Join-Path $projectRoot "data") (Join-Path $projectRoot "dashboard\dist") "${target}:$RemotePath/"
if ($LASTEXITCODE -ne 0) { throw "Data/dashboard copy failed." }

$remoteCheck = "cd '$RemotePath' && test -f .env && grep -q '^MQTT_HOST=127.0.0.1' .env && grep -Eq '^ALLOW_SIMULATOR_DATA=(false|False|FALSE|0|no|NO)$' .env"
& ssh $target $remoteCheck
if ($LASTEXITCODE -ne 0) {
    throw "Pi .env is missing or unsafe. Set MQTT_HOST=127.0.0.1 and ALLOW_SIMULATOR_DATA=false on the Pi, then rerun."
}

if (-not $SkipDependencySync) {
    Write-Host "Syncing Pi Python environment from its local uv cache only..."
    & ssh $target "cd '$RemotePath' && uv sync --offline --group engine"
    if ($LASTEXITCODE -ne 0) {
        throw "Pi dependency sync failed offline. Connect the Pi to internet once and run: cd $RemotePath && uv sync --group engine. Then reconnect FireNet and rerun this script."
    }
}

Write-Host "Applying schema to the Pi database..."
& ssh $target "cd '$RemotePath' && set -a && . ./.env && set +a && psql `"`$DATABASE_URL`" -v ON_ERROR_STOP=1 -f schema.sql"
if ($LASTEXITCODE -ne 0) { throw "Schema application failed; services were not started." }

Write-Host "Starting ingestion, WebSocket/dashboard, and margin engine on the Pi..."
$start = "cd '$RemotePath' && mkdir -p logs && (test -f logs/live.pid && kill `$(cat logs/live.pid) 2>/dev/null || true) && (test -f logs/engine.pid && kill `$(cat logs/engine.pid) 2>/dev/null || true) && (nohup env UV_OFFLINE=1 bash run_live.sh > logs/live.log 2>&1 < /dev/null & echo `$! > logs/live.pid) && (nohup env UV_OFFLINE=1 uv run --group engine python engine.py > logs/engine.log 2>&1 < /dev/null & echo `$! > logs/engine.pid)"
& ssh $target $start
if ($LASTEXITCODE -ne 0) { throw "Could not start services on the Pi." }

Start-Sleep -Seconds 4
Write-Host "Verifying Pi processes and API..."
& ssh $target "cd '$RemotePath' && kill -0 `$(cat logs/live.pid) && kill -0 `$(cat logs/engine.pid) && curl -fsS http://127.0.0.1:8000/api/sensors/latest"
if ($LASTEXITCODE -ne 0) {
    throw "Pi verification failed. Inspect: ssh $target 'cd $RemotePath && tail -n 80 logs/live.log logs/engine.log'"
}

Write-Host "Deployment complete. On FireNet, open http://$PiHost:8000"
