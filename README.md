# LCES Live

Wildfire safe escape route: MQTT sensor data feeds a live dashboard and a
PostgreSQL/PostGIS history store.

## Services

```text
MQTT broker → ws_server.py → WebSocket dashboard
            → ingest.py    → PostgreSQL/PostGIS
```

`ws_server.py` forwards every `sensors/#`, `engine/#`, and `crew/#` MQTT
message to connected dashboards at `/ws`. `POST /api/cmd` (`{"cmd": "start" | "reset" | "fire", ...}`) publishes
the body to `engine/cmd` (the dashboard's Start / Reset buttons; no auth, closed FireNet only). `ingest.py` saves weather heartbeats
from `sensors/+/wx` into the `readings` table. `engine.py` (margin engine)
subscribes to the sensor, fire, crew and `engine/cmd` topics and publishes
`engine/state` every second.

## Margin engine (v1)

Fire spread from pyrothermel, crew times on the escape routes in
`routes.geojson`, margins, reroute and alerts. Until the real
`data/yarnell/nodes.json` and `data/yarnell/routes.geojson` exist it uses the
placeholders in `data/yarnell/placeholder/` and reports
`"placeholder_data": true`. Details, message format and the gaps to the
planned v2 engine: `docs/LCES_Design.md` §5.6.

```bash
uv sync --locked                              # once, needs internet; exact versions from uv.lock (Python 3.13, builds pyrothermel)
uv run --group engine python test_margin.py   # 14 checks
uv run --group engine python engine.py        # live; MQTT_HOST from .env
```

Whole pipeline on one machine (Docker broker + engine + bridge + simulator), dashboard at
http://127.0.0.1:8000: `./run_demo.sh` (simulator args pass through, e.g.
`./run_demo.sh --wind-shift-after 45 --turn-s 150`; the wind turns from 200° to 20°, the 2013 outflow).

Offline demo story against a local broker, by hand:

```bash
uv run --group engine python engine.py --allow-sim
uv run python simulator.py --fire-after 20 --wind-shift-after 60 --silent-after 90
```

## Run on the Pi

Setup, PostGIS data load, starting the services and opening the dashboard: `docs/pi-setup.md`.

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

Build the dashboard once while the Pi has internet. The built files are served
by `ws_server.py` on port 8000, so Vite does not run during the demo:

```bash
sudo apt update
sudo apt install -y nodejs npm
cd /home/thunder/fire/dashboard
npm install
npm run build
```

Start both live services together (press `Ctrl+C` to stop both):

```bash
cd /home/thunder/fire
bash run_live.sh
```

If the Pi only has FireNet and cannot reach the internet, deploy from a laptop
that already has this repository and dependencies with the PowerShell helper:

```powershell
.\deploy_pi.ps1
```

It builds the dashboard locally, copies the application and data to the Pi over
SSH, preserves the Pi-only `.env`, applies `schema.sql`, and starts the live
services. The Pi must have had `uv sync --locked` run while it had internet
access; the helper deliberately uses `uv sync --offline` on FireNet.

Or, for separate logs/terminals, run `uv run python ingest.py` and `uv run
uvicorn ws_server:app --host 0.0.0.0 --port 8000` individually.

Open `http://10.42.1.1:8000` while on FireNet. The dashboard connects to
`ws://10.42.1.1:8000/ws` automatically.
