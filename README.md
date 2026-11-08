# LCES Live

Wildfire safe escape route: MQTT sensor data feeds a live dashboard and a
PostgreSQL/PostGIS history store.

## Services

```text
MQTT broker → ws_server.py → WebSocket dashboard
            → ingest.py    → PostgreSQL/PostGIS
```

`ws_server.py` forwards every `sensors/#`, `engine/#`, and `crew/#` MQTT
message to connected dashboards at `/ws`. `ingest.py` saves weather heartbeats
from `sensors/+/wx` into the `readings` table.

## Test real sensor data from a laptop

Connect to FireNet, then copy the example configuration and set the Pi broker:

```powershell
Copy-Item .env.example .env
notepad .env
```

Set `MQTT_HOST=10.42.1.1` and `ALLOW_SIMULATOR_DATA=false`. Keep the database
URL for whichever Postgres instance you are testing against. `simulator.py`
marks its own messages with `_simulated: true`; both services ignore those
messages unless `ALLOW_SIMULATOR_DATA=true` is deliberately set for an offline
test.

Start both live services and open the inspection endpoint in one command:

```powershell
.\run_live.ps1
```

The script checks that the Pi MQTT broker is reachable and opens separate
PowerShell windows for ingestion and the WebSocket server. To start them
manually instead:

```powershell
uv sync
uv run python ingest.py
```

```powershell
uv run uvicorn ws_server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/api/sensors/latest` to see live messages. Query
the database to confirm persistence:

```powershell
docker exec fireguard-postgis psql -U fireapp -d fire -c "SELECT node_id, seq, temp_c, rh, wind_ms, received_at FROM readings ORDER BY received_at DESC LIMIT 10;"
```

`simulator.py` is optional and should not be started while testing real nodes.
To erase existing **local Docker test data** before a live-data run, use the
guarded script below. It targets only the `fireguard-postgis` container, asks
for confirmation, and must never be used against the Pi database.

```powershell
.\reset_local_test_db.ps1
```

## Raspberry Pi deployment

On the Pi, Mosquitto runs locally, so create an uncommitted `.env` with:

```env
MQTT_HOST=127.0.0.1
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=
ALLOW_SIMULATOR_DATA=false
DATABASE_URL=postgresql://fireapp:YOUR_PASSWORD@127.0.0.1:5432/fire
```

After pulling the branch, run:

```bash
cd /home/thunder/fire
uv sync
set -a; . ./.env; set +a
psql "$DATABASE_URL" -f schema.sql
```

Start both live services together (press `Ctrl+C` to stop both):

```bash
cd /home/thunder/fire
bash run_live.sh
```

Or, for separate logs/terminals, run `uv run python ingest.py` and `uv run
uvicorn ws_server:app --host 0.0.0.0 --port 8000` individually.

The dashboard connects to `ws://10.42.1.1:8000/ws` while on FireNet.
