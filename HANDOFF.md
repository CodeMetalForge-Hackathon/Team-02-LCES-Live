# Handoff: LCES Live (Team-2), Sun Nov 8 2026, 06:50 EST

For the next Claude session. Merges two handoffs: Yugesh's "after session 5" (his laptop, ~04:30, was pasted into
`docs/handoff.md`) and Vivek's sessions (Pi bring-up, demo-dashboard plan, tickets 01–06, 09, 10, 13–15). Where they disagree,
this file has the current state. Update it at the end of each session.
**Deadline: submissions Sun Nov 8, 12:00 EST**, then live demos.

## 1. Where things stand

- **Repo:** branch `main`, last commit `c305724` ("dashboard connected", yugesharma 04:25). Vivek's laptop:
  `/home/vickinez_077/Projects/Hackathon-CM/Team-2` (the parent `Hackathon-CM/` is a separate empty git repo; ignore
  it). Yugesh's laptop: `/home/yugesh/Documents/codemetalHackathon/Team-2`. `git fetch` from Vivek's Claude shell fails
  (no GitHub credentials); the user commits, pulls and pushes.
- **Pipeline runs end to end, on the laptop and on the Pi:** sensor nodes / simulator → Mosquitto → `engine.py`
  (FireGrid fire spread + scipy routing over `data/yarnell/graph.npz`) → `engine/state` → `ws_server.py` (serves
  `dashboard/dist` + `/ws` on :8000) → browser. `ingest.py` → PostGIS.
- **Uncommitted on Vivek's laptop (Nov 8, ~05:00–06:50):**
  - Plan: `docs/specs/demo-dashboard/spec.md` + `issues/01..12-*.md`; 2-line "Revised Nov 8" note in
    `docs/specs/escape-margin/spec.md`.
  - **Tickets 01–06, 09, 10, 13, 14, 15 implemented** (status `resolved` in the ticket files, see section 3):
    `engine.py`, `margin.py`, `config.py`, `ws_server.py`, `test_margin.py`, `dashboard/src/{App.tsx,types.ts,styles.css}`,
    `README.md`, `docs/LCES_Design.md` (§0 rows, §5 steps 1/8 + story, **new §5.7**, §5.6 gap table, §8.4, §8.6, §9 items 8–10), `docs/GLOSSARY.md` ("Active route"), `docs/specs/demo-dashboard/spec.md` (06:00 revision, ending decided 06:05), ticket 11 note, `docs/pi-setup.md` (§5 ready/Start).
  - `docs/handoff.md` (Yugesh's handoff, untracked; superseded by this file), `dashboard/node_modules`,
    `dashboard/dist` (gitignored).
- **Tests:** `uv run python test_margin.py` → **30 checks pass** on the laptop. The Pi got the 28-check version
  (tickets 01–05 + 10) at ~06:15 and passed; round 2 (06:40) is **not on the Pi yet**. `uv run python build_graph.py --test` passes. `npm run build` passes.
- **Browser check:** headless Chrome works on Vivek's laptop (`google-chrome --headless=new --screenshot`); a scratch
  server that runs the real engine and serves `dist` + `/ws` is in the session scratchpad (`fake_ws.py`, not in the
  repo). Ready screen and the WNW story at 150 s looked right. Hover popups weren't checked (can't hover headless).
- **Not done yet:** round 2 not deployed to the Pi; nothing committed.
- **Design doc:** `docs/LCES_Design.md` §5.7 is the write-up of everything from this morning (phases, active route
  rule, wind smoothing, crew walk, `/api/cmd`, new `engine/state` fields, wind-window table, dashboard layout, local
  dev). Keep it in sync when things change.

## 1b. Developing locally (no Pi deploy per change)

Vivek found deploying to the Pi for every change tedious. Two setups (both written up in design doc §5.7):
- **All local, simulator:** terminal 1 `./run_demo.sh --dir-after 295 --wind-shift-after 45 --turn-s 150` (Docker
  Mosquitto + engine `--allow-sim` + `ws_server` + simulator; built dashboard at http://127.0.0.1:8000; press Start
  within 45 s, the simulator's wind turns 45 s after the script starts, not after Start). Ctrl+C stops all; rerun after
  `.py` changes. Terminal 2 `cd dashboard && npm run dev` → http://localhost:5173, reloads on save.
- **Dashboard against the Pi's live data:** `cd dashboard && VITE_WS_URL=ws://10.42.0.1:8000/ws
  VITE_API_URL=http://10.42.0.1:8000/api/cmd npm run dev` (10.42.1.1 on FireNet). Start/Reset then drive the Pi.
- Docker image: the laptop has `eclipse-mosquitto:2.0.20`; `run_demo.sh` asks for `:2` (pulls if online). Offline:
  `docker tag eclipse-mosquitto:2.0.20 eclipse-mosquitto:2`.
- **"Still the old layout"** (happened ~06:45): the laptop was serving the new build (checked: `curl -s
  localhost:8000/ | grep -o 'assets/index-[^"]*'` matches `dashboard/dist/assets`). Cause is the browser: a tab on the
  Pi's address (Pi still has round 1) or cache → use http://127.0.0.1:8000 and Ctrl+Shift+R. New layout = one wide
  Active-route tile, "Wind and fire spread" panel, compass on the map. Vivek hasn't confirmed which it was yet. Teammates edit the same files (`engine.py`/`margin.py`: Yugesh; `App.tsx`: Glenn):
  tell them before they push.

## 2. Team and code

| Who | Owns |
|---|---|
| Vivek (the user in this session) | Pi base station, N3 node, margin engine (with Yugesh) |
| yugesharma (Yugesh) | spec/ADRs/glossary, `build_graph.py`, scenario data, most engine work (FireGrid 8c, closures + reroute loop 8d, `areas`), dashboard integration, `load_static.py`, uv lock, `run_demo.sh`, `docs/pi-setup.md` |
| gladvinglenn (Glenn) | `ws_server.py`, `ingest.py`, `schema.sql`, original dashboard (`dashboard/`, React + MapLibre), `deploy_pi.ps1` |

Read first: `CLAUDE.md`, `docs/GLOSSARY.md` (use its terms; "perimeter" = final 2013 outline only, say "fire area"),
`docs/adr/0001`, `0002`, `docs/specs/escape-margin/spec.md` (demo story near the top, "Testing Decisions"),
`docs/specs/demo-dashboard/spec.md` (the new story), `docs/LCES_Design.md` §2.4, §5 (steps 1–10), §5.6 ("Update Nov 8"
blocks), §8.4–8.6, §9 track 8 (§0 status table is stale: "14 checks", "v1"). Running it: `README.md`,
`./run_demo.sh` (laptop), `docs/pi-setup.md` (Pi).

Code map: `margin.py` (`FireGrid`, `graph_routes`, `fire_areas`, `evaluate`), `engine.py` (`MarginEngine.compute`,
`on_cmd`), `build_graph.py` (`route`, `path_to`), `load_static.py`, `simulator.py`, `dashboard/src/App.tsx`,
`ws_server.py`.

Tracker convention (`docs/agents/issue-tracker.md`): specs in `docs/specs/<feature>/spec.md`, tickets
`issues/NN-slug.md` with `Status:` (`ready-for-agent`, `needs-triage`, `claimed`, `resolved`, …), `Type:`,
`Blocked by:`; comments under `## Comments`. (Yugesh's handoff said "no tickets"; the team has since adopted these
local markdown tickets. Still no GitHub Issues.)

## 3. The plan (docs/specs/demo-dashboard/) and progress

| # | Ticket | Area | State |
|---|---|---|---|
| 01 | Engine `ready` phase; `{"cmd":"start"}` starts the 16:20 clock (FireGrid created then); reset → ready | engine | **done** |
| 02 | Active route = fastest non-red at Start (the black), then changes only when red → "REROUTED"; `replan_ts` | engine | **done** (ranch-first dropped 06:00) |
| 04 | `POST /api/cmd` (start/reset/fire allow-list) → `engine/cmd` | ws_server | **done** |
| 05 | Start/Reset buttons, scenario clock, "Ready" overlay (now at the top of the map), "Ignite at N2" | dashboard | **done** |
| 03 | `t_crew_min` / `t_fire_min` per route point, aligned with `path` | engine | **done** |
| 10 | Route hover tooltip (crew vs fire time, margin here); tightest-point ring | dashboard | **done** (hover not checked yet) |
| 09 | Sensor node hover popups; remove "Sensor feed" panel; "direction unknown" | dashboard | **done** |
| 06 | Label Boulder Springs Ranch + 100 m circle, "2013 deployment site"; remove `sites.geojson` layer | dashboard | **done** |
| 07 | `render_map_layers.py` → `terrain.png`, `fuel.png`, `map_osm.geojson` | data | next |
| 08 | Coloured backdrop, fuel toggle, roads/buildings | dashboard | |
| 11 | Re-plan animation (banner, pulse from crew, route drawn via `line-gradient`/`line-progress`); now mainly at Start | dashboard | **done** (08:45) |
| 12 | Optional aerial imagery | data | needs-triage |
| 13 | Wind smoothing: moving average (20 s, direction as a vector), rounded 5° / 0.5 m/s up | engine | **done** |
| 14 | Simulated crew walks the **active route** after Start (decided over the 2013 descent line) | engine | **done** |
| 15 | Round 2: one Active-route tile (banner + other tiles removed), big wind/fire-spread arrow panel instead of the sensor feed, map compass, legend panel, sensor chip in header | dashboard | **done** |

**Vivek's round-2 list (Nov 8 ~06:20) and how each item was done:** 1. route colours: kept green/amber/red (his
choice). 2–3. "Margin engine running" and the other top boxes removed; only the Active escape route tile stays.
4. compass top-right of the map. 5. "Boulder Springs Ranch" label (+ safety zone circle; ticket 06). 6. legend panel
for every line/fill/dot. 7. sensor feed hidden; readings on node hover (ticket 09). 8. wind panel with a big arrow
pointing where the fire is pushed. 9. engine wind smoothing (ticket 13). 10. crew walks the active route (ticket 14).

Implementation notes for what's done:
- Engine phases: `ready` (no FireGrid, margins `null`, `areas` null, fire inputs ignored) → `start` → `running`
  (`started_ts`, `replan_ts` = now, FireGrid created) → `reset` → `ready` (also clears events and route-status memory).
  A second `start` is ignored. New state fields: `phase`, `started_ts`, `scenario_start` ("16:20"), `replan_ts`,
  per route `t_crew_min` / `t_fire_min` (~1.5 KB of a ~60 KB message; `areas` is ~52 KB).
- Crew walk: each running step moves the crew `dt` s along the active route (projected onto the polyline, then
  `seg_s`), then routes are re-planned from there. ~1.1 m/s; ~330 m in 5 min; real `crew/+/pos` stops it.
- Wind smoothing: `MarginEngine._smooth_wind` / `smoothed_wind`; `engine/state.weather.wind_from` is the smoothed value.
- Active route: picked once at the first running step after Start (fastest non-red = the black), then changes only
  by a reroute. Vivek decided at 06:00 to drop `INITIAL_ROUTE="ranch"` ("the black at start is logical").
- `/api/cmd`: 400 for other cmds, 503 if MQTT isn't connected; CORS `*` so the Vite dev server can call it.
  `VITE_API_URL` overrides the dashboard's URL.

**Demo story (decided Nov 8 ~05:30, ending decided ~06:05 by Vivek):** ready screen → Start (16:20, crew's route
back to the black drawn: the fastest route) → fan on N1 from SSW (both green) → swing fan to the **WNW (~290–300°,
≥ 4 m/s)** → **ranch route red** (where the 2013 crew went), the crew's route to the black stays green with a leave-by
time → stop. No reroute in the main story. NNE ("no safe route") only for Q&A; E–ENE fan → "REROUTED: black -> ranch"
also only for Q&A. Unplug N3 last. Reset between runs. Ticket 11's animation now matters mainly at Start.

## 4. Engine behaviour and findings

- **Closures + reroute loop** (Yugesh, §9 8d, §5 steps 3–7) in `margin.graph_routes(g, crew_xy, fire, now)`, called
  every recompute while running (no route cache): crew Dijkstra without closures → close vertices that are burning,
  within 4 × flame of burning, or reached by the fire (÷ 1.5) before the crew → Dijkstra again → repeat until no new
  closures (cap 10). `black` target = every black cell by now (grows). A zone with no path left keeps its fire-blind
  path, so it shows red and the all-red rule raises "NO SAFE ROUTE"; routes never vanish from `engine/state`.
- **`areas`** in `engine/state`: black, burning, front_5min (0–5 min), front_10min (5–10 min) as lon/lat
  MultiPolygons (`rasterio.features.shapes`), ~30 ms.
- **Wind windows** (engine step on the 2013 data from the descent point; wind 200° at 2.5 m/s, then the outflow;
  margins 1/3/5 min after). Black cut, ranch fine (→ reroute black → ranch with the crew starting on the black):
  60–120° at 4 m/s, 45–90° at 7.5 m/s, 30–90° at 10 m/s; 135–255° both green. Ranch cut, black fine:
  - 4 m/s: 270–300° → ranch red, black green (+18 … +6); 315° and beyond → both red.
  - 7.5 m/s: 300° → ranch red, black green (+33); 315° → black +12; 330° black amber; 345–20° both red.
  - Yugesh's sweep (7.5 m/s, +10°/min from 200°): green to 280°, ranch red 290°, black amber 330°, red 340°.
  - Probe: `import test_margin as T`, `eng = T.graph_engine()` (sends `start` at t=0), then
    `T.feed(eng, t, wind_ms=..., wind_dir=...)` and `eng.compute(t)`. Run from the repo root with
    `PYTHONPATH=. uv run python <script>`.
- **Any stale sensor node** (not only N1) → wind direction unknown → head-fire rate in every direction + worst case
  (10 m/s, 38 °C, 8 %). With N1 missing the whole map burns within minutes (seen on the Pi). All routes red with
  "stale N1,N2,N3" = no readings yet.
- FireGrid keeps history: burned ground stays burned until reset.
- **Margin ≠ ETA.** Keep them apart in answers. Amber with a big margin (e.g. 112 min) = the "dropped > 30 %" rule
  (§5.1), expected while the wind turns.
- Wind direction convention: "from". Test helper `feed()` feeds N1 wind; N2/N3 send −1.
- **Performance:** laptop 40–60 ms per recompute, ~270 ms on a weather change; Pi 4 ms idle, ~43 ms with the map
  burning.
- **Dashboard map:** only `hillshade.png` is drawn (saturation −0.75, opacity 0.68); no GeoTIFF reaches the browser.
  The yellow dot next to the crew is the "Observation deck (ridge)" from `sites.geojson` (the lunch spot isn't drawn).
  MapLibre text labels need a glyph server, which isn't there offline: use HTML markers/popups. MapLibre v6 needs
  `setWorkerUrl` to the bundled worker (already done) or no GeoJSON layer draws.
- Places: ranch −112.770913, 34.219524 (radius 100 m); descent point (crew start) −112.781828, 34.221598; nodes in
  `data/yarnell/nodes.json`.

## 5. The Pi (deployed Nov 8 ~04:55; follows docs/pi-setup.md)

- SSH `thunder@10.42.0.1` (cable) or `thunder@10.42.1.1` (FireNet). **Password-only:** Claude can't SSH in; Vivek runs
  commands (VS Code Remote-SSH terminal) and pastes output. Offer `ssh-copy-id` if direct access would help.
- `~/fire` is **not a git clone** (a local `git init`, untracked files). Code gets there by `scp` from the laptop repo
  (pi-setup.md step 2 Option B); dashboard by `scp -r dashboard/dist thunder@10.42.0.1:~/fire/dashboard/`. Backup:
  `~/fire-backup-HHMM.tgz`. (`deploy_pi.ps1` needs `pwsh`, which the laptops don't have; a bash port was offered.)
- Done: `uv sync --locked` (pyrothermel 0.1.4 built from source in 55 s; it removed opencv/pyserial/shapely);
  24 checks pass; `.env` fixed (`MQTT_HOST=127.0.0.1`, `DATABASE_URL=...@127.0.0.1:5432/fire`); role `fireapp` +
  database `fire`, postgis + postgis_raster, `schema.sql`, `load_static.py` (all layers, `nodes: 3`); services via
  nohup (`logs/live.pid`, `logs/engine.pid`); dashboard at http://10.42.1.1:8000 (or .0.1 on cable); FireNet hotspot
  (`sudo nmcli device wifi hotspot ifname wlan0 ssid FireNet password firenet123 band bg`); **N2 and N3 live**.
- **To deploy (round 2 included)** (laptop, from `Team-2`; same files as before):
  ```bash
  scp engine.py margin.py config.py ws_server.py test_margin.py thunder@10.42.0.1:~/fire/
  scp -r dashboard/dist thunder@10.42.0.1:~/fire/dashboard/
  ```
  then on the Pi: `cd ~/fire && UV_OFFLINE=1 uv run python test_margin.py | tail -1` (expect 30), restart services
  (below), `curl -X POST localhost:8000/api/cmd -H 'content-type: application/json' -d '{"cmd":"bogus"}'` (expect
  400), reload the page: "Ready" overlay → Start → clock from 16:20:00, black appears, active route = the black; `logs/engine.log` shows
  `running | ...`.
- Restart services (pi-setup.md step 4):
  ```bash
  cd ~/fire && kill $(cat logs/*.pid) 2>/dev/null
  nohup env UV_OFFLINE=1 bash run_live.sh > logs/live.log 2>&1 < /dev/null & echo $! > logs/live.pid
  nohup env UV_OFFLINE=1 uv run python engine.py > logs/engine.log 2>&1 < /dev/null & echo $! > logs/engine.pid
  ```
- Commands (after the new code): Start `mosquitto_pub -h localhost -t engine/cmd -m '{"cmd":"start"}'` (or the
  dashboard button), Reset `... -m '{"cmd":"reset"}'`, backup spot fire `... -m '{"cmd":"fire","node":"N2"}'`.
- Services don't survive a reboot (nohup); FireNet has no internet (install anything needing internet first).

## 6. Still open outside the plan (rough priority)

1. **N1 (La Crosse wind via SDR)** not started; it stays on a laptop on FireNet:
   `rtl_433 -f 433.92M -R 73 -F json | MQTT_HOST=10.42.1.1 SENSOR_ID=<id> python3 sdr/wind_bridge.py` (sensor id
   changes when batteries come out). Reset once 3/3 live.
2. **Fan test:** `wind_dir` must be "from" (fan from the south → ~180; if ~0, set `DIR_OFFSET` 180 in the bridge);
   measure N1's transmit interval (stale rule 30 s); note the `wind_ms` the fan gives (needs ≥ ~4 m/s for the reroute
   window).
3. **Hot-sensor rule** (§1.4, design §5.6): ≥ 45 °C, or +8 °C within 30 s → one fire point at that node, first
   ignition time kept. Not implemented, and no node sends `"flame": 1`, so the hair-dryer beat does nothing.
   ~15 lines in `engine.on_wx` + one check. Note: fire points are now ignored before Start.
4. **Exclude hot / fire nodes from temp/RH** in `engine.weather` (today the hottest reading sets moisture for the
   whole grid). Offered to Yugesh with item 3; unanswered. Full local weather (nearest-node zones): little gain now,
   ~3× ROS table cost; later.
5. ~~Crew simulator~~: done in the engine (ticket 14), along the active route. Not done: `simulator.py` publishing
   `crew/<id>/pos` (not needed now).
5b. Ready screen doesn't draw the black (no FireGrid before Start); the spec's ready screen mentions "the black behind
   them". Could draw `black0` from the graph in ready if wanted.
5c. Black route flickers amber at large margins (the "dropped > 30 %" rule) while the wind turns; seen in the WNW story
   run. Consider ignoring drops when the margin stays ≥ 10 min.
6. **Other v1→v2 gaps** (§5.6 table): wind from N1 only × `WIND_SCALE`; "only N1 stale → direction unknown"; worst
   case 13 m/s / 40 °C / 8 %; aging at 1 missed heartbeat; `fires` with `ignition_ts` (now `age_s`); reroute alert on
   a path change to the same zone (§5 step 8).
7. **Cleanup** (undecided): delete `export_routes()` / `--routes` / `routes.geojson` / `travel_factor` path (v1
   placeholder checks still use `load_routes`); move the 440 MB DEM to `data/raw/`.
8. Record a backup demo video during a good rehearsal.
- Also open: veg table calibration with the operator mentor, `WIND_SCALE` / worst-case calibration vs the slides.
- **Pitch rules:** never claim the system would have prevented the tragedy ("targets the gaps the investigation
  identified"); "Rate of spread from the Fire Lab's Behave engine, via pyrothermel"; crew model walks the 2013 descent
  line in 20.7 min (SAIT ~20).

## 7. Gotchas

- `build_graph.py` has no `--help`: any unrecognised flag **rebuilds `graph.npz`** (float-noise diff only;
  `git checkout -- data/yarnell/graph.npz` to undo). `--test` = self-checks, `--routes` = v1 export.
- After any dashboard change: `cd dashboard && npm run build` (Node 22 via nvm on Vivek's laptop), copy `dist` to the
  Pi, reload the page. After engine/ws_server changes: `scp` the `.py` files and restart the services.
- uv: Python `==3.13.*` (the Pi's Debian 13 Python), `default-groups = ["engine"]`, build tools for pyrothermel
  pinned; use `uv sync --locked`. pyrothermel is sdist-only on ARM (needs `build-essential cmake`).
- Simulator messages carry `_simulated`; engine (`--allow-sim`) and `ws_server` (`ALLOW_SIMULATOR_DATA`) drop them
  unless allowed; `run_live.sh` refuses simulator data on purpose (`ALLOW_SIMULATOR_DATA=false` on the Pi).
  `run_demo.sh` (laptop): Docker Mosquitto + engine `--allow-sim` + `ws_server` + simulator; with the new code press
  Start on the dashboard. Simulator for the demo story (pi-setup step 6): `simulator.py --dir-after 295
  --wind-shift-after 45 --turn-s 150` cuts the ranch (no reroute); for the black → ranch reroute (Q&A) the simulator can't
  do it as is: it always turns clockwise, so 200° → 90° sweeps through the N (both red). Use `--dir-before 135
  --dir-after 90`, or a real fan turned counter-clockwise from the SSW through the SE. Default `--dir-after 20` = the old NNE story.
- `load_dotenv` doesn't override exported env. Yugesh's laptop `.env` points at the Pi (`MQTT_HOST=10.42.1.1`);
  `run_demo.sh` overrides it.
- PostGIS static tables (`load_static.py`) are for SQL / QGIS only; nothing live reads them. `postgis_raster` needs
  `CREATE EXTENSION` once as superuser.
- Yugesh's laptop has another project's Docker PostGIS (`backend-db-1`); don't touch it.
- Starlette's `TestClient` needs `httpx2`, which isn't installed (installing it was blocked); test `ws_server` by
  calling the handler with a stub MQTT client, or curl against uvicorn.

## 8. Working with the team

- **Vivek:** software engineer, new to hardware: step-by-step commands, say which machine each runs on (laptop
  terminal vs the Pi's VS Code terminal) and what output to expect. He pastes output and screenshots back; read them
  carefully. Explain the dashboard in plain terms; he found the map hard to read at first.
- **Yugesh's preferences** (team conventions): docs under `docs/`; the user commits/pushes; minimal code, assert-only
  checks, no pytest; go by the spec; keep the demo simple and close to the 2013 truth; answers outcome first.

## 9. First steps for the next session

1. `git status` / `git log --oneline -5` in `Team-2`: did teammates push, did Vivek commit?
2. Deploy round 2 to the Pi (section 5 "To deploy"; expect 30 checks) and check in the browser: Ready → Start → crew
   starts walking, wind arrow follows the fan smoothly, hover a node and a route. Watch `(… ms)` in `logs/engine.log`
   (wind steps re-run the fire grid; if > ~800 ms, set `WIND_DIR_STEP_DEG = 10` in `config.py`).
3. Continue with ticket 07 + 08 (coloured backdrop), then 11. For each: set `Status: claimed`, implement, `test_margin.py` /
   `npm run build`, mark `resolved` with a comment, give the scp + restart steps.
