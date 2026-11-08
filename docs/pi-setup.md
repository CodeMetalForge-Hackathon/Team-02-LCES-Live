# Running LCES Live on the Pi

For the teammate with SSH access to the Pi. Covers: one-time setup, copying the project over, loading the
map data into PostGIS, starting everything, and opening the dashboard.

Pi facts (design doc §8.1–8.2): user `thunder`, project at `/home/thunder/fire`, Raspberry Pi OS (Debian 13,
64-bit), Mosquitto and PostgreSQL 17 + PostGIS already installed.

| How the laptop is connected | Pi address |
|---|---|
| FireNet hotspot (SSID `FireNet`, password `firenet123`) | `10.42.1.1` |
| Ethernet cable | `10.42.0.1` |

Examples below use `10.42.1.1`; swap in `10.42.0.1` on the cable.

What runs on the Pi:

```text
sensor nodes ──MQTT──> Mosquitto (1883) ──> engine.py ──engine/state──┐
                                        ├─> ingest.py ──> PostGIS      │
                                        └─> ws_server.py (8000) <──────┘ ──WebSocket──> dashboard in the browser
```

`ws_server.py` serves the dashboard page and the WebSocket on the same port, so the only URL anyone needs is
`http://<pi>:8000`.

---

## 1. One-time setup (Pi needs internet for step 1a)

The FireNet hotspot takes the Pi off the internet, so do step 1a **before** starting the hotspot (or with the
Pi on Ethernet + internet).

**a. Python dependencies + system packages** (do it first: pyrothermel compiles from source on the Pi, never
tried there yet).

The dependency files have to be on the Pi first. From the laptop, in the repo root:

```bash
ssh thunder@10.42.1.1 "mkdir -p ~/fire"
scp pyproject.toml uv.lock .python-version thunder@10.42.1.1:~/fire/
```

Then on the Pi:

```bash
ssh thunder@10.42.1.1
sudo apt install -y build-essential cmake      # C++ compiler for pyrothermel
cd ~/fire
uv sync --locked
```

`--locked` installs exactly the versions in `uv.lock` (Python 3.13, the Pi's own, Debian 13 ships it; set in
`.python-version` and `pyproject.toml`) and fails if the lock is out of date instead of changing it. Every package has a ready-made
ARM wheel except pyrothermel, whose build tools are pinned too (`build-constraint-dependencies` in
`pyproject.toml`). The built pyrothermel stays in uv's cache, so later offline syncs reuse it.

If this fails on pyrothermel, stop and tell the team: the engine can't run without it (fallback: design doc §2.7).

**b. Database and app user** (skip the parts that already exist; check with `sudo -u postgres psql -l`):

```bash
sudo -u postgres psql -c "CREATE ROLE fireapp LOGIN PASSWORD 'pick-a-password'"
sudo -u postgres psql -c "CREATE DATABASE fire OWNER fireapp"
```

**c. PostGIS extensions** (need the database superuser, once per database; harmless to repeat):

```bash
sudo -u postgres psql -d fire -c "CREATE EXTENSION IF NOT EXISTS postgis"
sudo -u postgres psql -d fire -c "CREATE EXTENSION IF NOT EXISTS postgis_raster"
```

**d. `.env` on the Pi** (`~/fire/.env`, never committed, never overwritten by a deploy):

```bash
cd ~/fire
cp .env.example .env     # if .env doesn't exist yet
nano .env
```

It must contain:

```ini
MQTT_HOST=127.0.0.1
MQTT_PORT=1883
ALLOW_SIMULATOR_DATA=false
DATABASE_URL=postgresql://fireapp:<the password from 1b>@127.0.0.1:5432/fire
```

(5432 on the Pi; 5433 is only the local Docker test database on a laptop.)

**e. Mosquitto running and listening on all addresses** (should already be true):

```bash
systemctl is-active mosquitto          # -> active
ss -ltn | grep 1883                     # -> 0.0.0.0:1883
```

---

## 2. Copy the project to the Pi

Run from a laptop with the repo, connected to FireNet or the cable.

### Option A: Windows laptop (one command)

```powershell
.\deploy_pi.ps1                      # or: .\deploy_pi.ps1 -PiHost 10.42.0.1
```

It builds the dashboard, copies code + data, applies the schema, loads the static data into PostGIS (step 3),
starts all services (step 4) and checks they're up. It stops with a message if the Pi `.env` is missing or
unsafe, or if `uv sync` can't run offline (then do step 1a). When it prints "Deployment complete", go to step 5.

### Option B: by hand (Linux / macOS laptop)

```bash
# on the laptop, in the repo root
cd dashboard && npm install && npm run build && cd ..      # creates dashboard/dist
ssh thunder@10.42.1.1 "mkdir -p ~/fire/dashboard ~/fire/logs"
scp config.py engine.py fire_model.py margin.py ingest.py ws_server.py simulator.py build_graph.py \
    test_margin.py load_static.py pyproject.toml uv.lock .python-version schema.sql run_live.sh mosquitto.conf .env.example \
    thunder@10.42.1.1:~/fire/
scp -r data thunder@10.42.1.1:~/fire/
scp -r dashboard/dist thunder@10.42.1.1:~/fire/dashboard/
```

`data/` is ~14 MB. Don't copy `docs/USGS_13_n35w113.tif` (440 MB, not needed).

Then on the Pi, check the code works before starting anything:

```bash
cd ~/fire
UV_OFFLINE=1 uv run python test_margin.py      # -> "24 checks passed (model: pyrothermel)"
```

---

## 3. Load the map data into PostGIS (Option B; Option A does this for you)

```bash
cd ~/fire
set -a; . ./.env; set +a
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f schema.sql          # live tables: nodes, readings, fire_points
UV_OFFLINE=1 uv run python load_static.py                       # static layers
```

Expected output:

```text
raster dem
raster slope
raster aspect
raster fuel
layer perimeter: 1 features
layer sites: 4 features
layer scenario: 11 features
layer osm: 651 features
nodes: 3
```

If it says "PostGIS raster support is off", do step 1c. Rerunning is safe: it replaces what it loaded.

What gets loaded (all in EPSG:32612, metres):

| Table | Content |
|---|---|
| `dem`, `slope`, `aspect`, `fuel` | 10 m rasters of the 5 × 5 km Yarnell patch, in 100 × 100 tiles |
| `static_features` | `layer` = `perimeter` (2013 final perimeter), `sites`, `scenario` (descent line, crew start, ranch, fire areas 16:00–16:50), `osm` (roads, tracks, buildings, water, fences; `props->>'kind'`); `name`, `props` (all GeoJSON properties), `geom` |
| `nodes` | N1–N3 with label and position from `data/yarnell/nodes.json` |

Sanity check (elevation, fuel code and distance to the ranch for each node):

```bash
psql "$DATABASE_URL" -c "
SELECT n.id, round(ST_Value(d.rast, n.geom)::numeric) AS elev_m,
       (SELECT ST_Value(rast, n.geom) FROM fuel WHERE ST_Intersects(rast, n.geom)) AS fuel_code,
       round(ST_Distance(n.geom, r.geom)) AS m_to_ranch
FROM nodes n
JOIN dem d ON ST_Intersects(d.rast, n.geom)
JOIN static_features r ON r.layer = 'scenario' AND r.name = 'Boulder Springs Ranch'
ORDER BY n.id;"
```

Expected: N1 1709 m / 122 / 1082 m, N2 1557 / 122 / 750, N3 1520 / 122 / 311.

The live services don't read these tables (the engine works from `data/yarnell/graph.npz`, the dashboard from
the files and `engine/state`). They are for SQL queries and map tools such as QGIS.

---

## 4. Start the services (Option B; Option A does this for you)

```bash
cd ~/fire && mkdir -p logs
# stop old copies, if any
(test -f logs/live.pid && kill $(cat logs/live.pid)); (test -f logs/engine.pid && kill $(cat logs/engine.pid))
# ingest.py + ws_server.py (dashboard + WebSocket on port 8000)
nohup env UV_OFFLINE=1 bash run_live.sh > logs/live.log 2>&1 < /dev/null & echo $! > logs/live.pid
# margin engine
nohup env UV_OFFLINE=1 uv run python engine.py > logs/engine.log 2>&1 < /dev/null & echo $! > logs/engine.pid
```

Check:

```bash
curl -fsS http://127.0.0.1:8000/api/sensors/latest | head -c 300; echo   # JSON, with "engine/state" in it
tail -n 20 logs/engine.log      # a line per status change: "routes ranch=green(...) black*=green(...) | wind ..."
tail -n 20 logs/live.log        # "MQTT connected"
```

Watch the times at the end of the engine lines, e.g. `(45 ms)`. Recompute takes 40–60 ms on a laptop
(~270 ms when the weather changes). If the Pi shows seconds, tell the team.

Stop everything: `kill $(cat logs/*.pid)`.

### 4b. N1 wind sensor (runs on a laptop, not the Pi)

N2 and N3 publish over Wi-Fi by themselves. N1 (the La Crosse wind sensor) is received by a USB SDR dongle on a
laptop, which publishes to the Pi's broker over FireNet (design doc §1.3). On that laptop, in the repo root:

```bash
rtl_433 -f 433.92M -R 73 -F json | MQTT_HOST=10.42.1.1 SENSOR_ID=<id> python3 sdr/wind_bridge.py
```

Check from the Pi: `mosquitto_sub -h localhost -t 'sensors/N1/wx' -v` (a message per packet received).

---

## 5. Open the dashboard

From any device on FireNet (laptop, phone, tablet): **http://10.42.1.1:8000**
(on the cable: **http://10.42.0.1:8000**; on the Pi's own browser: http://127.0.0.1:8000).

What you should see:

- Top right "Bridge connected"; "Margin engine: Running".
- "N / 3 live" sensors. Until the first sensor reading the engine uses worst-case weather, and the fire clock
  (16:20 in the scenario) starts at that first reading.
- Map: hillshade of Yarnell; the black (dark), burning edge (red), where the fire will be in 5 / 10 min
  (orange / yellow); the two escape routes coloured green / amber / red (the active one thicker); the crew
  (white dot, blue ring); sensor nodes (blue = fresh, yellow = aging, grey = stale); 2013 perimeter (dashed red)
  and 2013 descent line (dotted white).
- Right side: route margins and ETAs, sensor feed, alerts (e.g. "NO SAFE ROUTE"), leave-by time.

The page reconnects on its own if `ws_server.py` restarts. After copying a new dashboard build, reload the page.

---

## 6. Optional: test without the real nodes

`run_live.sh` refuses simulator data on purpose. To try the full story on the Pi without sensors, stop the
services (step 4), then in three terminals:

```bash
cd ~/fire && UV_OFFLINE=1 uv run python engine.py --allow-sim
cd ~/fire && ALLOW_SIMULATOR_DATA=true UV_OFFLINE=1 uv run uvicorn ws_server:app --host 0.0.0.0 --port 8000
cd ~/fire && UV_OFFLINE=1 uv run python simulator.py --wind-shift-after 45 --turn-s 150 --silent-after 100000
```

The wind starts from 200° (SSW, both routes green) and turns to 20° (the NNE outflow): the ranch route goes
red, then the black route, then "NO SAFE ROUTE". Restart the normal services (step 4) afterwards.

On a laptop, `./run_demo.sh` does the same thing with a Docker broker.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Page doesn't load | `ws_server.py` not running (`tail logs/live.log`), or wrong address for how you're connected (table at the top) |
| "Bridge offline" | Same as above; the page retries every 2 s |
| Map shows only the hillshade, no routes or fire | Old dashboard build: rebuild (`npm run build`) on the laptop and copy `dashboard/dist` again |
| "No engine/state received yet" | Engine not running: `tail logs/engine.log` |
| All routes red right after start, "stale N1,N2,N3" | No sensor data yet (worst-case weather). Check the nodes are on FireNet: `mosquitto_sub -h localhost -t 'sensors/#' -v` |
| `load_static.py`: "PostGIS raster support is off" | Step 1c |
| `uv sync --offline` fails | Step 1a with internet |
| `uv sync --locked`: lock out of date | Someone changed `pyproject.toml` without `uv lock`. On a laptop: `uv lock`, commit `uv.lock`, copy it again |
| N1 never shows up | On the N1 laptop (step 4b): on FireNet, `rtl_433` running, right `SENSOR_ID` (changes when the batteries come out), `MQTT_HOST=10.42.1.1` |
| `psql: password authentication failed` | `DATABASE_URL` in `.env` doesn't match the password from step 1b |
