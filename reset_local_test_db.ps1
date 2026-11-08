[CmdletBinding(SupportsShouldProcess, ConfirmImpact = "High")]
param()

$ErrorActionPreference = "Stop"
$container = "fireguard-postgis"

if (-not (docker ps --format "{{.Names}}" | Select-String -SimpleMatch $container -Quiet)) {
    throw "Local test container '$container' is not running. This script does not target a Pi or remote database."
}

if ($PSCmdlet.ShouldProcess("local Docker database '$container'", "delete all local test readings, nodes, and fire points")) {
    docker exec $container psql -v ON_ERROR_STOP=1 -U fireapp -d fire -c "TRUNCATE readings, fire_points, nodes RESTART IDENTITY;"
}
