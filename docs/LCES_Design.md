# LCES Live: Design Document
The single build reference for the team: what each part computes, with which data, how the pieces talk, and current status.
`LCES_Live.md` is the initial pitch and is **not** kept in sync; where it differs, this file wins.
(Merged from the team context doc "FireGuard — Team Context & Status", Nov 7, which this file replaces.)

Core question the system answers, continuously:
**margin = (when fire reaches a point) − (when the crew reaches it)**, taken at the worst point along each escape route.

**Event:** Code Metal Forge 2026 ("Operation Lost and Found"), BASE-X Boston, sponsored by USSOCOM. Build Sat 10:00 → submissions Sun 12:00, then live demos. Judged on mission relevance, a working live demo, deployability.
**Objectives fit:** strong on Map changing conditions and Deliver critical information, good on Detect hazards, weak on Locate people unless we add the crew tag / "can rescuers still reach the trapped hiker safely" angle.
**Pitch wording:** the 2013 Yarnell Hill investigation (19 firefighters died) highlighted gaps in real-time weather, fire and crew-location information; our system targets those gaps. **Do not claim it would have prevented the tragedy.**
**Demo:** sensor nodes spread a couple of metres apart on the floor, each given a **fake GPS position** at a real Yarnell spot (§1.4); the Yarnell map is on a screen. A fan simulates a wind shift, a hair dryer / heat gun (never open flame) simulates a spot fire. Margin collapses, route flips green → red, an alternate route appears, an alert fires.

---

## 0. Status (Sat Nov 7, evening; engine / dashboard rows updated Sun Nov 8, ~06:50)

| Area | Status | Owner | Next step |
|---|---|---|---|
| **Node N1: La Crosse TX145WSDTHv3 via SDR** (temp, RH, wind speed, wind direction) | **Working end to end: readings seen on the Pi broker** (verified as `W1`, renamed `N1`; §1.3) | TBD | Fan test of direction; measure transmit interval; move SDR to the Pi |
| Node N2: XIAO ESP32-S3 + DHT11 (`firmware/weather-node`) (temp, RH) | **Working:** joins FireNet (−35 dBm with antenna), heartbeat to `sensors/N2/wx` every 10 s accepted by the Pi broker (verified Sat Nov 7) | TBD | — |
| Node N3: XIAO ESP32-C5 + DHT11 (`firmware/`, env `node_n3`) (temp, RH) | **Working end to end:** DHT11 read every 3 s (~24 °C, ~48 % RH), heartbeat with real values to `sensors/N3/wx` every 10 s over FireNet, seen on the Pi broker alongside N2 (verified Sat Nov 7, late evening; §8.3.2) | Vivek | — |
| Pi 5 base: OS, packages, Mosquitto | Done and verified | Vivek | — |
| FireNet hotspot on the Pi | **On**; Pi = `10.42.1.1` on FireNet (verified by the wind bridge) | Vivek | Point all ESP32s at `10.42.1.1` |
| Laptop ↔ Pi SSH over Ethernet | Done, VS Code Remote-SSH works | Vivek | Share access with the team |
| Simulator (fake N1–N3) | Running, verified | Vivek | Teammates copy it for local dev |
| PostGIS database + ingest | **Real node data ingested and verified** (Nov 7) | Teammate | Re-apply `schema.sql` (drops the seq constraint) |
| WebSocket server (FastAPI) | **Implemented and verified locally with simulator data** | Teammate | Connect the MapLibre dashboard; verify against real Pi MQTT messages |
| Yarnell map data (`data/fetch_yarnell.py` → `data/yarnell/`) | **Done and cross-checked:** 5×5 km, 10 m grid, EPSG:32612: DEM, slope, aspect, hillshade, LF2025 fuel, 2013 perimeter, OSM, key sites; LFPS download has EVC/EVH/EVT too (§3.1) | — | §3.2: ranch + escape route + wind timeline from the report, routing crop + node GPS positions, travel factor from EVC/EVH, load into PostGIS |
| Dashboard (MapLibre) | **Demo layout (Nov 8, §5.7):** Start / Reset / scenario clock, one Active-route tile, wind + fire-spread arrow, compass, legend, place labels, route and sensor hover popups; served by `ws_server.py` on the Pi (first round deployed ~06:15) | Glenn + Vivek | Coloured terrain / fuel backdrop (demo-dashboard tickets 07–08), route animation (11) |
| Margin engine (`engine.py`) | **v2 working on the 2013 data (Nov 8):** fire spread from the black (§2.4), closures + reroute loop (§5.4), `areas`, ready / Start phases, smoothed wind, simulated crew walk (§5.6, §5.7); 30 checks pass; pyrothermel built on the Pi, first round deployed there ~06:15 | Vivek + Yugesh | Hot-sensor rule, `WIND_SCALE`, N1 fan test (§9) |
| Flame sensor, LoRa link, thermal lookout, crew tag | **Cut (Nov 8)** | — | — |
| Node placement (fake GPS per node, §1.4) | **Decided Nov 8:** N1 on the ridge near the saddle, N2 in the box canyon, N3 between canyon and ranch | TBD | Pick exact lat/lon on the map |

**Vocabulary:** `docs/GLOSSARY.md` is the source for terms (sensor node, cell, escape route, margin, the black, …). Decisions with a real trade-off are in `docs/adr/`.

---

## 1. Pipeline, messages, coordinates

### 1.1 Overview
```
ESP32 nodes (temp, RH) ────────────────────MQTT─┐
La Crosse wind sensor ─433 MHz─ SDR + rtl_433 ──┤  sensors/<node>/wx
   (wind_bridge.py)                             │
Dashboard "ignite" button ──────────────────────┤  sensors/<src>/fire
Scenario simulator (crew walk) ─────────────────┤  crew/<id>/pos
                                                ▼
                               Mosquitto on the Pi (10.42.1.1)
                                                │
                   ingest.py → PostGIS (readings, fire_points)
                                                │
     ┌──────────────────────────────────────────┼──────────────────────────┐
     ▼                                          ▼                          ▼
FIRE MODEL (pyrothermel + scipy)    CREW MODEL (scipy)            STATIC DATA (loaded once)
fire arrival time per cell          crew arrival time per cell    DEM, slope, aspect, fuel,
                                                                  trails, grid, safety zones
     └────────────────────► MARGIN ENGINE ◄─────┘
                 margin, colour, reroute, alerts
                                │  publishes engine/state (MQTT)
                                ▼
         ws_server.py (FastAPI, subscribes sensors/# + engine/#) → WebSocket → MapLibre
```
Every component talks only through Mosquitto, so the simulator and the real nodes are interchangeable. The WebSocket server only subscribes to MQTT.
Recompute on every incoming message **and at least every 5 s** (the fire moves with the clock and the crew walks; target: < 2 s end to end).

### 1.2 MQTT topics and message format
| Topic | Sent by | Content |
|---|---|---|
| `sensors/<node>/wx` | Sensor nodes, wind bridge, simulator | Weather heartbeat (every 10 s for ESP32 nodes; N1 per radio packet, §1.3) |
| `sensors/<src>/fire` | Dashboard "ignite" button (manual fire point) | Fire point at a sensor node's position or an explicit lat/lon. Hot-sensor fire points are created inside the engine (§1.4) |
| `crew/<id>/pos` | Scenario simulator | Crew position (single crew `C1`) |
| `engine/state` | Margin engine | Escape routes, margins, fire polygons, alerts (§5.5; v1 format as implemented: §5.6) |
| `engine/cmd` | Demo operator / dashboard (**v1 engine only**, not in the v2 plan) | `{"cmd":"reset"}` clears fires · `{"cmd":"fire","node":"N2"}` or `{"cmd":"fire","lat":..,"lon":..}` · `{"cmd":"crew","lat":..,"lon":..}` |

JSON; `-1` / `-99` = no data yet. Nodes have no reliable clock, so the Pi adds its own receive time.
```json
{"type":"wx","node":"N1","seq":184,"temp_c":31.2,"rh":18.0,"wind_ms":-1,"wind_dir":-1,"flame":-1,"batt":-1}
{"type":"fire","src":"THERM1","node":"N2","conf":0.9}
{"type":"fire","src":"THERM1","lat":34.2215,"lon":-112.7790,"conf":0.9}
{"type":"crew","id":"C1","lat":34.2214,"lon":-112.7822}
```
- `seq` counts heartbeats per node. `wind_dir` = direction the wind blows **from**, degrees. `wind_ms` = m/s.
- **Demo nodes (3):** `N1` = La Crosse sensor via SDR, sends **temp, RH, wind speed and wind direction** (§1.3). `N2` = ESP32-S3 + DHT11, sends **temp and RH** only (§8.3.1). `N3` = ESP32-C5 + DHT11, sends **temp and RH** only (§8.3.2). The simulator also uses `N1`–`N3` (§8.4).

### 1.2.1 Backend implementation update (local, Nov 8)

The MQTT-to-dashboard and MQTT-to-database MVP is implemented in the repository root:

| File | Purpose | Local verification completed |
|---|---|---|
| `ws_server.py` | FastAPI service. Subscribes to `sensors/#`, `engine/#`, and `crew/#`; wraps each MQTT JSON payload in `{topic, rx_ts, data}`; broadcasts it to `/ws`; exposes the latest per-topic cache at `/api/sensors/latest`; serves the built dashboard at `/` when `dashboard/dist` exists. | Uvicorn started successfully; MQTT connection and `/api/sensors/latest` cache verified with simulator messages; dashboard root and API returned HTTP 200 locally. |
| `ingest.py` | Subscribes to `sensors/+/wx`, validates weather messages, converts missing `-1`/`-99` values to SQL `NULL`, upserts `nodes.last_seen`, and inserts readings. | Simulator messages were persisted and queried from PostgreSQL. |
| `schema.sql` | Creates the PostGIS extension plus `nodes`, `readings`, and `fire_points`. `readings` has a unique `(node_id, seq)` constraint to avoid duplicate weather inserts. | Applied to a local PostGIS 3.7 database. |
| `.env.example` | Documents MQTT and database configuration; `.env` is intentionally ignored by Git. | Used for local and Pi configuration. |
| `simulator.py` | Optional fake N1-N3 publisher for offline development only. | Used to verify both backend paths; stop it before testing real sensors. |
| `run_live.ps1` | Windows helper for a laptop on FireNet. It checks `.env`, verifies `10.42.1.1:1883` is reachable, starts ingest/WebSocket in separate terminals, and opens the latest-message endpoint. | Added; requires FireNet and a real Pi broker. |
| `reset_local_test_db.ps1` | Guarded reset of the local Docker test tables only. | Added to remove existing simulator rows before a live-data test. |
| `run_live.sh` | Pi/Linux launcher. Starts `ingest.py` and `ws_server.py` together and stops both cleanly on Ctrl+C. | Added for deployment after `uv sync` and `.env` setup. |

Local Docker development services: `fireguard-mqtt` exposes Mosquitto at `127.0.0.1:1883`; `fireguard-postgis` exposes PostGIS at `127.0.0.1:5433` (container port remains 5432). The host port 5433 was selected because Windows would not allow a 5432 binding. These containers are local test infrastructure, not the Pi deployment.

**Real-data test status:** not yet confirmed end-to-end from the Pi broker. A laptop on FireNet must set `MQTT_HOST=10.42.1.1`, stop `simulator.py`, restart `ingest.py` and `ws_server.py`, and confirm incoming W1/N1/N2/N3 rows. On the Pi itself, use `MQTT_HOST=127.0.0.1` because Mosquitto is local.

`simulator.py` now adds `"_simulated": true` to its messages. The two services ignore marked simulator messages by default (`ALLOW_SIMULATOR_DATA=false`), so a live run will not pollute the dashboard cache or database. Set `ALLOW_SIMULATOR_DATA=true` only for intentional offline simulator testing. This marker is specific to the project simulator; real sensor messages remain unchanged.

**Known issues from code review (Sat Nov 7 evening). #1, #2 and #4 fixed Nov 7; re-apply `schema.sql` on any existing database to drop the old constraint:**
1. **Readings are dropped after a node restarts.** `readings` has `UNIQUE (node_id, seq)` and `ingest.py` inserts with `ON CONFLICT DO NOTHING`. Every node restarts `seq` at 0 on reboot or power loss (ESP32 nodes, and the wind bridge when it restarts). From then on, its new readings are silently discarded until `seq` passes the old maximum. `nodes.last_seen` still updates and `ingest.py` still prints `Saved <node> seq=…`, so it is easy to miss. This hits the grey-node demo (unplug a node, plug it back). Suggested fix: drop the unique constraint. QoS 0 MQTT never redelivers, so the duplicates it guards against don't occur. Alternative: add a per-boot id to the payload.
2. `ws_server.py` waits with `websocket.receive()`. After a client disconnects, the next `receive()` raises `RuntimeError` instead of `WebSocketDisconnect`, so every closed dashboard tab logs an error (functionally harmless). Fix: use `receive_text()`.
3. Pi deployment: `uv sync` needs internet, but with FireNet on the Pi has none, and `python-dotenv` is a new dependency. Sync before turning FireNet on. `/home/thunder/fire` is the older `uv init` project, not a clone of this repo; decide whether to clone the repo there or copy the files.
4. `.env.example` uses database port `5432`; the local Docker PostGIS above is on `5433`.

### 1.3 Node N1: La Crosse TX145WSDTHv3 over SDR (working)
We could not get an anemometer + vane for the ESP32, so we use the outdoor unit of a **La Crosse S81120v2** station (model **TX145WSDTHv3**, FCC ID OMOTX145WSDTHv3, 433 MHz, 3×AA + solar). It sends wind speed, wind direction, temperature and humidity, so it is a complete weather node by itself.

**How it works**
```
TX145WSDTHv3 ──433.92 MHz OOK──► Nooelec NESDR SMArt v5 (USB) ──► rtl_433 -R 73 -F json ──► sdr/wind_bridge.py ──► MQTT sensors/N1/wx
```
- **Receiver:** Nooelec NESDR SMArt v5 (FCC ID 2A8GD-SMArt-V5), a standard RTL2832U dongle, so no special driver is needed. Antenna on the SMA port: a whip extended to ~17 cm, or ~17.3 cm of straight wire (quarter wave at 433.92 MHz), vertical.
- **Decoder:** rtl_433 (25.12 used). Protocol **73** = "LaCrosse TX141-Bv2, TX141TH-Bv2, TX141-Bv3, TX141W, TX145wsdth". rtl_433 reports the sensor as model **`LaCrosse-TX141W`** and alternates two packet types:
  ```json
  {"model":"LaCrosse-TX141W","id":51625,"channel":0,"battery_ok":1,"wind_avg_km_h":0.000,"wind_dir_deg":322,"test":0,"mic":"CRC"}
  ```
  plus temperature/humidity packets (`temperature_C`, `humidity`). It reports **average wind only, no gust.** The decoder in `lacrosse_breezepro.c` is for the 915 MHz LTV-WSDTH01 and does **not** apply.
- **Bridge** (`sdr/wind_bridge.py`): filters `LaCrosse-TX141W` packets with our sensor id, keeps the latest wind and the latest temp/RH, and publishes **one full `wx` heartbeat per packet received**. That way a wind packet never blanks the temperature, and the other way round. Conversions: `wind_ms = wind_avg_km_h / 3.6`, `wind_dir = (wind_dir_deg + DIR_OFFSET) % 360`, `batt = 100` if `battery_ok` else `10` (the sensor only reports ok/low). Fields with no data yet: `temp_c = -99`, others `-1`; `flame = -1` always. It does **not** resend cached values on a timer, so a dead sensor still goes stale. Self-check: `python3 sdr/wind_bridge.py --test`.
- **Settings** (env vars): `MQTT_HOST` (default `localhost`), `SENSOR_ID`, `NODE_ID` (default `N1`), `DIR_OFFSET` (default `0`).

**Run** (from `Team-2/`, laptop on FireNet):
```bash
rtl_433 -f 433.92M -R 73 -F json | \
  MQTT_HOST=10.42.1.1 SENSOR_ID=51625 python3 sdr/wind_bridge.py
# check, on the Pi or any FireNet machine:
mosquitto_sub -h 10.42.1.1 -t 'sensors/#' -v
```
**Verified Sat Nov 7:** laptop (SDR attached) on FireNet → messages seen on the Pi broker (then published as `W1`; the node is now `N1`).

**Setup and gotchas**
- One-time on the host: `blacklist dvb_usb_rtl28xxu` in `/etc/modprobe.d/` (else the TV-tuner kernel driver grabs the dongle).
- `usb_claim_interface error -6` = another program already has the dongle open (we hit this with a leftover `rtl_433` test run). Stop it (Ctrl+C or `kill <pid>`) and run again.
- **Sensor id changes when the batteries come out.** Run plain `rtl_433 -f 433.92M -R 73 -F json`, read the new `id`, update `SENSOR_ID`. The sensor sends a burst right after battery insertion.
- **Direction depends on mounting:** the vane reads relative to the housing. The label says the solar panel must face **south**. On the floor, either face it to real south or set `DIR_OFFSET` (mark north on the floor; the fan's direction must match compass directions).
- The Python MQTT library (`python3-paho-mqtt`) must be installed before joining FireNet, which has no internet.
- **Decided Nov 8: the SDR stays on a laptop** on FireNet for the demo (not moved to the Pi); it publishes to the Pi broker with `MQTT_HOST=10.42.1.1` as above.

**Still to verify**
- [ ] Fan test: blow from a known compass direction and confirm `wind_dir` = direction the wind comes **from** (§2.2 relies on this). Set `DIR_OFFSET` if needed.
- [ ] Transmit interval: time between consecutive `sensors/N1/wx` messages. If it is longer than ~10 s, the 3-heartbeat stale rule (§5.1) needs a per-node threshold for N1.

### 1.4 Coordinates
**Decision (Nov 7): no tabletop model, no scale.** Each node gets a **fake GPS position** at a real Yarnell spot. All positions are real coordinates; the room's layout doesn't matter except for spacing (below).
- **Sensor nodes:** `data/yarnell/nodes.json` maps node id → lat/lon + label; the engine/ingest fills `nodes.geom` from it. **Decided Nov 8:** **N1** (wind) on the ridge near the saddle, where a crew would put it (high, exposed); **N2** in the box canyon on the 2013 descent line; **N3** between the canyon and the ranch. Exact lat/lon picked on the map (Nov 8), in `data/yarnell/nodes.json`.
- **Fire points:** (1) **hot sensor node rule**, in the engine: a fresh reading ≥ 45 °C, or a rise of more than 8 °C within 30 s, creates a fire point at that node's position (hair dryer; DHT11 tops out near 50 °C, so tune on the day); (2) **manual** "ignite" button on the dashboard (stage backup). At most one fire point per sensor node; a node that stays hot keeps its first ignition time. Fire points last until a manual reset.
- **Crew:** single crew `C1`. The scenario simulator starts it at the **saddle at 16:20** (2013 clock) and walks it down the **2013 descent line** at its computed travel speed, in real time. It keeps walking that line even when the system reroutes: the demo contrasts what happened with what the system advises.
- **Wind:** the fan blows on the real sensor; **N1's wind applies to the whole area** (one sensor can't tell where the wind changes, so using it everywhere is the safer assumption). The fire model multiplies N1's speed by `WIND_SCALE` (default ×4: a fan gives 3–5 m/s, the 2013 outflow was 13–19 m/s). Engine-only; not shown on the dashboard. **North still matters:** mark north on the floor, or set `DIR_OFFSET` once (§1.3).
- **Demo runs in real time** (no time compression); wind shifts and new fire points change arrival times instantly.
- **Floor space:** only for spacing nodes far enough apart (a couple of metres) that the fan or heat gun hits one node and not the others, so each reads differently.
- **Messages carry lat/lon (EPSG:4326);** the engine converts to **EPSG:32612 (UTM 12N, metres)** with one `pyproj` transform in one place, because the fire and crew models need metres (spread rate and Tobler speed in km/h, rasters in metres). Reproject every raster input to EPSG:32612 on load.
- **Routing crop:** the graph covers a 2 km × 2 km square inside the 5 km data patch (§3.1, §4.1), **centred at ≈ 34.2229 N, −112.7806 W** (UTM x0 334990, top 3789300; moved 320 m west and 210 m north on Nov 8 so the lunch spot, anchor point and ~2,600 cells of the 16:00 black fit): ridge, descent point, box canyon, deployment site, ranch and the edge of the black. Yarnell town stays outside.
- **Fallback** if the DEM/LANDFIRE load is not ready: single fuel (SH5) and flat ground in real metres. Only the terrain detail is lost.
- Dropped with the tabletop: table size, `SCALE`, origin `E0/N0`, foam terrain.

---

## 2. Fire model

### 2.1 Decision: use pyrothermel
- `pip install pyrothermel`. Python bindings to the Missoula Fire Lab's Behave C++ engine (Rothermel surface model, the same engine BehavePlus and FlamMap use).
- A community wrapper, not an official Fire Lab release. Pitch line: "Rate of spread from the Fire Lab's Behave engine, via pyrothermel."
- **The Pi needs a source build.** PyPI only ships a Windows wheel. It needs `build-essential` + `cmake`. It built fine on x86 Linux / Python 3.14. **Not yet tested on the Pi** (Debian 13 Trixie, aarch64, Python 3.13 via uv).
- Reference demo: `pyrothermel_demo.py` (runs, 9 self-checks pass).

### 2.2 Inputs
| Input | Value / source | Notes |
|---|---|---|
| `fuel_model` | `FuelModel.from_existing(<int>)` with the LANDFIRE **FBFM40** cell value | The integer IDs equal LANDFIRE codes (101 = GR1, 145 = SH5). 91–99 = non-burnable → spread 0, so they act as natural barriers. Demo default: `SH5` (chaparral, Yarnell) |
| `moisture_scenario` | Demo: `MoistureScenario.from_existing(1, 2)`. Live: `MoistureScenario(one_hr, ten_hr, hundred_hr, live_herb, live_woody)` as fractions | **TODO:** 1-h moisture from temp + RH using the Rothermel 1983 (INT-GTR-143) table, about 10 lines. 10-h ≈ 1-h + 1%, 100-h ≈ 1-h + 2%. **Input (decided Nov 8):** hottest temp and lowest RH across fresh sensor nodes, one value for the whole area; a node with a fire point is excluded (the hair dryer must not dry out the whole map) |
| `wind_speed` | N1's `wind_ms` × `WIND_SCALE` (§1.4), **km/h** (`× 3.6`) | Use the **highest recent `wind_ms`** (conservative). N1 (La Crosse) reports averages only, no gust |
| `wind_input_mode` | `'direct_midflame'` for nodes mounted ~1–2 m | Use `'ten_meter'` (+ canopy_cover/height/ratio) for **forecast** winds. 20 km/h at 10 m becomes ~9.5 km/h midflame, which more than halves the spread rate |
| `wind_direction` | **`wind_dir`, direction the wind blows FROM** | Verified in pyrothermel: 225 in → head runs toward 45. **No +180 needed.** N1's convention still needs the fan test (§1.3) |
| `slope` | Degrees, from the slope raster **at the fire point** | Conservative option: steepest upslope sampled along the fire→target line |
| `aspect` | Degrees from north, direction the slope **faces (downhill)**, at the fire point | Same convention as `gdaldem aspect` |

### 2.3 Calls and outputs
```python
run = PyrothermelRun(fuel, moisture, wind_kmh, wind_direction=wind_from, slope=s, aspect=a)
head = run.run_surface_fire_in_direction_of_max_spread()
r    = run.run_surface_fire_in_direction_of_interest(bearing_deg)   # any direction
```
Outputs (metric): `spread_rate` (km/h), `direction` (compass bearing of the head), `flame_length` (m), `fireline_intensity` (kW/m), `reaction_intensity`, `midflame_windspeed`.
- **We don't write our own ellipse.** `direction_of_interest` handles the ellipse internally, including a sensible length-to-breadth ratio (~3.7:1 at 20 km/h). The hand Anderson formula would give ~22:1 there unless capped at 8 like FARSITE does.
- Ignore `scorch_height` (meaningless at extreme intensity).

### 2.4 Fire arrival time per cell (minimum travel time, decided Nov 8, ADR 0002)
The fire **keeps its history**, so a wind shift turns the fire from where it already reached (the point of the demo). Finney (2002) minimum travel time on the same 10 m grid as the crew routing:
- **State:** one fire arrival time per cell, stored as a **clock time** (`inf` = not reached).
- **Start:** cells inside the **fire area at 16:15** (`scenario.geojson`, traced from the SAIT overview slides) are reached at scenario start and are the black. Only `front0` spreads: ground burned 16:00–16:15 (the north strip of the routing area). The west edge by the lunch spot held since 16:00 and is cold (changed Nov 8, was: the whole edge of the Fig. 22 black). New fire points set their cell to their ignition time. No separate main-fire points.
- **Each recompute:** cells with arrival ≤ now are fixed. Run scipy Dijkstra (multi-source) from the fire front over the unreached cells; step time = step length / rate of spread in that step's direction under **current** wind and moisture. Those times become the new arrival times.
- **Rate of spread:** pyrothermel `run_surface_fire_in_direction_of_interest`, **cached by (fuel, slope band, aspect band, step direction)** per recompute: a few hundred calls instead of 40k × 8.
- **Cell states:** reached > `BURNOUT_MIN` (5 min) ago = **the black** (safe); reached within the last 5 min = **burning** (impassable); not reached = unburned.
- Divide the time **until** arrival (arrival − now) by the **safety factor (1.5)** before comparing with crew times. Show the unfactored value as the uncertainty band.
- Model assumptions to state openly: constant conditions between recomputes, no spotting, no terrain wind channeling, one wind for the whole area.
- **Implemented (Nov 8):** `margin.FireGrid`; details and numbers in §5.6 ("Update Nov 8, fire spread" and "Start state from the overview slides").

**How it differs from v1 (straight lines, §5.6):**

| | v1: straight lines from each fire point | Grid (`FireGrid`) |
|---|---|---|
| Path | Straight line fire point → route point | Fastest fire path cell to cell (Dijkstra), goes around obstacles |
| Fuel, slope, aspect | The fire point's own cell, applied all the way out | Each 10 m step uses the cell it is in (faster upslope/downwind, slower backing) |
| Non-burnable cells | Only if the fire point itself is non-burnable | Stop the fire wherever they are |
| Wind shift | Redrawn as if the new wind had always blown | History kept: reached cells keep their times; the fire turns from where it is |
| Start | Spot fires only; the black doesn't spread | Fire area at 16:15; its live front (`front0`) spreads |
| Flame buffer | One head flame length for the whole fire | Per cell, the flame in the direction the fire reached it (backing ~5 m, head ~25 m) |

**Model vs the 2013 slides:** from 16:15 with wind from 20° at 7.5 m/s the front covers ~1 km in ~20 min; the slides show ~1 km by 16:40 (IoU ≈ 0.55 with the traced 16:40 area), but the model runs ahead of the 16:30 slide. Why:
- The wind is constant and instant; in 2013 it swung 180° over about an hour, so the test front-loads the outflow.
- Rough weather inputs: one wind for the whole area and very dry fuel (32 °C / 15 % RH → ~3 % 1-h moisture) make pyrothermel fast in SH5.
- The slide fire areas are estimates: edges ~30–60 m, traced areas ~85 % of the stated acreage (scale bar vs site markers disagree).
- Not modelled: spotting, terrain channelling of the wind, suppression (retardant on the north end).

So the timing is in the right range, not exact; the 1.5 safety factor on the margin is meant to cover this.

### 2.5 Extra outputs used for alerts
| Use | Rule | Source |
|---|---|---|
| Suppression limit | flame < 1.2 m hand crews OK · 1.2–2.4 m equipment only · > 2.4 m control efforts fail | Fire characteristics chart / hauling chart |
| **Safety zone adequacy** | Required separation ≥ **4 × flame height** (use flame_length as the proxy). Alert if a safety zone's radius is smaller | Butler & Cohen 1998. Behave's safety module isn't exposed, so it's one line of our own code |
| Safety zone adequacy (vegetation-height version, **to verify**) | Current NWCG guidance sizes the separation from **vegetation height**, slope and wind (≈ 8 × vegetation height × slope/wind factor). LANDFIRE EVH gives vegetation height for every cell, so this could replace the 4× flame rule and auto-suggest safety zones (low or non-burnable patches big enough, near routes) | Butler 2014; NWCG safety zone guide. **Check the formula against the source before use** |
| Crown fire threshold (timber sites only) | `calculate_crowning_index()` = wind speed where an active crown fire starts, i.e. a wind-based trigger | Not relevant to chaparral. Test output looked odd (crowning with crown ROS < surface ROS), so **verify against BehavePlus before use** |

### 2.6 Known pyrothermel caveats
- Only `surface`, `crown` and `mortality` are bound. Behave's **spotting, safety, size, contain and ignite modules aren't available.**
- `run_crown_fire_rothermel()` actually runs Scott & Reinhardt (duplicate of the other method).
- Crown results' `reaction_intensity` is actually heat per unit area.
- Run the surface method before the crown method.
- `slope` must be ≥ 0.

### 2.7 Fallback if pyrothermel won't build on the Pi
Head ROS ≈ 10% of 10-m open wind speed (Cruz & Alexander 2019, **forests and shrublands; grass can be faster**). Ellipse: `R(θ) = R_head·(1−e)/(1−e·cosθ)`, `e = sqrt(1−1/LB²)`, LB from Anderson 1983 **capped at 8**. Or run pyrothermel on a laptop on the same network.

---

## 3. Terrain and fuel data (static, loaded once)

### 3.1 Yarnell patch: done (Nov 7)
**Patch:** 5 km × 5 km, 10 m grid (500 × 500), **EPSG:32612**, bounds `334640 3785520 339640 3790520` (lon/lat ≈ 34.1976–34.2434 N, −112.7956 to −112.7404). It covers the deployment site, the ridge above it, the Hotshot Journey trailhead and Yarnell town. The 2 km × 2 km routing crop is taken from it (§1.4).

Rebuild (needs internet, no GDAL install needed; rasterio bundles it): `uv run --with rasterio --with pyproj python data/fetch_yarnell.py` → `data/yarnell/`

| File (`data/yarnell/`) | Content | Source |
|---|---|---|
| `dem.tif` | Elevation, m, float32 | USGS 3DEP ImageServer (best available, resampled to 10 m) |
| `slope.tif`, `aspect.tif` | Degrees. Aspect = direction the slope faces (downhill), 0 where flat (= `gdaldem -zero_for_flat`) | Horn method in numpy, self-checked |
| `fuel.tif` | FBFM40 codes, uint16 (nearest neighbour from 30 m) | LANDFIRE **LF2025** FBFM40 ImageServer (no email needed) |
| `hillshade.png` | Map background; corner lon/lat in `meta.json` for a MapLibre image source | From the DEM |
| `perimeter.geojson` | Final perimeter, Yarnell Hill 2013, 7,954 ac | NIFC InterAgency Fire Perimeter History |
| `osm.geojson` | 198 roads/tracks/trails, 434 buildings, water, 6 fences | OpenStreetMap (Overpass) |
| `sites.geojson` | Fatality/deployment site, observation deck (ridge), Hotshot Journey Trailhead, Yarnell town; OSM node ids in properties | OpenStreetMap |
| `meta.json` | Bounds, corners, sources, fetch time, value summaries | — |

**Manual downloads (also in the repo, offline copies):**
- `docs/USGS_13_n35w113.tif`: 3DEP 1/3 arc-second tile (NAD83 geographic, covers 34–35 N, 112–113 W; tiles are named after their **north** edge). **440 MB, don't commit** (move to `data/raw/` and gitignore). Direct URL: `https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/n35w113/USGS_13_n35w113.tif`
- `data/landfire_5fb9e848-…/`: LANDFIRE LFPS download, LF2025 **FBFM40, EVC, EVH, EVT**, 30 m, native Albers (EPSG:5070), box 34.18–34.26 N, −112.82 to −112.72. Code meanings are in each `*.tif.vat.dbf`.
- Request native Albers from LANDFIRE and reproject once yourself with `-r near` (choosing UTM there means a second resample).
- Optional: 1 m lidar from 2021 (`AZ_Yavapai_2021_B21`, tiles `x33y379`, `x33y380`) exists for a sharper hillshade.

**Cross-checks (both paths agree):**
- DEM: tile vs ImageServer, median |diff| 0.12 m, 99th percentile 0.9 m, max 2.1 m. The difference grows with slope and mostly disappears with a 1 m east shift, so it comes from resampling/datum handling (NAD83 vs WGS84 ≈ 1 m), not bad data. Below the source's own ~1–2 m accuracy. Prefer the tile (one resample under our control, works offline).
- Fuel: LFPS download vs ImageServer, 99% of cells identical (differences at cell edges).
- Sites sampled: Yarnell town 1,458 m (published ≈ 4,780 ft), deployment site 1,537 m, ridge deck 1,685 m.

**What the data says about the patch:**
- Fuel (LF2025 FBFM40): 76% **GS2 (122)**, then 91 urban 6%, GR2 6%, 99 bare 4%. Inside the 2013 perimeter it is almost uniformly GS2: post-fire regrowth. **LANDFIRE has nothing before LF2016, so no pre-fire fuel layer exists.**
- Vegetation type (EVT): **51% Mogollon Chaparral**, 29% Madrean pinyon-juniper. So it *is* chaparral, just young, which supports SH5 for the 2013 replay.
- Shrub height (EVH): mostly 1.1–1.4 m (chest high). Shrub cover (EVC): mostly 26–31%.
- Canopy layers (CC, CH, CBH, CBD) skipped: crown-fire inputs; there's almost no timber here (§2.5).

**Fuel decision:** 2013 replay → SH5 inside the 2013 perimeter (pre-fire chaparral, as the investigation describes). "If it happened today" → LF2025 `fuel.tif` as is.

### 3.2 Yarnell: left to do
The ordered plan is in §9 (Map + routing track); this list keeps the data-specific details.
- [ ] Put EVC/EVH/EVT on the 10 m grid (nearest neighbour from the LFPS download) and turn shrub height × cover into the off-trail travel factor (replaces the placeholder per-fuel factors in §4.3).
- [ ] Switch `fetch_yarnell.py` to read the local DEM tile and LFPS rasters (`gdalwarp -r bilinear` / `-r near`); keep the ImageServers as a cross-check.
- [x] From the SAIT report (Nov 8; printed page numbers, PDF page = printed + 6):
  - **Deployment site:** 34.22047, −112.77757 (p.82). **Ranch:** ~600 yd east of it (p.2, p.31) ≈ 34.2205, −112.7716; size not stated. **Fig. 7 (p.20) puts it at 34.2195, −112.7709** (OSM buildings within 35 m); that is the value used.
  - **Crew:** in the black near the "lunch spot", on the ridge flank (p.21, p.30). Walked SE on the two-track road to the saddle ~16:05–16:20, then down a box canyon: boulders, washes, "pockets of heavy brush", travel under half the road rate (p.30, p.37–38).
  - **Vegetation:** chaparral 3–10 ft, "nearly impenetrable", unburned since 1966 (p.8, p.65).
  - **Wind:** W 10–12 mph ~15:50 (p.25); outflow reached the fire's north end 16:18 (p.29), the south end 16:30: WNW → NNE, 30–40 mph, gusts 43, "50 mph+" in the box canyon (p.64, p.74). Deployment ~16:41; spread 10–12 mph, 60–80 ft flames, under 2 min warning (p.78).
  - **The black:** > 400 acres next to the crew (p.17, p.35). Vehicles moved to the Shrine area ~15:55 (p.24).
- [x] Hand-draw **the black at ~16:00** (polygon) and the **2013 descent line** (reference layer) from the report maps → `data/yarnell/scenario.geojson`.
- [x] Choose the 2 km × 2 km routing crop and each node's fake GPS position (§1.4).
- [x] Load rasters and GeoJSON into PostGIS: `load_static.py` (Nov 8). Rasters `dem`/`slope`/`aspect`/`fuel` via `raster2pgsql` (100 × 100 tiles); `perimeter`/`sites`/`scenario`/`osm` into one `static_features(layer, name, props, geom)` table; `nodes.geom` from `nodes.json`; all EPSG:32612. Rerunnable; no GDAL/`ogr2ogr` needed. One-time superuser step: `CREATE EXTENSION postgis_raster`. `deploy_pi.ps1` runs it after the schema. Tested on PostGIS 3.6 as a non-superuser.
- [ ] SH5 override inside the perimeter for the 2013 replay (one flag in `config.py`).
- [ ] Optional: offline street basemap (PMTiles); 1 m lidar hillshade.

### 3.3 General layer reference
| Layer | Source | Use |
|---|---|---|
| Elevation (DEM) | USGS 3DEP 10 m (apps.nationalmap.gov/downloader) or LANDFIRE elevation | Tobler slopes, hillshade |
| Slope, aspect | LANDFIRE (precomputed) or `gdaldem slope/aspect dem.tif` (`-zero_for_flat` for aspect) | pyrothermel inputs |
| Fuel model | **LANDFIRE FBFM40** | pyrothermel fuel + travel factors |
| Vegetation cover / height (EVC, EVH) | LANDFIRE | Travel factor (brush density and height) |
| Water | LANDFIRE NB8, USGS NHD, OSM | Barrier |
| Buildings, fences | OSM | Barrier |
| Understory density (field version) | USGS 3DEP lidar: share of returns 0.5–3 m above ground | Travel factor (Campbell et al. 2017) |

GDAL command-line tools (`sudo apt install gdal-bin`) are optional: rasterio covers reprojection and the numpy code covers slope/aspect/hillshade. They're handy for manual steps and checks (`gdalinfo -stats`), and QGIS brings them anyway.

Loading:
```bash
gdalinfo in.tif                                  # check the source coordinate system
gdalwarp -t_srs EPSG:32612 in.tif dem.tif
raster2pgsql -s 32612 -t 100x100 -I dem.tif dem | psql fire
```
Point lookup:
```sql
SELECT ST_Value(rast, pt) FROM dem WHERE ST_Intersects(rast, pt) LIMIT 1;
```

---

## 4. Crew model (routing)

### 4.1 Graph: two layers joined together
1. **Trail graph.** OSM roads/tracks/trails from `osm.geojson` (decided Nov 8; no hand-drawn routes, see §4.6).
2. **Off-trail grid graph.** Every 10 m cell is a graph vertex with 8 neighbours (16 with knight moves, for smoother paths). Join trail vertices to the nearest grid cells.

**How the grid is built** (`build_graph.py`, Python + numpy, written to `data/yarnell/graph.npz`, optionally also to PostGIS for SQL queries; runs once):
- **Area:** only the 2 km × 2 km routing crop (§1.4), not the full 5 km patch: 200 × 200 = **40,000 nodes**. The full patch would be 250k nodes / ~1M edges, too much for the < 2 s target with 3–4 Dijkstra runs per recompute.
- **Nodes (`grid_nodes`):** one per cell centre. `id = row * 200 + col`, `geom` = cell centre, `elev` from the DEM, `veg_factor` from §4.3.
- **Edges (`grid_edges`):** store only 4 of the 8 directions per node (E, SE, S, SW); the other 4 are the same edges seen from the neighbour. Each row has `cost` (source → target) and `reverse_cost` (target → source). ≈ 160k rows.
  ```
     NW  N  NE          store only:  E, SE, S, SW
      W  ●  E
     SW  S  SE
  ```
- **Impassable** (cost `-1`): slope > 38°, water/buildings (OSM polygons stamped onto the grid), diagonal corner-cutting past an impassable cell. Vegetation is **never** impassable (§4.3).
- **Trails:** OSM roads/tracks/trails, segmentized to ~25 m. Trail vertex ids follow straight after the grid ids (scipy needs dense ids), factor 1.0. **Connector edges** link each trail vertex to its nearest grid node so routes can leave and rejoin trails. Routing (crew times, escape routes, reroutes) always runs over **grid + trails + connectors** together (concatenated edge arrays in `graph.npz`), so an escape route can go cross-country wherever that is faster.
- Crew position and safety zones are **snapped** to their cell by grid arithmetic (§4.6).

Why both: crews mostly use trails, but Yarnell's crew went cross-country, so a trail-only graph can't represent that.

### 4.2 Edge cost (seconds)
```
S          = dz / length                               (rise/run, not degrees)
tobler     = 6 · exp(−3.5 · |S + 0.05|)   km/h         (fastest at 5% downhill)
speed      = tobler × min(factor_A, factor_B)          (worst cell governs)
cost_s     = length_m / (speed / 3.6)
reverse    = same with −S                              (uphill ≠ downhill)
```
- **Segmentize trails to ~25 m first** (`ST_Segmentize` + dump into individual segments). Otherwise a climb-then-descend segment shows dz ≈ 0.
- Grid edge length: 10 m straight, 14.14 m diagonal.
- **No corner-cutting:** skip a diagonal edge if either orthogonal neighbour is impassable.
- Impassable: no edge.

Trail cost SQL (corrected):
```sql
ALTER TABLE trails ADD dz float, ADD cost float, ADD reverse_cost float;

UPDATE trails t SET dz =
  (SELECT ST_Value(rast, ST_EndPoint(t.geom))   FROM dem WHERE ST_Intersects(rast, ST_EndPoint(t.geom))   LIMIT 1) -
  (SELECT ST_Value(rast, ST_StartPoint(t.geom)) FROM dem WHERE ST_Intersects(rast, ST_StartPoint(t.geom)) LIMIT 1);

UPDATE trails SET
  cost         = ST_Length(geom) / (6*exp(-3.5*abs( dz/ST_Length(geom) + 0.05)) / 3.6),
  reverse_cost = ST_Length(geom) / (6*exp(-3.5*abs(-dz/ST_Length(geom) + 0.05)) / 3.6);
```
Sanity check: a 100 m segment climbing 10 m costs ≈ 101 s uphill and ≈ 71 s downhill.
Endpoints outside the DEM give NULL dz, so check `WHERE cost IS NULL` after loading.

### 4.3 Travel factors (multiplier on Tobler speed)
**Vegetation slows the crew, it never blocks.** Crews can push through brush (the Yarnell crew did); marking it impassable would report "no route" when a slow one exists, or hide the route the crew is on. A slow route can still be compared against the fire's arrival time and turn amber/red. So vegetation gets a factor with a **floor of 0.1**, and only hard limits are impassable.

**Per edge:** `factor = min(veg_factor[source], veg_factor[target])` (the slowest cell governs the step). Vegetation costs the same in both directions; only slope makes uphill ≠ downhill. **Trail edges always use 1.0.**

**veg_factor per cell** from LANDFIRE EVH (height) × EVC (cover), a lookup table in `config.py`. **Placeholders: calibrate with the operator mentor and Campbell et al. 2017.** A table is easier to calibrate than a formula: change one cell at a time.

| Shrub height \ cover | < 25% | 25–50% | > 50% |
|---|---|---|---|
| < 0.5 m (knee) | 0.9 | 0.8 | 0.7 |
| 0.5–1.5 m (waist–chest) | 0.7 | 0.5 | 0.3 |
| > 1.5 m (over head) | 0.5 | 0.3 | **0.1** (floor) |

| Other ground | Factor |
|---|---|
| Trail / road | 1.0 |
| Grass / herb | 0.8 |
| Bare ground (off-trail but clear) | 0.8 |
| Open pinyon-juniper (tree cells; understory unknown) | 0.7 |
| Water, buildings, fences, slope > 38° | impassable |
| **Cells the fire reaches before the crew, or within 4 × flame height of the front** | blocked per recompute (§5.4) |

**Yarnell 2025 data:** shrub cover 24–34% (max 51%), height 0.3–1.5 m (max 2.0 m); no shrub cell is both ≥ 50% cover and ≥ 1.5 m. So today's patch is mostly 0.5–0.7. **2013 replay:** cells inside the 2013 perimeter set to `REPLAY_VEG` = 0.6 in `build_graph.py`, calibrated so the 2013 descent line (435 m, −130 m) takes ~20 min as in 2013 (16:20 → 16:42); 0.1 gave 124 min. Tobler already slows the steep descent. OSM footways are dropped in replay (Hotshot Journey Trail, 2016, runs down the descent line).

**Worked example**, one 10 m step on flat ground (Tobler ≈ 5.0 km/h):

| Ground | Factor | Speed | 10 m takes | Distance in 10 min |
|---|---|---|---|---|
| Trail | 1.0 | 5.0 km/h | 7 s | 840 m |
| Typical here (1.2 m, 30% cover) | 0.5 | 2.5 km/h | 14 s | 420 m |
| 2013 replay, old dense chaparral | 0.1 | 0.5 km/h | 71 s | 84 m |

So a 400 m shortcut through old chaparral costs about as much time as 4 km of trail.

**Fallback** if EVC/EVH aren't on the grid yet (by fuel model): bare/grass 0.8 · GS/TL 0.6 · SH1–3 0.4 · TU 0.3 · SH5–9 0.15.

**Build:** EVC/EVH reprojected to the 10 m grid with nearest neighbour → lookup → `grid_nodes.veg_factor` → edge costs when `grid_edges` is built.

### 4.4 Route quality
- 8-direction grids zigzag (~8% too long). Use 16 neighbours, or simplify the path afterwards and re-check it against barriers.
- **Scouting status:** the crew boss can mark segments **scouted** or **no-go** on the map. Unscouted off-trail stretches get an extra time buffer and a warning ("route uses 400 m of unscouted brush"). This addresses the Yarnell Hill lesson directly.

### 4.5 Start and targets (decided Nov 8)
- Start: latest `crew/C1/pos`, snapped to its cell.
- **Safety zones:** **Boulder Springs Ranch**, a circle of `RANCH_RADIUS_M` = 100 m around ≈ 34.2195 N, −112.7709 W (SAIT Fig. 7; deployment "approximately 600 yards west of the Ranch", p.2/p.31; size not stated, adjust by eye on the hillshade/OSM buildings), and **the black** (any black cell, §2.4; it grows as the fire burns). Vehicles dropped: they were moved to the Shrine area at ~15:55 (p.24).
- One Dijkstra from the crew gives times to every cell; each safety zone's escape route = the fastest path to any of its cells (`predecessors` gives the path).

### 4.6 Tool decision: scipy (decided Nov 8, ADR 0001)
**`scipy.sparse.csgraph.dijkstra` on an in-memory graph**, built once by `build_graph.py`. It handles directed costs (uphill ≠ downhill), runs in milliseconds, and the fire model (§2.4) uses the same grid. Closures = drop those edges from the edge list and rebuild the sparse matrix. PostGIS still holds the rasters and vectors for the map and SQL queries. `pgRouting` was rejected: it reloads every edge per query and needs 3–4 queries per recompute (untimed on the Pi). Also rejected: `skimage.graph.MCP_Geometric` (can't make uphill cost more than downhill), GRASS `r.walk` (heavy extra tool).
- **Graph file** `data/yarnell/graph.npz`: cell UTM x/y, edge arrays `src`, `dst`, `cost_s` (each direction its own entry), safety-zone cell ids.
- **Snapping** a lat/lon to a cell is arithmetic on the regular grid: `col = (x - x0) // 10`.
- **Trails** (the full-speed layer) = OSM roads/tracks only; everything else is routed over the off-trail grid at Tobler × vegetation factor. The 2013 crew route is a **map reference layer**, not a trail: they went cross-country through brush, so a full-speed trail there would invert the lesson.
- Gotcha: zero-cost edges vanish in a scipy sparse matrix (harmless; every step costs time).

---

## 5. Margin engine (design settled Nov 8)

Every recompute (on each incoming message, and at least every 5 s):
1. Pull the latest reading per sensor node (wind as a ~20 s moving average, §5.7), the active fire points and the crew position. **A stale sensor node is not skipped: the engine uses worst-case values for it** (§5.3). Apply the hot-sensor-node rule (§1.4) to create fire points.
2. **Fire:** advance the fire arrival times from the current fire front under current wind and moisture (§2.4). Classify cells: the black / burning / unburned.
3. **Crew:** one Dijkstra from the crew to every cell, no closures: `t_crew(cell)`.
4. `t_fire(cell) = (arrival − now) / safety_factor`.
5. **Close** a cell if `t_fire < t_crew`, **or** it is burning, **or** it lies within 4 × flame length of a burning cell (60–80 ft flames ≈ 75–100 m). Drop the edges touching closed cells and run Dijkstra again.
6. **Escape routes:** one per safety zone (ranch; any black cell). Margin of a route = `min over its cells (t_fire − t_crew)`; black cells count as unlimited. The safety zone's own cells are included.
7. A detour reaches later cells later, so repeat 5–6 once or twice until the routes stop changing. **No path left → "no safe route" alert.**
8. **Active route** = the fastest escape route that isn't red, picked once at Start; after that it changes only by a reroute (decided Nov 8, §5.7). **Reroute** = the active route turns red and another safety zone's route isn't. A route the fire bends off its fire-blind path (closures) raises "Route <id> re-planned around the fire" and sets that route's `replanned_ts`, when it moved > `ROUTE_CHANGE_M` (50 m) from the last announced path, at most once per `ROUTE_CHANGE_HOLD_S` (20 s) (Nov 8, ticket 17).
9. **Leave-by time** = `now + margin − buffer`, shown as a countdown.
10. Publish `engine/state` (§5.5); `ws_server.py` forwards it to the dashboard.

**Demo story (current, decided Nov 8 ~06:05; details in §5.7 and `docs/specs/demo-dashboard/spec.md`):** ready screen → Start (16:20; the crew takes and walks the route back to the black) → fan on N1 from the SSW (both green) → fan to the WNW (~290–300°, ≥ 4 m/s) → the ranch route (where the 2013 crew went) turns red while the black route stays green → stop. NNE ("no safe route") for Q&A only.

*Superseded story (Nov 8, before ~05:30):* scenario starts 16:20, crew at the saddle heading for the ranch, the black ~19 min behind them. Wind from the SSW (as until ~16:00) pushes the fire NE: both routes green. Fan on (the outflow; in 2013 the wind swung 180° to NNE within the hour) → the front north of the crew runs south → `ranch` red, then `black` red → "no safe route". No reroute: from the saddle the outflow cuts both. The simulated crew keeps walking the 2013 line (§1.4), so the margin keeps falling on the path the real crew took.

### 5.1 Thresholds (defaults, in `config.py`)
| Status | Rule |
|---|---|
| Green | margin ≥ 5 min |
| Amber | 2 min ≤ margin < 5 min, **or** margin dropped > 30% within 30 s while under 10 min (`DROP_AMBER_BELOW_MIN`, Nov 8: a drop from 140 to 40 min showed amber with 40 min left) |
| Red | margin < 2 min → reroute + alert (the 2013 crew had under 2 min of warning, SAIT p.78) |
| Aging (sensor node) | 1 missed heartbeat at **that sensor's own interval** |
| Stale (sensor node) | 3 missed heartbeats at its own interval (30 s for N2/N3; 3 × the measured interval for N1, §1.3) |

Every reading shows its age on the map.

### 5.2 Alerts (decided Nov 8)
1. Active route red, or rerouted.
2. No safe route.
3. Sensor node stale.
4. Ranch too small for the current flame length (radius < 4 × flame length; Butler & Cohen 1998).

Dropped: flame length over 1.2 / 2.4 m near the crew (covered by #4), forecast vs sensor wind (no forecast wind; one wind for the whole area).

### 5.3 Worst-case values for a stale sensor node
Why: skipping a silent sensor node lets the margin stay green on old data, the same failure as Yarnell. Worst case means the margin can only shrink. In the demo, the silent N3 visibly greys out and the margin drops. Defaults in `config.py` (decided Nov 8):

| Input | Worst case |
|---|---|
| Wind speed | max(last reading, `WORST_WIND_MS` = 13 m/s ≈ 30 mph, the low end of the 2013 outflow) |
| Wind direction (N1 stale) | Unknown → head-fire rate of spread in every direction |
| Temp / RH | max(recent temp, `WORST_TEMP_C` = 40) / min(recent RH, `WORST_RH` = 8 %) |

### 5.4 How the fire closes routes (graph fixed, closures change)
Like a road map with some roads closed today: the graph (`graph.npz`, §4.6) is built once; each recompute drops the edges touching closed cells (§5 step 5) from a copy of the edge arrays and rebuilds the sparse matrix (milliseconds). There is no `fire_blocked` table.

**Example:** crew at the saddle, safety zones = ranch and the black. 16:20: nothing closed, ranch route margin 9 min → green. Outflow arrives: the canyon burns in 6 min but the crew needs 8 → canyon cells closed; the ranch route detours or dies; the black route (back uphill) becomes active, margin 4 min → **amber, rerouted, alert**.

### 5.5 `engine/state` message
```json
{"type":"state","ts":1762600000,
 "routes":[{"id":"ranch","margin_min":4.2,"status":"amber","active":false,
            "path":[[-112.782,34.221],[-112.779,34.220]]},
           {"id":"black","margin_min":11.0,"status":"green","active":true,"path":[...]}],
 "nodes":[{"id":"N3","status":"stale","age_s":45}],
 "fires":[{"lat":34.2215,"lon":-112.779,"ignition_ts":1762599900}],
 "areas":{"black":<GeoJSON>,"burning":<GeoJSON>,"front_5min":<GeoJSON>,"front_10min":<GeoJSON>},
 "alerts":["Rerouted: back to the black"],"leave_by_ts":1762600180}
```
`areas` are GeoJSON (Multi)Polygons traced from the cell grid (`rasterio.features.shapes`; adds `rasterio` as a runtime dependency). The full 40k-cell grid is not sent.

**Update Nov 8 ~08:30 (ticket 16):** `areas` replaced by `fire_grid` (`margin.fire_grid_msg`): the fire arrival time per cell as base64(zlib(uint16)), 0 = the black at Start, k ≥ 1 = `t0 + (k − 1) × 0.25 s`, 65535 = not within 15 min; plus `cols`, `rows`, `t0` (= `started_ts`), `burnout_s`, `corners` (lon/lat tl, tr, br, bl). ~50 KB per message (`areas` was ~52 KB). The dashboard (`dashboard/src/fireLayer.ts`) upsamples it ×2 bilinearly and redraws black / burning / 0–5 / 5–10 min at 20 fps at the interpolated scenario time, on a MapLibre canvas source: the front moves smoothly between cells and between the 1 s updates. The routing / fire grid also grew from 200 × 200 to **335 × 322 cells** (`build_graph.CROP_BOUNDS`: x 334640–337990, y 3787300–3790520, up to the top of `dem.tif`), so the 2013 black and the live front cover the upper map. black0 46,854 cells (front0 5,402); descent point → black0 still 18.8 min; demo-story margins unchanged. Engine step ~130 ms on the laptop (was 40–60), ~730 ms on a weather change (was ~270).
This is the **v2 target** format. What the v1 engine publishes today is in §5.6; fields added Nov 8 for the demo dashboard (`phase`, `started_ts`, `scenario_start`, `replan_ts`, per-point `t_crew_min` / `t_fire_min`, `crew.walking` / `arrived`) are in §5.7.

### 5.6 Implemented now: v1 engine (`engine.py`, Vivek, Sun Nov 8)
Built before the Nov 8 v2 design (§2.4, §4.6, ADR 0001/0002) and runnable today, so the dashboard can be built against it. **Simplification:** no off-trail grid, no scipy/pgRouting graph, no PostGIS dependency. Each hand-drawn route in `routes.geojson` is evaluated on its own along its points (densified to 25 m); "rerouting" = switching the active route. The fire spreads in straight lines from each fire point (the original §2.4), not by minimum travel time.

**Update Nov 8 (after the pull): v1 is for testing, not the demo.**
- Inputs are now real: `data/yarnell/nodes.json` and `data/yarnell/routes.geojson`, exported from the graph by `uv run python build_graph.py --routes` (fastest route from the descent point to each safety zone: `ranch` 28.1 min, `black` 18.8 min; v1's walking times match the graph's). The placeholders are no longer loaded.
- The demo follows the spec story in its order (fan on N1 turns the fire from the black's edge → ranch red → reroute to the black; then hair dryer on N2; then unplug N3). v1 can't do this: it has no fire from the black, only spot fires. With the real N2 (box canyon, on the descent line), a spot fire at N2 or N3 turns `ranch` red at once whatever the wind (checked: N2, wind 270° → `ranch` −26 min, 180° → −17 min; `black` stays green). The hair dryer beat never replaces the fan beat.
- Needed for the demo: fire spread from the black (§2.4, ADR 0002), then closures + the reroute loop (§5.4).
- **Update Nov 8, later:** `black0` is in `graph.npz` and the engine now routes over it when `data/yarnell/graph.npz` exists (§9 track 8b). Crew times along a route are the graph's own step times, so the blended `travel_factor` flaw of `routes.geojson` is gone in this mode. Each route in `engine/state` has `path`. Fire is still v1 (straight-line spread from spot fires); with a spot fire at N2, wind 270° at 7.5 m/s: `ranch` margin −25.3 min red (ETA 28.1; fire reaches the ranch end at 2.8 min), `black` margin +53.9 min green (ETA 18.8; worst point is the crew's own cell, fire there at 53.9 min). The 2.8 min over ~740 m (≈ 260 m/min, incl. the 1.5 safety factor and the 4 × flame buffer) is v1's straight-line spread; don't quote it, task 8c replaces it. `routes.geojson` + `load_routes` remain only as the no-graph fallback (and `test_margin.py`'s placeholder checks).
- **Update Nov 8, fire spread (§9 track 8c):** `margin.FireGrid` (graph mode). Arrival clock time per grid cell; `black0` reached at engine start / reset (16:20), counted as black; each recompute keeps cells reached by now and spreads from them by Dijkstra (one virtual source, edges weighted by each front cell's arrival time) over 8-neighbour grid edges between burnable cells. Rate of spread and flame length per (fuel, 5° slope band, 22.5° aspect band) × 8 directions from pyrothermel: 253 cell types, rebuilt only when the weather changes (~250 ms; ~25 ms otherwise). Each cell keeps the flame length in the direction the fire reached it (backing fires ~5 m, head ~25 m), and a cell is unsafe from the moment it lies within 4 × that flame of a burning cell (square window, conservative). Spot fires ignite their cell and the burnable cells around it at their detection time. Arrival times are unfactored; the margin divides (unsafe − now) by the 1.5 safety factor (§2.4). Checks: downwind < flank < upwind, wind shift keeps history and turns from the reached tongue, non-burnable wall never crossed, burning 5 min then black, engine spreads from the black + spot fire + reset (`test_margin.py`, 21 pass).
  **Start state from the overview slides (Nov 8, later):** `docs/YarnellOverview.pdf` has estimated fire areas at 16:00, 16:15, 16:30, 16:40, 16:50 (p.17–23); traced by colour and georeferenced on the lunch spot / deployment site / ranch markers into `scenario.geojson` (`fire area HH:MM`; replaces the Fig. 22 "the black", which matched the 16:00 area to 13 cells). Areas come out ~85 % of the slide acreage (the slide scale bar disagrees with the markers by ~20 %; markers kept). In the routing area the fire at 16:15 is the lunch-spot corner (cold) plus a strip ~1 km north of the crew (live, `front0`, 983 cells); the slides show it running south over the descent point by 16:40. Model check: from 16:15 with wind from 20° at 7.5 m/s the front covers ~1 km in ~20 min (slides: ~1 km by 16:40); overlap with the traced 16:40 area IoU ≈ 0.55; the model runs ahead of the 16:30 slide.
- **Update Nov 8, closures + reroute loop (§9 track 8d, §5 steps 3–7):** `margin.graph_routes(g, crew_xy, fire, now)`, called every recompute (no route cache). Crew Dijkstra with no closures → close every vertex (trail vertices via their cell) that is burning, within 4 × flame of burning, or reached by the fire (÷ 1.5) before the crew → Dijkstra again without those vertices → repeat until no new closures (they only grow; capped at 10). The `black` target is every cell black by now (`FireGrid.black(now)`), so it grows. A safety zone with no path left keeps its fire-blind path: it shows red with a negative margin and the existing all-red rule raises "NO SAFE ROUTE" (routes never disappear from `engine/state`). 2013 sweep, N1 7.5 m/s turning 10°/min from 200°: both green to 280°; `ranch` red at 290°; `black` amber at 330°, red at 340° → NO SAFE ROUTE; no detour appears from the saddle (fits the no-reroute story). The black route shortens as the black grows (18.8 → 16.2 min). 40–60 ms per recompute, ~270 ms when the weather changes (pyrothermel table rebuild). Checks: closures detour around a spot fire with margin ≥ 0 where the fire-blind route is negative; fire on the crew keeps both routes listed and red; the black target grows (`test_margin.py`, 24 pass).
  **Results from the descent point:** calm 225° 2.5 m/s → `ranch` +35.5, `black` +18.2 (both green). Outflow from 0–20° at 4–13 m/s → `ranch` −11 to −26 (red) **and `black` −9 to −15 (red)**: the fire runs SSW along the black's south edge and reaches the black route's end 10–20 min after the shift, the crew needs 18.8 min. `black` stays green under the outflow only within ~6 min of the black (the first stretch of the 2013 walk, ~16:04–16:10). Decided: keep 16:20 and show "no safe route" (spec demo story). Engine run, N1 at 7.5 m/s turning 1 step per minute from 200° clockwise: 200° both green → 270° both green → 300° `ranch` red, `black` green → 330° `black` amber → 0° both red, NO SAFE ROUTE. Turning via east: `black` goes red first (at 90°), `ranch` at 20°. At engine start the active route is `black` (fastest non-red); the 2013 crew was heading to the ranch.

| File | What it does |
|---|---|
| `config.py` | Every tunable number: thresholds, safety factor, worst-case values, travel factors, scenario (`"2013"` = SH5 everywhere except non-burnable; `"today"` = LF2025 `fuel.tif`) |
| `fire_model.py` | 1-h fuel moisture from temp/RH (Fosberg Table A, **verify before quoting**), pyrothermel spread in any direction (1° cache), §2.7 fallback if pyrothermel isn't installed (`"model": "fallback"` in the state). `arrival_min()` = minutes from now until a point is within 4 × flame length of the front, with the 1.5 safety factor, minus the time the fire has already burned |
| `margin.py` | Terrain sampling (DEM, slope, aspect, fuel), route loading, Tobler crew times (trail 1.0, off-trail by fuel group = §4.3 fallback, or a per-route `travel_factor`), per-route margin |
| `engine.py` | MQTT service: node health, conservative weather, fires, active route, alerts → `engine/state` every second (`RECOMPUTE_S = 1`) |
| `test_margin.py` | §7 checks, 14, all pass (re-run Nov 8 after the pull, model: pyrothermel) |
| `data/yarnell/placeholder/` | **Placeholder** `nodes.json` + `routes.geojson` (N1 on the real observation deck, everything else made up; tuned so the simulator's wind shift turns R1 red and reroutes to R2). The engine switches to `data/yarnell/nodes.json` / `routes.geojson` automatically once they exist, and reports `"placeholder_data": true/false` |
| `simulator.py` | New flags `--fire-after <s>` / `--fire-node <id>`: one simulated spot fire on `sensors/SIM/fire` at that node |
| `pyproject.toml` | New `engine` dependency group: numpy, pyproj, pyrothermel, rasterio |

**Rules as implemented:**
- **Weather** (one conservative value for the whole area): wind speed = highest reading (a stale node's last reading still counts); wind direction = freshest node that reports one; temp = highest, RH = lowest across fresh nodes.
- **Node health:** fresh < 20 s, aging ≥ 20 s, stale ≥ 30 s since the last message; a node never heard from = stale. Alert "Node <id> stale" on the change.
- **Stale node** → worst case per §5.3: wind speed `max(..., WORST_WIND_MS)`, wind direction unknown (head-fire rate in every direction), `WORST_TEMP_C`, `WORST_RH`.
- **Active route** = the fastest route that isn't red. It changes only when it turns red and another isn't ("REROUTED" alert). All red → no reroute, "NO SAFE ROUTE" alert naming the least-bad route.
- **Status:** §5.1 thresholds; "dropped > 30 % within 30 s" makes a green route amber and is held for 30 s so it doesn't flicker.
- **Fires:** `sensors/<src>/fire` (`node` or `lat/lon`), a `wx` message with `"flame": 1` (fire at that node), or `engine/cmd`. Within 30 m of an existing fire = the same fire (keeps its first time). Fuel at a fire point = most common burnable code in the surrounding 30 m.
- **Alerts:** route red, rerouted, no safe route, LEAVE NOW, node stale, active safety zone radius < 4 × flame length. Event alerts stay for 30 s.
- **Simulator data** (`"_simulated": true`) is ignored unless `--allow-sim` or `ALLOW_SIMULATOR_DATA=true`, as in `ingest.py` / `ws_server.py`.

**Run:**
```
uv sync --group engine                                   # once, needs internet (pyrothermel builds from source, ~35 s)
uv run --group engine python test_margin.py              # checks
uv run --group engine python engine.py                   # live (MQTT_HOST from .env)
# offline demo story, two more terminals (local broker on 127.0.0.1):
uv run --group engine python engine.py --allow-sim
uv run python simulator.py --fire-after 20 --wind-shift-after 60 --silent-after 90
```
Verified Sun Nov 8 with the placeholder data (local Mosquitto + simulator, ~30 ms per recompute): fire at 20 s → R1 (active) 22 min green, R2 92 min; wind shift at 60 s → `Route R1 RED: margin -15.5 min`, `REROUTED: R1 -> R2`; N3 silent → stale after 30 s → both routes red, `NO SAFE ROUTE`.

**`engine/state` message as published by v1** (one per second):
```json
{"type":"state","ts":1794117186.0,"scenario":"2013","model":"pyrothermel","placeholder_data":true,
 "weather":{"wind_ms":7.9,"wind_from":180.0,"temp_c":36.0,"rh":11.1,"m1h_pct":2,"notes":[]},
 "crew":{"lat":34.22146,"lon":-112.78221},
 "active_route":"R2","leave_by_ts":1794119000,
 "routes":[{"id":"R1","name":"…","to":"…","margin_min":-15.8,"status":"red","active":false,"eta_min":21.9,
            "worst":{"lon":-112.774,"lat":34.2185,"t_fire_min":6.1,"t_crew_min":21.9}},
           {"id":"R2","name":"…","to":"…","margin_min":61.7,"status":"amber","active":true,"eta_min":39.4,"worst":{…}}],
 "nodes":[{"id":"N3","label":"…","lat":34.2185,"lon":-112.787,"status":"fresh|aging|stale","age_s":12}],
 "fires":[{"id":"F1","lat":34.21,"lon":-112.771,"source":"SIM","age_s":105,"head_ros_m_min":309.6,
           "head_dir":354.3,"flame_m":24.7,"omni":false,"burnable":true}],
 "alerts":["REROUTED: R1 -> R2 (R1 margin -15.5 min, R2 60.1 min)"]}
```
- `eta_min` = crew walking time to the safety zone. `margin_min` = min over the route's points of (fire arrival − crew arrival), minutes; it is not a travel time.
- `margin_min` / `worst` are `null` when no fire threatens the route (status green). `worst` = the point that sets the margin: draw it on the map.
- Route geometry isn't sent: the dashboard draws `routes.geojson` and colours each route by `id` → `status`. Use the same file the engine loaded (real or placeholder).
- `leave_by_ts` = `now + margin − 2 min` for the active route (unix s; in the past = "LEAVE NOW" alert). `null` without a fire.
- `alerts` = current conditions plus events from the last 30 s.

**Gaps between v1 and the v2 plan** (§2.4, §4, §5, `docs/specs/escape-margin/spec.md`):

| Topic | v1 (implemented) | v2 (planned) |
|---|---|---|
| Routes | Hand-drawn LineStrings `R1`/`R2`, each evaluated alone | scipy Dijkstra over the 10 m grid + OSM trails; one escape route per safety zone (`ranch`, `black`) (ADR 0001) |
| Fire spread | Straight line from each fire point, no history | Minimum travel time on the grid with history (ADR 0002); starts from the black |
| Safety zones | Placeholder A/B with `sz_radius_m` | Boulder Springs Ranch (100 m) and the black |
| Fire trigger | `"flame": 1` in a `wx` message; 30 m merge; **hot-sensor rule done Nov 8 (ticket 17)**, hot nodes left out of temp/RH | Hot-sensor rule (≥ 45 °C or +8 °C in 30 s); at most one fire point per sensor node |
| Reset / ignite | `engine/cmd` | **Done Nov 8:** dashboard Start / Reset / Ignite at N2 → `POST /api/cmd` → `engine/cmd` (§5.7) |
| Wind | Highest wind across all nodes, unscaled; **each node's wind smoothed (20 s moving average, Nov 8)** | N1 only, × `WIND_SCALE` (4) |
| Moisture | Hottest / driest fresh node | Same, but a node with a fire point is excluded |
| Stale → wind direction unknown | When any node is stale | Only when N1 is stale |
| Aging | 2 missed heartbeats (20 s), fixed 10 s interval | 1 missed heartbeat at each node's own interval |
| Worst case | `WORST_WIND_MS` 10, `WORST_TEMP_C` 38, `WORST_RH` 8 | 13 m/s, 40 °C, 8 % (§5.3) |
| 2013 fuel | SH5 everywhere (burnable cells) | SH5 inside the 2013 perimeter only |
| Crew | **Done Nov 8 (differently):** the engine walks the crew along the active route from Start; a real `crew/+/pos` stops it | Scenario simulator walks the 2013 descent line from the saddle at 16:20 (dropped: the crew follows the plan) |
| `engine/state` | `path` per route (graph mode, Nov 8); no `areas`; fires have `age_s`; extra `weather`, `crew`, `worst`, `eta_min` | `path` per route, `areas` (black / burning / +5 / +10 min), fires with `ignition_ts` (§5.5) |

### 5.7 Demo-dashboard update (Sun Nov 8, ~05:00–06:50; Vivek)
Spec and tickets: `docs/specs/demo-dashboard/` (01–06, 09, 10, 13–15 resolved). Checks: `test_margin.py`, **30 pass**.

**Engine**
- **Phases.** The engine starts in `ready`: no `FireGrid`, routes planned without fire (margins `null`, green), fire inputs ignored, nobody moves. `engine/cmd {"cmd":"start"}` → `running`: the scenario clock (`config.SCENARIO_START` = 16:20) and the `FireGrid` start at that moment. `{"cmd":"reset"}` → `ready` (fires, history, events, crew position cleared). Replaces "the fire clock starts at the first sensor reading" (§8.6).
- **Active route.** Picked once, at the first running step after Start: the fastest route that isn't red (the black on the 2013 data). It then changes only when it turns red and another route isn't (`REROUTED`). Fixes the old rule that re-picked the fastest route every step while there were no spot fires. A ranch-first variant (`INITIAL_ROUTE = "ranch"`) was tried and dropped the same morning.
- **Wind smoothing.** N1's readings jump (fan gusts, vane). Per node, an exponential moving average with time constant `WIND_SMOOTH_S` = 20 s; direction averaged as a unit vector (350° and 10° → 0°); direction rounded to `WIND_DIR_STEP_DEG` = 5°, speed rounded up to 0.5 m/s. The fire model and `weather.wind_from` use the smoothed values. A sharp change takes ~30–40 s to show fully. Each 5° step still rebuilds the spread table (~250 ms on the laptop); raise the step to 10° if the Pi is slow.
- **Crew walk** (spec story 35, done differently). After Start the crew moves along the **active route** at the modelled walking times (`seg_s`), projected onto the route polyline each step; routes are then re-planned from the new position, so ETA and margins update as the crew walks. ~1.1 m/s on the black route (~330 m in 5 min; the black is ~19 min away). A real `crew/<id>/pos` message stops the simulated walk until Reset. "Crew reached <zone>" on arrival. `CREW_WALK` in `config.py`.
- **Command path.** `ws_server.py` `POST /api/cmd` (`start` / `reset` / `fire` only, else 400; 503 without MQTT) publishes the body to `engine/cmd`. No auth (closed FireNet). CORS open so the Vite dev server can call it.
- **`engine/state` additions:** `phase` (`ready`/`running`), `started_ts`, `scenario_start` ("16:20"), `replan_ts` (Start or last reroute); per route `t_crew_min` / `t_fire_min` (minutes from `ts` per `path` point; `null` before the crew's join point or where the fire never arrives; `t_fire_min` includes the 1.5 safety factor; min of the difference = `margin_min`); `crew.walking`, `crew.arrived`; per route `replanned_ts` (ticket 17). Adds ~1.5 KB to a ~60 KB message (`areas` is ~52 KB).

**Results on the 2013 data** (from the descent point; 60 s of SSW 2.5 m/s, then the shift; margins 1 / 3 / 5 min after):

| Wind from | ranch | black | Crew on the black → |
|---|---|---|---|
| 135–255° | green | green | nothing |
| 270–300° at 4 m/s; 285–315° at 7.5 m/s | red | green (+6 … +33) | **demo ending**: ranch cut, black safe |
| 315°+ at 4 m/s; 330° at 7.5 m/s | red | red / amber | no safe route (Q&A) |
| 60–120° at 4 m/s; 45–90° at 7.5 m/s | green | red | REROUTED black → ranch (Q&A) |
| 330–30° | red | red | no safe route |

Full run with a jumpy sensor (±25°, ±30 % speed every 2 s), smoothing and the crew walking: the ranch turns red ~30 s after the fan reaches 295°; the black stays at 12–83 min margin. The black can flicker amber at large margins (the "dropped > 30 %" rule, §5.1) while the wind turns.

**Dashboard** (`dashboard/src/App.tsx`): header Start / Reset (confirm) / Ignite at N2 + scenario clock + "N / 3 sensors live"; one Active escape route tile (name in status colour, crew walking, margin, ETA, leave-by in scenario time); map with a "Ready" box before Start, compass (top-right), Boulder Springs Ranch label + 100 m safety zone circle, 2013 deployment site; `sites.geojson` (post-2013 places) removed; tightest-point ring per route; hover popups on routes (crew / fire time and margin at that point) and sensor nodes (live reading); side panels: wind and fire spread (big arrow pointing where the wind pushes the fire), escape routes, alerts, map legend. The sensor feed panel and the four summary tiles are gone. Labels are HTML markers (MapLibre text needs a glyph server, which FireNet doesn't have). Route colours unchanged (§5.1).

**Developing the dashboard locally** (no Pi deploy per change): `./run_demo.sh --dir-after 295 --wind-shift-after 45 --turn-s 150` (Docker broker + engine + bridge + simulator; dashboard at http://127.0.0.1:8000; press Start within 45 s) plus `cd dashboard && npm run dev` (http://localhost:5173, reloads on save). Against the Pi's live data: `VITE_WS_URL=ws://10.42.0.1:8000/ws VITE_API_URL=http://10.42.0.1:8000/api/cmd npm run dev`.

**Open (decide with the team):** `WORST_WIND_MS = 10` with an unknown direction is already harsh: in dry SH5 any stale node turns every placeholder route red (head ROS ~480 m/min). Lower it, or use e.g. 1.5 × the current max, if the demo should show "margin drops" rather than "no safe route". The v2 plan raises it to 13.

---

## 6. Data model (PostGIS database `fire` on the Pi)
- `nodes(id, geom, label)`: positions from the fake-GPS node config (§1.4)
- `readings(...)`: one row per `wx` message plus the Pi's receive time. **Columns not fixed yet** (`ingest.py` defines them)
- `fire_points(...)`: from `sensors/<src>/fire`
- `trails(..., dz, cost, reverse_cost, scouted bool, no_go bool)`
- `grid_nodes(id, row, col, geom, elev, veg_factor)` and `grid_edges(...)`: optional SQL copy of `graph.npz` (routing runs in memory, §4.6)
- `evc`, `evh` rasters (or just `veg_factor` on `grid_nodes`)
- `dem`, `slope`, `aspect`, `fuel` raster tables (EPSG:32612)
- `safety_zones(id, geom, radius_m)`: the ranch (radius checked against 4 × flame length) and the starting black polygon

### 6.1 Implemented MVP schema (local; deploy unchanged to Pi after review)

`schema.sql` now defines the following initial columns. This does not replace the planned routing/static-data tables above; it is the live sensor-ingestion foundation.

- `nodes(id TEXT PRIMARY KEY, label, x_cm, y_cm, geom geometry(Point,32612), last_seen)`. Positions now come from fake GPS (§1.4): fill `geom` from the node config; `x_cm`/`y_cm` are unused and can be dropped at the next schema change.
- `readings(id, node_id, seq, temp_c, rh, wind_ms, wind_dir, flame, batt, received_at, raw JSONB)`. `received_at` is the database receipt time. `(node_id, seq)` is unique, making the current weather message insert idempotent across reconnects.
- `fire_points(id, source, x_cm, y_cm (unused now, §1.4), confidence, geom geometry(Point,32612), received_at, raw JSONB)`. The table exists but `ingest.py` deliberately does not persist fire events yet.

Apply on the Pi after setting `DATABASE_URL` in `.env`:

```bash
set -a; . ./.env; set +a
psql "$DATABASE_URL" -f schema.sql
```

---

## 7. Checks to keep (one runnable file each, asserts only)
**Fire** (already in `pyrothermel_demo.py`):
- Wind from 225 → head toward 45.
- Direction-of-interest at the head equals the head rate.
- Head > flank > back, and the ellipse is symmetric.
- No wind on flat ground gives a circle.
- More wind is faster, wetter is slower, upslope is faster.
- Downwind is reached first.

**Wind bridge** (`python3 sdr/wind_bridge.py --test`, passes):
- km/h → m/s conversion, direction passthrough, battery mapping.
- A temp/RH packet keeps the last wind (and the other way round).
- Other 433 MHz devices are ignored.

**Routing** (to write):
- Uphill cost > downhill cost (100 m / +10 m ≈ 101 s vs 71 s).
- The grid route detours around a barrier.
- No diagonal corner-cutting past an impassable cell.
- A trail is preferred over a parallel chaparral path.

**Fire spread** (to write):
- After a wind shift, the fire turns from where it already reached (cells reached before the shift keep their arrival times).
- Downwind cells are reached before upwind cells.
- A cell is burning for 5 min, then black.

**Margin** (to write for v2):
- A route the fire cuts mid-way is red even when its safety zone is still reachable.
- Rerouting picks the other safety zone when the ranch route is cut.
- A stale sensor node goes grey and its worst-case values shrink the margin.
- A hot sensor node (≥ 45 °C) creates exactly one fire point.

**v1 engine** (`uv run --group engine python test_margin.py`, 14 checks, all pass, §5.6):
- Fire: moisture table ordering; downwind fastest and unknown wind direction gives a circle; non-burnable never arrives; arrival shrinks as the fire ages and the 4 × flame buffer applies; fallback model shape.
- Crew: Tobler uphill 101 s vs downhill 71 s per 100 m / 10 m; a route walked in reverse costs differently.
- Margin: no fire → all green, shortest route active; a route cut mid-way is red even if its safety zone is reachable; wind shift turns R1 red and reroutes to R2; a stale node goes stale, applies worst case and shrinks the margin; reset clears fires; a flame reading creates one fire at the node; the state is JSON-serialisable.

---

## 8. Base station, network, nodes

### 8.1 Raspberry Pi 5
Raspberry Pi OS based on Debian 13 "Trixie" (64-bit, aarch64), user `thunder`, with Mosquitto and PostgreSQL 17 installed and active.
```
sudo apt install -y mosquitto mosquitto-clients \
  postgresql postgis postgresql-17-postgis-3 postgresql-17-pgrouting \
  python3-venv git
```
Mosquitto config `/etc/mosquitto/conf.d/local.conf` (verified listening on `0.0.0.0:1883` and `[::]:1883`, enabled at boot):
```
listener 1883
allow_anonymous true
```
**Python project (uv)** at `/home/thunder/fire`: `uv init --python 3.13`, env in `.venv`, deps in `pyproject.toml` + `uv.lock`. Intended libraries: paho-mqtt, pyserial, fastapi, uvicorn[standard], websockets, psycopg[binary], shapely, numpy, scipy, rasterio, pyrothermel (§2.1). Check with `uv pip list`; teammates reproduce with `uv sync`; run with `uv run python <script>.py`. Files: `pyproject.toml`, `uv.lock`, `README.md`, `src/`, `simulator.py`.
**Pi tips:** official 5V/5A (27 W) supply; video only via the micro-HDMI port nearest the USB-C power port; the edge power button shuts down (one press) and starts (press again, LED red → green).

### 8.2 Network
- **Venue Wi-Fi isolates devices** (laptop and Pi on venue Wi-Fi could not ping each other). Nothing on the venue Wi-Fi can reach the Pi.
- **FireNet hotspot (on):** SSID `FireNet`, password `firenet123`. Pi = **`10.42.1.1`**. The ESP32 node and the wind bridge publish here. Started with `sudo nmcli device wifi hotspot ssid FireNet password firenet123`. It takes the Pi off venue Wi-Fi (no internet), so install packages first. FireNet itself has no internet either.
- **Direct Ethernet:** Pi = `10.42.0.1` (`ipv4.method shared`; the laptop gets an address automatically). One-time on the Pi:
  ```
  sudo systemctl enable --now ssh
  sudo nmcli con add type ethernet ifname eth0 con-name direct ipv4.method shared
  sudo nmcli con up direct
  ```
  Connect: `ssh thunder@10.42.0.1` (password: ask Vivek; reset with `sudo passwd thunder`). Passwordless: `ssh-copy-id thunder@10.42.0.1` (don't overwrite your key with `ssh-keygen`); from Windows PowerShell: `type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh thunder@10.42.0.1 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"`. WSL and Windows keep separate keys; VS Code uses the Windows one.
- **VS Code Remote-SSH:** "Remote - SSH" extension → F1 → Remote-SSH: Connect to Host → `thunder@10.42.0.1` → Open Folder `/home/thunder/fire`. Install the Python extension "in SSH" and select `./.venv`.

### 8.3 ESP32 nodes (N2, N3)

#### 8.3.1 N2: XIAO ESP32-S3
**Node N2 is a XIAO ESP32-S3** with the PlatformIO firmware in `firmware/weather-node` (DHT11 on D3, LED on D4, node ID `N2`, Wi-Fi FireNet → `10.42.1.1`). Build/flash: `cd firmware/weather-node && pio run -t upload && pio device monitor`. At boot it prints FireNet's signal strength, and while connecting the Wi-Fi status (1 = network not seen, 4 = connect failed / password, 6 = disconnected / retrying). **The XIAO ESP32-S3 needs its U.FL antenna attached:** without it FireNet showed −82 dBm and it never connected (status 6); with it −35 dBm and it connected at once.

#### 8.3.2 N3: XIAO ESP32-C5 (Vivek, Sat Nov 7 evening)
**Status:** **working end to end.** DHT11 read, Wi-Fi + MQTT heartbeat with real `temp_c`/`rh` received on the Pi broker (verified Sat Nov 7, late evening):
```
sensors/N3/wx {"type":"wx","node":"N3","seq":19,"temp_c":23.8,"rh":48.8,"wind_ms":-1,"wind_dir":-1,"flame":-1,"batt":-1}
```

**Toolchain: PlatformIO project in `firmware/`** (open the `firmware` folder itself in VS Code; `firmware/weather-node` is a separate project for N2).
- Platform: Seeed's `https://github.com/Seeed-Studio/platform-seeedboards.git#1.1.1` (arduino-esp32 core 3.3.7), board `seeed-xiao-esp32-c5`. The official PlatformIO `espressif32` platform does not support the C5. The board definition already sets `ARDUINO_USB_MODE=1` and `ARDUINO_USB_CDC_ON_BOOT=1`, so `Serial` goes over USB-C with no extra flags.
- One-time install: `pio pkg install -g -p "https://github.com/Seeed-Studio/platform-seeedboards.git#1.1.1"`. The first build downloads ~3–4 GB.
- Linux, one-time: install PlatformIO's udev rules (they include Espressif `303a:1001`) and `sudo usermod -aG dialout $USER`, then log out and back in.
- Each test is its own environment with its own folder in `firmware/src/`. Run it from the PlatformIO sidebar: *env → General → Upload and Monitor*.

| Env | Purpose | Result (Sat Nov 7) |
|---|---|---|
| `c5_alive` | Smoke test: chip info + uptime over USB | ✅ ESP32-C5 rev 1.0, 8 MB flash, 8 MB PSRAM, core 3.3.7 |
| `lib_check` | Compile-only: all libraries build for the C5 | ✅ builds (DHT, DHTesp, PubSubClient, ArduinoJson, WiFi) |
| `led_test` | Blink the Grove LED module on **D1** | ✅ blinks |
| `led_test_d0` | Same LED test on **D0**, to check the DHT11 wiring by swapping the LED module onto the DHT11's cable | Not run yet |
| `dht_diag` | DHT11 line probe (no library, no Wi-Fi): pull-up check on every free pin, pull-down check on D0, then a start pulse on D0 and a count of the sensor's reply edges (~84 = healthy) | ✅ found the faulty first sensor and the timing bug (below) |
| `node_n3` | **The real node:** reads the DHT11 every 3 s, heartbeat to `sensors/N3/wx` every 10 s | ✅ real temp/RH on the Pi broker |

**Wiring** (USB-C at the top, XIAO straddling the centre gap, its top pin pair in breadboard **row 1**, so pins occupy rows 1–7):

| Module | Wire | XIAO pin | Breadboard row |
|---|---|---|---|
| DHT11 (Grove "Temperature&Humidity Sensor v1.2") | Yellow (SIG) | **D0** = GPIO1 | row 1, left side |
| | Black (GND) | **GND** | row 2, right side |
| | Red (VCC) | **3V3** (never 5V) | row 3, right side |
| | White (NC) | not connected | — |
| Grove LED module (optional status light) | Yellow (SIG) | **D1** = GPIO0 | row 2, left side |
| | Black / Red | GND / 3V3 | rows 2 / 3, right side (shared with the DHT11) |

⚠ **Row 1 on the right side is 5V.** Nothing goes there.

- Pin map: D0 = GPIO1, D1 = GPIO0, D2 = GPIO25 (strapping, avoid), D3 = GPIO7 (strapping, avoid), D4 = GPIO23 (SDA), D5 = GPIO24 (SCL), D6 = GPIO11 (TX); onboard LED = GPIO27. **Avoid strapping pins GPIO2, 3, 7, 25, 26, 27, 28.** Confirmed against the core's `variants/XIAO_ESP32C5/pins_arduino.h`.

**`node_n3` behaviour** (`firmware/src/node_n3/main.cpp` + `dht11.h`):
- **Sensor:** reads the DHT11 on D0 every 3 s with our own driver (`dht11.h`, no library) and prints `dht: temp 23.8 C  humidity 48.8 %`. This runs whether or not Wi-Fi is up, so the sensor can be tested on USB alone. A failed read prints which step failed (e.g. `no response`, `stuck LOW in response`, `checksum mismatch`).
- **Network:** Wi-Fi FireNet → MQTT `10.42.1.1:1883`, client id `N3`, topic `sensors/N3/wx`, §1.2 format. A heartbeat goes out right after connecting, then every 10 s. The network code never blocks the sensor loop for more than ~2 s.
- **No data:** if there's no good reading in the last 15 s, the heartbeat **still goes out**, with `temp_c -99` and `rh -1`, so the node stays alive and the sensor shows "no data". (N2 skips the heartbeat instead, which makes the node look dead.)
- **`seq`** counts every heartbeat, including ones made while offline, so a gap on the Pi = missed heartbeats. It restarts at 0 when the board reboots (opening the serial port can reboot it).
- **Serial copy:** every heartbeat is also printed as `payload sensors/N3/wx {…}`, followed by `sent over Wi-Fi` or `(offline: not sent over Wi-Fi)`. This is what the USB bridge below uses.
- The boot-time FireNet scan (signal strength printout) was dropped in the rewrite. The `mqtt: connecting … rssi` line still shows signal strength once connected.
- Status LED on D1: blinking once per second = connecting; short flash every 10 s = heartbeat delivered.

**What worked (verified Sat Nov 7):**
- Upload and USB serial on Linux (`/dev/ttyACM0`, sometimes `/dev/ttyACM1` after a re-plug).
- `led_test` on D1.
- `node_n3`, first run (before the DHT11 fix): `wifi: saw FireNet rssi -36 dBm ch 11` → connected in ~6 s → IP `10.42.1.64` → `mqtt: connected` → `sensors/N3/wx` received on the Pi (`mosquitto_sub -h localhost -t 'sensors/N3/#' -v`), `seq` continuous (10 → 17).
- `node_n3` with the DHT11 fix, late evening: every read succeeds (~24 °C, ~48 % RH); `mqtt: connected` (IP `10.42.1.64`, −34 dBm) → `sent over Wi-Fi` every 10 s → seen on the Pi next to N2. After a reboot, Wi-Fi took **~40 s** to rejoin (`status 6` until then), so N3 may grey out briefly on the Pi after each reboot.

**DHT11 on N3: what was wrong (solved Sat Nov 7)**

There were three separate problems:
1. **First sensor faulty (hardware).** D0 read LOW even with the chip's internal pull-up on, while every other free pin (D1–D10) read HIGH, and the line never moved after a start pulse. A working module leaves the line HIGH (the Grove board has its own 10k pull-up). **Swapping in a new DHT11 fixed it:** D0 then read HIGH even with the internal pull-*down* on, meaning the module is powered and connected. The swap changed the module and its connections at once, so we can't tell whether the old module was dead or just not getting power.
2. **The code started listening too late (software, the main bug).** The DHT11 replies ~14 µs after the start pulse ends, and the whole reply lasts ~4 ms. Switching the pin with Arduino `pinMode(OUTPUT)` → `pinMode(INPUT_PULLUP)` is too slow on the C5 (arduino-esp32 3.3.x), so the reply was over before the code started listening: `no response`, even with good wiring. The Adafruit DHT library switches the pin the same way, which most likely explains why it never worked here either. **Fix:** drive the pin in **open-drain** mode with the ESP-IDF driver (`gpio_set_direction(pin, GPIO_MODE_INPUT_OUTPUT_OD)`, then `gpio_set_level(pin, 0/1)` and `gpio_get_level`). The pin stays readable, so listening starts instantly. Same sensor, same wiring: `pinMode` switching = 0 reply edges, open-drain = 84 (a full reply).
3. **FireNet hotspot was off (setup).** The broker was running, but the board had no network to join (`status 6`). Turned back on with `sudo nmcli device wifi hotspot ifname wlan0 ssid FireNet password firenet123 band bg`; the board joined by itself.

Library notes: Adafruit DHT and DHTesp (beegee_tokyo) are both still in `lib_deps` (used by `lib_check`), but `node_n3` no longer uses either. Don't use them for a DHT on the C5; reuse `firmware/src/node_n3/dht11.h`. The old `dht_diag`'s "freeze after `step 2`" was probably the library failing plus lost USB output; it was replaced by the line probe above. `analogReadMilliVolts()` puts the pin in ADC mode and drops the pull-up, so it can't measure a pulled-up line.

**Testing the MQTT side without FireNet (USB bridge)**

`firmware/tools/serial_bridge.py` reads the `payload …` lines from the board's USB port and publishes them to a broker (needs only pyserial, which comes with PlatformIO). It's for checking message format / backend / dashboard when FireNet is off. Close `pio device monitor` first: only one program can use the serial port.
```
# local broker on the laptop (Docker), plus a subscriber
docker run -d --name mqtt-local -p 1883:1883 eclipse-mosquitto:2.0.20 mosquitto -c /mosquitto-no-auth.conf
docker exec -it mqtt-local mosquitto_sub -t 'sensors/#' -v
cd firmware && ~/.platformio/penv/bin/python tools/serial_bridge.py               # -> localhost
cd firmware && ~/.platformio/penv/bin/python tools/serial_bridge.py --host 10.42.0.1  # -> Pi broker over the cable
docker rm -f mqtt-local   # when done
```
Verified Sat Nov 7: three N3 heartbeats arrived at the local broker 10 s apart. **Don't point the bridge at the Pi while N3 is also on FireNet**, or every message arrives twice.

**Harmless messages (ignore):**
- Boot log `E (1019) MSPI Timing: Failed to allocate dummy cacheline for PSRAM memory barrier!`. PSRAM is still detected (8 MB), and nothing uses PSRAM.
- Compiler warning `invalid suffix on literal` in `periph_ctrl.h` (from Espressif's own headers).
- `esptool` "Deprecated: Choice 'default_reset'" warnings.

**Lessons**
- **Attach the U.FL antenna**, as on N2.
- **Timing-critical one-wire sensors (DHT11/22) on the C5: use open-drain via the ESP-IDF gpio driver, not `pinMode()` switching** (see "DHT11 on N3" above).
- **A Grove LED lighting up proves SIG and GND only**, not VCC: the LED module runs from SIG alone. To check a sensor's power, use `dht_diag` (D0 HIGH with pull-down = module powered) or a multimeter (3.3 V red ↔ black at the sensor).
- `Could not exclusively lock port /dev/ttyACM…` = an old *Upload and Monitor* terminal still holds the port. Press **Ctrl+C** in every old monitor before uploading again.
- `Auto-detected: /dev/ttyS0` = the board is **not on USB** (cable unplugged or loose). Check `ls /dev/ttyACM*`.
- The port can renumber (`ttyACM0` ↔ `ttyACM1`) after a re-plug. PlatformIO auto-detects it.
- Earlier Arduino IDE lessons (C5): `DHTPIN 25` (D2, strapping) caused "Sensor reading failed". Grove-to-Grove cables don't fit the XIAO, so use Grove-to-male-jumper. A XIAO on one half of the breadboard shorts left and right pins. Upload stuck: hold BOOT, tap RESET, release BOOT, upload again.

### 8.4 Simulator
`~/fire/simulator.py` on the Pi pretends to be N1–N3, so backend and dashboard can be built before the hardware. Code in Appendix A. Every 10 s it publishes one heartbeat per node to `sensors/<node>/wx`, and acts out the two demo situations:
- **Wind shift** after `--wind-shift-after` s: direction 200° turning clockwise to `--dir-after` (default 20°; **295° for the current demo story**, §5.7) over `--turn-s` (was 270° → 180°), speed ~2 → 7.5 m/s, temp +4 °C, RH −5%.
- **Silent node** after `--silent-after` s: `--silent-node` (default N3) stops sending.
```
mosquitto_sub -h localhost -t 'sensors/#' -v
cd ~/fire && uv run python simulator.py --wind-shift-after 30 --silent-after 60
```
Verified: seq 0–2 calm at 270°, ~2 m/s; seq 3 (t = 30 s) 180°, ~7.5 m/s, temp up, RH down; N3 stops after 60 s. For local dev, install Mosquitto on your laptop and run the same script; topics and format are identical.
- **Spot fire** (added Nov 8 for the v1 engine, §5.6; **testing only, not the demo story**): `--fire-after <s>` publishes one fire point to `sensors/SIM/fire` at `--fire-node` (default N2); `-1` = never. Messages in the repo's `simulator.py` carry `"_simulated": true`.

### 8.5 WebSocket server → dashboard contract
`ws_server.py` (FastAPI) subscribes to `sensors/#`, `engine/#`, and `crew/#` and pushes every message to the dashboard. The implementation is now in the repository root; the Appendix B code is retained as the original reference. Each WebSocket message:
```json
{"topic": "sensors/N1/wx", "rx_ts": 1762554000.12, "data": {"type": "wx", "node": "N1", "seq": 4, "temp_c": 30.8, "rh": 16.2, "wind_ms": 2.1, "wind_dir": 270, "flame": 0, "batt": 90}}
```
`rx_ts` = server receive time (Unix s). On connect, the server first sends the latest message per topic, so a reloaded dashboard isn't empty.
Run: `uv sync` then `uv run uvicorn ws_server:app --host 0.0.0.0 --port 8000`.
Test in a browser console: `const ws = new WebSocket("ws://localhost:8000/ws"); ws.onmessage = e => console.log(JSON.parse(e.data));`
On the Pi, the dashboard connects to `ws://10.42.1.1:8000/ws` (FireNet) or `ws://10.42.0.1:8000/ws` (cable).
`http://127.0.0.1:8000/api/sensors/latest` is an HTTP inspection endpoint, not the WebSocket itself. Navigating to `ws://.../ws` in a browser address bar is not a valid WebSocket test.

### 8.6 Dashboard deployment

The React/MapLibre dashboard lives in `dashboard/`. It renders the local Yarnell hillshade, perimeter and site data, live sensor cards, placeholder R1/R2 routes, route state, alerts and a leave-by time. It uses `ws://<current-host>:8000/ws` by default, so it works unchanged in local development and when opened from the Pi on FireNet.

Before starting `ws_server.py` on the Pi, build the frontend once:

```bash
cd ~/fire/dashboard
npm install
npm run build
```

`ws_server.py` serves the generated `dashboard/dist` at `http://10.42.1.1:8000`; the same service provides the WebSocket at `/ws`. Do not commit `dashboard/dist`; build it on the Pi after `git pull`.

**Integrated with the engine (Nov 8):** the map draws everything from `engine/state`: routes from each route's `path` (coloured by status, active one thicker), sensor nodes from `nodes` (fresh/aging/stale), spot fires, the crew, and the fire `areas` (black, burning, +5 / +10 min fronts; added to `engine/state` per §5.5 by `margin.fire_areas`, ~15 KB, ~30 ms). The hardcoded R1/R2 lines are gone. Reference layers: hillshade (corners from `meta.json`), 2013 final perimeter (dashed), 2013 descent line (dotted). Fixes: MapLibre v6 loads its worker from a separate file that the Vite build didn't ship, so no GeoJSON layer drew (now `setWorkerUrl` to the bundled worker); `package.json` pinned to exact versions (was `latest`); the WebSocket reconnects every 2 s. The engine's fire clock (16:20) now starts at the first sensor reading, not at engine start (it used to run on worst-case weather until the first reading and kept that history). **Superseded the same morning: the clock starts on the dashboard's Start button (§5.7).** Layout as of ~06:40: §5.7. Local run: `./run_demo.sh`. Simulator wind: `--dir-before 200` turning clockwise to `--dir-after 20` over `--turn-s` after `--wind-shift-after`.

---

## 9. Next steps, open questions, gotchas

**Next steps**
- [x] ESP32-S3 node: on FireNet, publishing heartbeats (as `N2`, verified Sat Nov 7)
- [x] ESP32-C5 node `N3`: PlatformIO setup, on FireNet, heartbeats on the Pi broker (verified Sat Nov 7)
- [ ] N1 (La Crosse): fan test for direction, measure transmit interval, move SDR + bridge to the Pi (§1.3)
- [x] Local PostGIS schema + `ingest.py` (MQTT → database) implemented and verified with simulator data
- [x] `ws_server.py` working locally against the simulator; latest-message HTTP cache verified
- [ ] Deploy the committed schema, `ingest.py`, and `ws_server.py` to the Pi; verify real MQTT messages reach both the database and WebSocket dashboard
- [x] Staleness (fresh / aging / stale) + worst-case values, inside the v1 margin engine (§5.3, §5.6)
- [x] v1 margin engine on hand-drawn routes, `engine/state` format documented (§5.6); placeholder data until `nodes.json` / `routes.geojson` exist
- [x] RH/temp → 1-h fuel moisture table (`fire_model.py`; verify the table against the source)
- [x] v1 engine on real `nodes.json` + graph-exported `routes.geojson` (Nov 8; `uv.lock` conflict markers fixed)
- [ ] Move the v1 engine toward v2: see the gap list in §5.6 and track item 8 below
- [x] Coordinates decided: fake GPS per node, no tabletop transform (§1.4)
- [x] Margin engine, fire spread and routing design settled (Nov 8 grilling; `docs/GLOSSARY.md`, `docs/adr/`, spec in `docs/specs/escape-margin/`)
- [ ] Map + routing track: see the ordered list below
- [ ] Build pyrothermel on the Pi 5
- [x] Download LANDFIRE + 3DEP DEM for the Yarnell patch, reproject to EPSG:32612 (§3.1)
- [ ] Questions for the operator mentor: travel speed in chaparral vs trail (calibrate the vegetation table in §4.3), acceptable margin thresholds, a quote for the pitch

**Map + routing track (suggested order; 3–5 can run in parallel)**
1. [x] **Pull 2013 facts from the SAIT report** (done Nov 8, §3.2; still to draw: the black polygon + descent line) (§10; ADOSH report as cross-check, opens in a browser): Boulder Springs Ranch location (the 2013 safety zone, not in OSM), the crew's starting position on the ridge, the route they took, and the wind-shift timeline (time, direction, speed). Add the points to `data/yarnell/sites.geojson` with the report page as source; the timeline goes in a scenario file for the simulator.
2. [x] **Routing crop decided** (centre ≈ 34.221, −112.777, §1.4); pick each sensor node's exact lat/lon (N1 ridge near saddle, N2 box canyon, N3 canyon→ranch). Write a node config (e.g. `data/yarnell/nodes.json`: id → lat/lon, label); `ingest.py` fills `nodes.geom` from it.
3. [x] **Hand-draw the black (~16:00) and the 2013 descent line** → `data/yarnell/scenario.geojson`; scenario simulator walks the crew from the saddle at 16:20.
4. [x] **Vegetation onto the grid:** EVC/EVH → `veg_factor` with the §4.3 lookup table; `config.py` flag for the 2013 replay (SH5 fuel + factor 0.1 inside the 2013 perimeter).
5. [x] **Routing engine: scipy** (§4.6, ADR 0001).
6. [x] **`build_graph.py`:** grid + OSM trails + connector edges for the crop → `graph.npz` (§4.1, §4.6), with the routing checks in §7 (uphill > downhill, detours around barriers, no corner-cutting, trail preferred over brush).
7. [ ] **Load into PostGIS on the Pi:** rasters with `raster2pgsql`, GeoJSON with `ogr2ogr` or psycopg (§3.1).
8. [ ] **Margin engine (Person A joins Vivek here, Nov 8):** in order (a) ~~`black0` cells in `graph.npz`~~ done Nov 8 (2,581 cells; descent point → black0 18.8 min, asserted in `build_graph.py`); (b) ~~engine routes over `graph.npz`~~ done Nov 8: `margin.load_graph` / `GraphTerrain` (terrain/fuel from the graph, no "SH5 everywhere" override) / `graph_routes` (crew cell → `ranch` / `black0` with `route()`, `seg_s` = the graph's edge costs, re-planned when the crew changes cell), `path` per route in `engine/state`; checks in `test_margin.py` (16 pass); 8 ms per recompute, 35 ms with a re-plan; (c) ~~fire spread from the black over the grid (§2.4)~~ done Nov 8, two open questions (§5.6 "fire spread" update); (d) ~~closures + reroute loop (§5.4)~~ done Nov 8 (§5.6 "closures" update); (e) `engine/state` per §5.5, with the fire and margin checks in §7 (`areas` done; Start / phases / per-point times / crew walk added, §5.7). v1 (§5.6) is for testing only; `routes.geojson` (`build_graph.py --routes`) feeds it meanwhile.
9. [x] **Dashboard map layers** (Nov 8, §5.7; still to do: coloured terrain / fuel backdrop, demo-dashboard tickets 07–08): `hillshade.png` (corners in `meta.json`), 2013 perimeter, sites, sensor nodes (age, grey when stale), the black / burning / +5 / +10 min fronts, escape routes coloured by margin, alerts, leave-by countdown, "ignite" and "reset" buttons.
10. [x] ~~**Scenario simulator:** publish the crew position walking the 2013 descent line~~ Done differently Nov 8: the engine walks the crew along the active route after Start (§5.7).

**Housekeeping**
- [ ] Once fire spread + closures run on the graph (track 8c–d): delete `build_graph.py --routes` / `export_routes()`, `data/yarnell/routes.geojson` and the `travel_factor` path in `margin.load_routes` (the v1 placeholder checks still use `load_routes`).
- [ ] Move `docs/USGS_13_n35w113.tif` (440 MB) to `data/raw/` and add `data/raw/` to `.gitignore`.
- [ ] Switch `fetch_yarnell.py` to read the local DEM tile and LANDFIRE download; keep the online services as a cross-check (§3.2).
- [ ] Mark north on the floor where the wind sensor sits (fan directions must match compass directions; §1.3, §1.4).
- [ ] Verify the vegetation-height safety zone formula (Butler 2014 / NWCG) against the source before using it (§2.5).
- [ ] `schema.sql`: drop the unused `x_cm`/`y_cm` columns at the next schema change (§6.1).

**Open questions**
- Resolved Nov 8: node positions and crop (§1.4), routing engine (scipy, §4.6), demo scenario (**2013 replay only**; LF2025 fuels stay a config flag), worst-case numbers (§5.3).
- Tune on the day: hot-sensor rule (45 °C / +8 °C in 30 s), `WIND_SCALE`, `RANCH_RADIUS_M`.
- N1 (La Crosse) transmit interval, and whether its `wind_dir` is "from" (§1.3).
- `batt` units: the simulator sends 90 (percent?); N1 (La Crosse) sends 100/10 for ok/low; N2 and N3 send -1. Proposal: percent 0–100, -1 = unknown (all current senders already fit). Confirm what the dashboard expects.
- Is pre-written code / pre-built hardware allowed? Ask the organizers.
- Project name: the team doc says "FireGuard", this repo says "LCES Live".

**Gotchas**
- Never put 5 V on an ESP32 pin; power sensors from 3V3. Avoid strapping pins GPIO2, 3, 7, 25, 26, 27, 28.
- Venue Wi-Fi isolates devices: use FireNet (`10.42.1.1`) or the cable (`10.42.0.1`).
- FireNet has no internet, and turning it on takes the Pi off venue Wi-Fi. Install packages first (Pi and laptop).
- New terminals on the Pi: `cd ~/fire` and use `uv run`, or activate `.venv`.
- **Don't run the simulator alongside the real nodes:** it also publishes `sensors/N1/wx`, `sensors/N2/wx` and `sensors/N3/wx`. `ingest.py` and `ws_server.py` ignore its messages (`_simulated`), but `mosquitto_sub` and any other subscriber still see them mixed in.
- DHT11 on the ESP32-C5: Arduino `pinMode()` switching is too slow and misses the sensor's reply; use the open-drain driver in `firmware/src/node_n3/dht11.h` (§8.3.2).
- ESP32 serial port "busy" / "could not exclusively lock": an old PlatformIO monitor still holds it. Ctrl+C every old *Upload and Monitor* terminal first (§8.3.2).
- `mosquitto_sub` looks stuck when nothing is publishing; it is just waiting (Ctrl+C to stop).
- paho-mqtt 2.x needs `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)`.
- `docs/USGS_13_n35w113.tif` is 440 MB: don't commit it (move to `data/raw/`, gitignore).
- SDR: only one program can hold the dongle (`usb_claim_interface error -6`); the La Crosse id changes on battery swap (§1.3).

---

## 10. Sources
**Fire spread**
- Andrews (2018), *The Rothermel surface fire spread model and associated developments*, RMRS-GTR-371. https://www.fs.usda.gov/rm/pubs_series/rmrs/gtr/rmrs_gtr371.pdf
- Rothermel (1972), INT-RP-115. https://www.fs.usda.gov/rm/pubs_int/int_rp115.pdf
- Rothermel (1983), *How to predict the spread and intensity of forest and range fires*, INT-GTR-143 (moisture tables). https://www.fs.usda.gov/rm/pubs_int/int_gtr143.pdf
- Anderson (1983), *Predicting wind-driven wild land fire size and shape*, INT-RP-305.
- Van Wagner (1969), "A simple fire-growth model", *Forestry Chronicle* 45(2).
- Cruz & Alexander (2019), "The 10% wind speed rule of thumb…", *Annals of Forest Science* 76:44.
- Finney (1998), FARSITE, RMRS-RP-4. Finney (2002), "Fire growth using minimum travel time methods", *CJFR* 32.
- Scott & Burgan (2005), *Standard fire behavior fuel models*, RMRS-GTR-153.

**Firefighter safety and travel**
- Butler & Cohen (1998), safety zone separation distance (4 × flame height), *IJWF*.
- Beighley (1995), "Beyond the safety zone: creating a margin of safety", *Fire Management Notes* 55(4).
- Cova, Dennison et al. (2005), "Setting wildfire evacuation trigger points using fire spread modeling and GIS", *Transactions in GIS* 9(4).
- Campbell, Dennison & Butler (2017), LiDAR-based travel rates for escape-route mapping, *IJWF*; and crowdsourced slope/travel-rate model, *Applied Geography*.
- Tobler (1993), hiking function.

**Yarnell Hill 2013 (for the ranch, crew route, positions and wind timeline)**
- Arizona State Forestry, *Yarnell Hill Fire Serious Accident Investigation Report* (SAIT), Sept 23, 2013. The official state report: timeline, maps, weather, deployment site. Mirror on the Wildland Fire Lessons Learned Center (verified downloadable, 6.6 MB): https://lessonslearned-prod-media-bucket.s3.us-gov-west-1.amazonaws.com/s3fs-public/irdoc/Yarnell_Hill_Fire_Investigation_Report.pdf
- Wildland Fire Associates for ADOSH, *Granite Mountain IHC Entrapment and Burnover Investigation*, Dec 2013. Independent occupational-safety investigation; more critical of the agency; own timeline, weather data and maps. ASU library copy (opens in a browser, blocks scripts): https://prism.lib.asu.edu/system/files/c16/106328/Granite_Mountain_IHC_Entrapment_and_Burnover_Investigation.pdf
- AZ DFFM press release on the SAIT report: https://dffm.az.gov/node/96
- *Yarnell Hill Staff Ride* (State of Arizona, interagency, since 2017): field-study guide with site-by-site detail; obtained by Phoenix New Times via public records, no confirmed public copy yet.
- Positions: use the reports' maps cross-checked against `data/yarnell/` (hillshade, perimeter, OSM sites). If the two reports disagree, note both.

**Software and data**
- pyrothermel: https://github.com/j-tenny/pyrothermel · Behave: https://github.com/firelab/behave
- ELMFIRE (field upgrade): https://github.com/lautenberger/elmfire
- LANDFIRE: https://landfire.gov · USGS National Map: https://apps.nationalmap.gov/downloader
- rtl_433 La Crosse TX141/TX145wsdth decoder: https://github.com/merbanan/rtl_433/blob/master/src/devices/lacrosse_tx141x.c
- La Crosse TX145WSDTHv3: https://www.lacrossetechnology.com/products/tx145wsdthv3

---

## Appendix A: `simulator.py` (on the Pi at `~/fire/simulator.py`)
```python
import json, time, random, argparse
import paho.mqtt.client as mqtt

p = argparse.ArgumentParser()
p.add_argument("--wind-shift-after", type=int, default=60)
p.add_argument("--silent-node", default="N3")
p.add_argument("--silent-after", type=int, default=90)
a = p.parse_args()

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.connect("localhost", 1883)
c.loop_start()

seq = {"N1": 0, "N2": 0, "N3": 0}
t0 = time.time()
while True:
    elapsed = time.time() - t0
    shifted = elapsed > a.wind_shift_after
    for node in seq:
        if node == a.silent_node and elapsed > a.silent_after:
            continue
        msg = {
            "type": "wx", "node": node, "seq": seq[node],
            "temp_c": round(random.uniform(28, 33) + (4 if shifted else 0), 1),
            "rh": round(random.uniform(15, 22) - (5 if shifted else 0), 1),
            "wind_ms": round(random.uniform(2, 3) + (5 if shifted else 0), 1),
            "wind_dir": 270 if not shifted else 180,
            "flame": 0, "batt": 90,
        }
        c.publish(f"sensors/{node}/wx", json.dumps(msg))
        seq[node] += 1
    print(f"t={int(elapsed)}s sent  shifted={shifted}")
    time.sleep(10)
```

## Appendix B: `ws_server.py` skeleton
```python
import asyncio, json, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import paho.mqtt.client as mqtt

BROKER = "localhost"
clients: set[WebSocket] = set()
latest: dict[str, dict] = {}
queue: asyncio.Queue = asyncio.Queue()
loop = None

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
    except json.JSONDecodeError:
        return
    item = {"topic": msg.topic, "rx_ts": time.time(), "data": data}
    loop.call_soon_threadsafe(queue.put_nowait, item)   # MQTT thread -> asyncio

async def broadcaster():
    while True:
        item = await queue.get()
        latest[item["topic"]] = item
        for ws in list(clients):
            try:
                await ws.send_json(item)
            except Exception:
                clients.discard(ws)

@asynccontextmanager
async def lifespan(app):
    global loop
    loop = asyncio.get_running_loop()
    m = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    m.on_message = on_message
    m.connect(BROKER, 1883)
    m.subscribe("sensors/#")
    m.subscribe("engine/#")
    m.loop_start()
    task = asyncio.create_task(broadcaster())
    yield
    task.cancel()
    m.loop_stop()
    m.disconnect()

app = FastAPI(lifespan=lifespan)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    for item in list(latest.values()):
        await ws.send_json(item)
    try:
        while True:
            await ws.receive_text()   # keep the connection open
    except WebSocketDisconnect:
        clients.discard(ws)
```
