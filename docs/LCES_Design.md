# LCES Live: Design Document
The single build reference for the team: what each part computes, with which data, how the pieces talk, and current status.
`LCES_Live.md` is the initial pitch and is **not** kept in sync; where it differs, this file wins.
(Merged from the team context doc "FireGuard — Team Context & Status", Nov 7, which this file replaces.)

Core question the system answers, continuously:
**margin = (when fire reaches a point) − (when the crew reaches it)**, taken at the worst point along each escape route.

**Event:** Code Metal Forge 2026 ("Operation Lost and Found"), BASE-X Boston, sponsored by USSOCOM. Build Sat 10:00 → submissions Sun 12:00, then live demos. Judged on mission relevance, a working live demo, deployability.
**Objectives fit:** strong on Map changing conditions and Deliver critical information, good on Detect hazards, weak on Locate people unless we add the crew tag / "can rescuers still reach the trapped hiker safely" angle.
**Pitch wording:** the 2013 Yarnell Hill investigation (19 firefighters died) highlighted gaps in real-time weather, fire and crew-location information; our system targets those gaps. **Do not claim it would have prevented the tragedy.**
**Demo:** tabletop terrain model. A fan simulates a wind shift, a hair dryer / heat gun (never open flame) simulates a spot fire. Margin collapses, route flips green → red, an alternate route appears, an alert fires.

---

## 0. Status (Sat Nov 7, evening)

| Area | Status | Owner | Next step |
|---|---|---|---|
| **Node N1: La Crosse TX145WSDTHv3 via SDR** (temp, RH, wind speed, wind direction) | **Working end to end: readings seen on the Pi broker** (verified as `W1`, renamed `N1`; §1.3) | TBD | Fan test of direction; measure transmit interval; move SDR to the Pi |
| Node N2: XIAO ESP32-S3 + DHT11 (`firmware/weather-node`) (temp, RH) | **Working:** joins FireNet (−35 dBm with antenna), heartbeat to `sensors/N2/wx` every 10 s accepted by the Pi broker (verified Sat Nov 7) | TBD | — |
| Pi 5 base: OS, packages, Mosquitto | Done and verified | Vivek | — |
| FireNet hotspot on the Pi | **On**; Pi = `10.42.1.1` on FireNet (verified by the wind bridge) | Vivek | Point all ESP32s at `10.42.1.1` |
| Laptop ↔ Pi SSH over Ethernet | Done, VS Code Remote-SSH works | Vivek | Share access with the team |
| Simulator (fake N1–N3) | Running, verified | Vivek | Teammates copy it for local dev |
| PostGIS database + ingest | Not started | Vivek | Create DB `fire` (`nodes`, `readings`), write `ingest.py` |
| WebSocket server (FastAPI) | Not started | Teammate | Appendix B |
| Dashboard (MapLibre) | Not started | TBD | Consume the WebSocket feed |
| Margin engine | Not started | Vivek | After ingest: staleness, trail graph, fire/escape model |
| Flame sensor | Not started | TBD | — |
| LoRa link | Not started | TBD | Module model unknown; RadioLib likely |
| Thermal lookout | Not started | TBD | Get the thermal camera from the shared library first |
| Tabletop terrain model | Not started | TBD | Cardboard/foam; photo from above becomes the map |

---

## 1. Pipeline, messages, coordinates

### 1.1 Overview
```
ESP32 nodes (temp, RH, flame) ─────────────MQTT─┐
La Crosse wind sensor ─433 MHz─ SDR + rtl_433 ──┤  sensors/<node>/wx
   (wind_bridge.py)                             │
Thermal lookout (hot spot → tabletop x,y) ──────┤  sensors/<src>/fire
Crew tag (optional) ────────────────────────────┤  crew/<id>/pos
                                                ▼
                               Mosquitto on the Pi (10.42.1.1)
                                                │
                   ingest.py → PostGIS (readings, fire_points)
                                                │
     ┌──────────────────────────────────────────┼──────────────────────────┐
     ▼                                          ▼                          ▼
FIRE MODEL (pyrothermel)            CREW MODEL (pgRouting)        STATIC DATA (loaded once)
fire arrival time per node          crew arrival time per node    DEM, slope, aspect, fuel,
                                                                  trails, grid, safety zones
     └────────────────────► MARGIN ENGINE ◄─────┘
                 margin, colour, reroute, alerts
                                │  publishes engine/state (MQTT)
                                ▼
         ws_server.py (FastAPI, subscribes sensors/# + engine/#) → WebSocket → MapLibre
```
Every component talks only through Mosquitto, so the simulator and the real nodes are interchangeable. The WebSocket server only subscribes to MQTT.
Recompute on every new reading or detection (target: < 2 s end to end).

### 1.2 MQTT topics and message format
| Topic | Sent by | Content |
|---|---|---|
| `sensors/<node>/wx` | Sensor nodes, wind bridge, simulator | Weather heartbeat (every 10 s for ESP32 nodes; N1 per radio packet, §1.3) |
| `sensors/<src>/fire` | Thermal lookout | Spot-fire event with tabletop position |
| `crew/<id>/pos` | Crew tag (optional) | Crew position |
| `engine/state` | Margin engine | Route margins and colours (format TBD) |

JSON; `-1` / `-99` = no data yet. Nodes have no reliable clock, so the Pi adds its own receive time.
```json
{"type":"wx","node":"N1","seq":184,"temp_c":31.2,"rh":18.0,"wind_ms":-1,"wind_dir":-1,"flame":-1,"batt":-1}
{"type":"fire","src":"THERM1","x":42.0,"y":18.5,"conf":0.9}
{"type":"crew","id":"C1","x":10.0,"y":55.0}
```
- `seq` counts heartbeats per node. `wind_dir` = direction the wind blows **from**, degrees. `wind_ms` = m/s.
- **Demo nodes (2):** `N1` = La Crosse sensor via SDR, sends **temp, RH, wind speed and wind direction** (§1.3). `N2` = ESP32-S3 + DHT11, sends **temp and RH** only (§8.3). The simulator also uses `N1`–`N3` (§8.4).

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
- **Direction depends on mounting:** the vane reads relative to the housing. The label says the solar panel must face **south**. On the tabletop, either face it south or set `DIR_OFFSET`.
- The Python MQTT library (`python3-paho-mqtt`) must be installed before joining FireNet, which has no internet.
- For the demo, plan to move the SDR to the Pi (install `rtl-433` while the Pi still has internet, copy `wind_bridge.py` to `~/fire`, run with `uv run python wind_bridge.py`; default `MQTT_HOST=localhost`). **Not done yet.**

**Still to verify**
- [ ] Fan test: blow from a known compass direction and confirm `wind_dir` = direction the wind comes **from** (§2.2 relies on this). Set `DIR_OFFSET` if needed.
- [ ] Transmit interval: time between consecutive `sensors/N1/wx` messages. If it is longer than ~10 s, the 3-heartbeat stale rule (§5.1) needs a per-node threshold for N1.

### 1.4 Coordinates
**Messages use tabletop centimetres; the engine computes in real metres.**
- All messages and positions (nodes, trails, safety zones, hot spots, crew) are tabletop **cm, (x, y) from one corner**.
- The fire and crew models need real units (spread rate and Tobler speed are in km/h, slope and fuel come from real rasters). So the engine maps the table onto a real Yarnell Hill patch in **EPSG:32612 (UTM zone 12N, metres)** with one transform, in one place:
  ```
  east_m  = E0 + x_cm * SCALE
  north_m = N0 + y_cm * SCALE        (check the y sign: table corner vs. map north)
  ```
  Example only: 1 cm = 20 m makes a 100 cm table = 2 km × 2 km, the routing grid size in §4.1.
- Reproject every raster input to EPSG:32612 on load. Lengths, distances and costs all assume metres.
- **Fallback** if the DEM/LANDFIRE load is not ready: single fuel (SH5) and flat ground over the same scaled metres. Units stay real; only the terrain detail is lost.
- **To decide (team):** table size in cm, `SCALE` (m per cm), origin `E0/N0` (which Yarnell patch, which corner is (0,0)), and whether the foam model is shaped after that patch's real terrain.

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
| `moisture_scenario` | Demo: `MoistureScenario.from_existing(1, 2)`. Live: `MoistureScenario(one_hr, ten_hr, hundred_hr, live_herb, live_woody)` as fractions | **TODO:** 1-h moisture from node temp + RH using the Rothermel 1983 (INT-GTR-143) table, about 10 lines. 10-h ≈ 1-h + 1%, 100-h ≈ 1-h + 2% |
| `wind_speed` | `wind_ms` from the nodes, **km/h** (`× 3.6`) | Use the **highest recent `wind_ms`** (conservative). N1 (La Crosse) reports averages only, no gust |
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

### 2.4 Fire arrival time at a point
```python
def arrival_min(run, fire_xy, pt_xy):           # metres (after §1.4 transform), x=east, y=north
    dx, dy = pt_xy[0]-fire_xy[0], pt_xy[1]-fire_xy[1]
    bearing = math.degrees(math.atan2(dx, dy)) % 360
    ros_m_min = run.run_surface_fire_in_direction_of_interest(bearing)["spread_rate"] * 1000/60
    return math.hypot(dx, dy) / ros_m_min
```
- With several fire points (spot fires, thermal blobs), the arrival time = **min over all fire points**.
- Divide arrival times by a **safety factor (default 1.5)**. Show the unfactored value as the uncertainty band.
- Model assumptions to state openly: straight-line spread, constant conditions between recomputes, no spotting, no terrain wind channeling.
- Upgrade path: Finney (2002) Minimum Travel Time, i.e. fire arrival as a shortest path over the same grid with edge time = length / directional ROS. Reuses pgRouting.

### 2.5 Extra outputs used for alerts
| Use | Rule | Source |
|---|---|---|
| Suppression limit | flame < 1.2 m hand crews OK · 1.2–2.4 m equipment only · > 2.4 m control efforts fail | Fire characteristics chart / hauling chart |
| **Safety zone adequacy** | Required separation ≥ **4 × flame height** (use flame_length as the proxy). Alert if a safety zone's radius is smaller | Butler & Cohen 1998. Behave's safety module isn't exposed, so it's one line of our own code |
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

| Layer | Source | Use |
|---|---|---|
| Elevation (DEM) | USGS 3DEP 10 m (apps.nationalmap.gov/downloader) or LANDFIRE elevation | Tobler slopes, hillshade |
| Slope, aspect | LANDFIRE (precomputed) or `gdaldem slope/aspect dem.tif` (`-zero_for_flat` for aspect) | pyrothermel inputs |
| Fuel model | **LANDFIRE FBFM40** | pyrothermel fuel + travel factors |
| Water | LANDFIRE NB8, USGS NHD, OSM | Barrier |
| Buildings, fences | OSM | Barrier |
| Understory density (field version) | USGS 3DEP lidar: share of returns 0.5–3 m above ground | Travel factor (Campbell et al. 2017) |

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
1. **Trail graph.** Demo: **hand-drawn** on the tabletop map (2–3 routes + alternates, snapped at junctions), digitised in tabletop cm and transformed (§1.4). Later: OSM via `osm2pgrouting`, or USFS trails + `pgr_extractVertices`. Check OSM coverage at Yarnell before relying on it.
2. **Off-trail grid graph.** Every 10 m DEM cell is a node with 8 neighbours (16 with knight moves, for smoother paths). 2 km × 2 km ≈ 40k nodes / 320k edges, fine for `pgr_dijkstra`. Join trail vertices to the nearest grid cells.

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
- Impassable: no edge (or cost `-1` in pgRouting).

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

### 4.3 Travel factors (multiplier on Tobler speed; 0 = impassable)
**Placeholders I made up. Calibrate with Campbell et al. 2017 and the operator mentor.** Keep them in `config.py`.

| Ground | Factor |
|---|---|
| Trail / road | 1.0 |
| Bare ground (NB9), grass (GR) | 0.8 |
| Grass-shrub (GS), timber litter (TL) | 0.6 (Tobler's own off-path factor) |
| Light shrub (SH1–3) | 0.4 |
| Timber understory (TU) | 0.3 |
| Heavy shrub / chaparral (SH5–9) | 0.15 |
| Water (NB8), buildings, fences | 0 |
| Slope > 38° | 0 |
| **Cells the fire reaches before the crew, or within 4 × flame height of the front** | 0 (dynamic) |

### 4.4 Route quality
- 8-direction grids zigzag (~8% too long). Use 16 neighbours, or simplify the path afterwards and re-check it against barriers.
- **Scouting status:** the crew boss can mark segments **scouted** or **no-go** on the map. Unscouted off-trail stretches get an extra time buffer and a warning ("route uses 400 m of unscouted brush"). This addresses the Yarnell Hill lesson directly.

### 4.5 Start and targets
- Start: crew position (`crew/<id>/pos`, or fixed) snapped to the nearest node (`pgr_withPoints` to start partway along an edge).
- Targets: all safety zones + vehicle locations. `pgr_dijkstra(sql, crew_node, ARRAY[...])`, cheapest result wins.

### 4.6 Tool decision
**pgRouting** (handles uphill vs downhill costs, same database as everything else). Considered and rejected for now: `skimage.graph.MCP_Geometric` (can't make uphill cost more than downhill), GRASS `r.walk` (good but a heavy extra tool).

---

## 5. Margin engine

Every recompute:
1. Pull the latest reading per node and the active fire points. **A stale node (§5.1) is not skipped: the engine uses worst-case values for it** (§5.3).
2. Build one pyrothermel run per fire point (fuel/slope/aspect at that point, current wind and moisture).
3. Run Dijkstra from the crew to each target. That gives `t_crew(i)` for every node `i` on each route.
4. For every route node: `t_fire(i) = min over fire points of arrival_min(...) / safety_factor`.
5. `margin(route) = min_i ( t_fire(i) − t_crew(i) )`. The safety zone itself is one of the nodes.
6. **Reroute:** mark edges where `t_fire < t_crew` (plus the flame-distance buffer) as impassable, then run Dijkstra again. The surviving cheapest path becomes the active escape route.
7. **Leave-by time** (dynamic trigger) = `now + margin − buffer`. Show it as a countdown.
8. Publish the result (routes with margin/colour, fire points, node health) to MQTT `engine/state`; `ws_server.py` forwards it to the dashboard.

### 5.1 Thresholds (defaults, in `config.py`)
| Status | Rule |
|---|---|
| Green | margin ≥ 5 min |
| Amber | 2 min ≤ margin < 5 min, **or** margin dropped > 30% since the last update |
| Red | margin < 2 min → reroute + alert |
| Grey (node) | 3 missed heartbeats (≈ 30 s at the 10 s interval). N1's (La Crosse) interval is not yet measured (§1.3) |

Every reading shows its age on the map.

### 5.2 Alerts
1. Route red / rerouted.
2. Safety zone too small for current flame length (4× rule).
3. Flame length over 1.2 m / 2.4 m near the crew.
4. Node stale / lookout lost (data age).
5. Forecast vs sensor wind disagree by > X (shows the Yarnell failure: the forecast didn't match the ground).

### 5.3 Worst-case values for a stale node
Why: skipping a silent node lets the margin stay green on old data, the same failure as Yarnell. Worst case means the margin can only shrink, so a lost sensor gives an early warning instead of false confidence. In the demo, the silent N3 visibly greys out and the margin drops. **Proposed rules; the numbers go in `config.py` and are not decided yet:**

| Input | Worst case |
|---|---|
| Wind speed | max(node's last reading, current max across other nodes, fixed floor `WORST_WIND_MS`) |
| Wind direction | Unknown → assume toward each route point, i.e. head-fire rate in every direction (a circle of radius head ROS) |
| Temp / RH | Highest recent temp and lowest recent RH, or fixed limits `WORST_TEMP_C` / `WORST_RH` |

---

## 6. Data model (PostGIS database `fire` on the Pi)
- `nodes(id, geom, label)`: positions entered once (tabletop cm → §1.4)
- `readings(...)`: one row per `wx` message plus the Pi's receive time. **Columns not fixed yet** (`ingest.py` defines them)
- `fire_points(...)`: from `sensors/<src>/fire`
- `trails(..., dz, cost, reverse_cost, scouted bool, no_go bool)`
- `grid_edges(id, source, target, cost, reverse_cost, geom)`, built in Python from the rasters
- `dem`, `slope`, `aspect`, `fuel` raster tables (EPSG:32612)
- `safety_zones(id, geom, radius_m)`; the radius is checked against 4 × flame height

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

**Margin** (to write):
- A route the fire cuts mid-way is red even when its safety zone is still reachable.
- Rerouting picks the alternate route.
- A stale node goes grey and its worst-case values shrink the margin.

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
**Python project (uv)** at `/home/thunder/fire`: `uv init --python 3.13`, env in `.venv`, deps in `pyproject.toml` + `uv.lock`. Intended libraries: paho-mqtt, pyserial, fastapi, uvicorn[standard], websockets, psycopg[binary], shapely, numpy, opencv-python-headless (optional until the thermal camera). Check with `uv pip list`; teammates reproduce with `uv sync`; run with `uv run python <script>.py`. Files: `pyproject.toml`, `uv.lock`, `README.md`, `src/`, `simulator.py`.
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

### 8.3 Node N2 (ESP32)
**Node N2 is a XIAO ESP32-S3** with the PlatformIO firmware in `firmware/weather-node` (DHT11 on D3, LED on D4, node ID `N2`, Wi-Fi FireNet → `10.42.1.1`). Build/flash: `cd firmware/weather-node && pio run -t upload && pio device monitor`. At boot it prints FireNet's signal strength, and while connecting the Wi-Fi status (1 = network not seen, 4 = connect failed / password, 6 = disconnected / retrying). **The XIAO ESP32-S3 needs its U.FL antenna attached:** without it FireNet showed −82 dBm and it never connected (status 6); with it −35 dBm and it connected at once.

Earlier ESP32-C5 notes, kept for reference:

Grove DHT11 on D0 (GPIO1), powered from 3V3 (never 5V), Grove-to-male-jumper cable, XIAO straddling the breadboard's centre gap.

| DHT11 pin | Wire | XIAO pin |
|---|---|---|
| GND | Black | GND |
| VCC | Red | 3V3 |
| SIG | Yellow | D0 = GPIO1 |
| NC | White | Not connected |

- With USB-C at the top: left side = D0…D6 (top to bottom), right side = 5V, GND, 3V3, D10…D7. With USB-C toward you the sides swap.
- Pin map: D0 = GPIO1, D1 = GPIO0, D2 = GPIO25 (strapping, avoid), D3 = GPIO7 (strapping, avoid), D4 = GPIO23 (SDA), D5 = GPIO24 (SCL), D6 = GPIO11 (TX). **Avoid strapping pins GPIO2, 3, 7, 25, 26, 27, 28.**
- Arduino IDE 2.3.10, board "ESP32C5 Dev Module", Serial Monitor 115200. Libraries: "DHT sensor library" (Adafruit) + "Adafruit Unified Sensor"; MQTT: PubSubClient (Nick O'Leary). With Dev Module selected use `#define DHTPIN 1` (`D0` only exists with board "XIAO_ESP32C5"). Fallback library: "DHT sensor library for ESPx" (beegee_tokyo).
- Lessons: program prints appear in the Serial Monitor, not the Output panel. `DHTPIN 25` (D2, strapping) caused "Sensor reading failed". Grove-to-Grove cables don't fit the XIAO. XIAO on one half of the breadboard shorts left and right pins. Upload stuck: hold BOOT, tap RESET, release BOOT, upload again.

### 8.4 Simulator
`~/fire/simulator.py` on the Pi pretends to be N1–N3, so backend and dashboard can be built before the hardware. Code in Appendix A. Every 10 s it publishes one heartbeat per node to `sensors/<node>/wx`, and acts out the two demo situations:
- **Wind shift** after `--wind-shift-after` s: direction 270° → 180°, speed ~2 → 7.5 m/s, temp +4 °C, RH −5%.
- **Silent node** after `--silent-after` s: `--silent-node` (default N3) stops sending.
```
mosquitto_sub -h localhost -t 'sensors/#' -v
cd ~/fire && uv run python simulator.py --wind-shift-after 30 --silent-after 60
```
Verified: seq 0–2 calm at 270°, ~2 m/s; seq 3 (t = 30 s) 180°, ~7.5 m/s, temp up, RH down; N3 stops after 60 s. For local dev, install Mosquitto on your laptop and run the same script; topics and format are identical.

### 8.5 WebSocket server → dashboard contract
`ws_server.py` (FastAPI) subscribes to `sensors/#` and `engine/#` and pushes every message to the dashboard. Skeleton in Appendix B. Each WebSocket message:
```json
{"topic": "sensors/N1/wx", "rx_ts": 1762554000.12, "data": {"type": "wx", "node": "N1", "seq": 4, "temp_c": 30.8, "rh": 16.2, "wind_ms": 2.1, "wind_dir": 270, "flame": 0, "batt": 90}}
```
`rx_ts` = server receive time (Unix s). On connect, the server first sends the latest message per topic, so a reloaded dashboard isn't empty.
Run: `uv add fastapi "uvicorn[standard]" paho-mqtt` then `uv run uvicorn ws_server:app --host 0.0.0.0 --port 8000`.
Test in a browser console: `const ws = new WebSocket("ws://localhost:8000/ws"); ws.onmessage = e => console.log(JSON.parse(e.data));`
On the Pi, the dashboard connects to `ws://10.42.1.1:8000/ws` (FireNet) or `ws://10.42.0.1:8000/ws` (cable).

---

## 9. Next steps, open questions, gotchas

**Next steps**
- [ ] Vivek: PostGIS database `fire` (`nodes`, `readings`) + `ingest.py` (MQTT → database)
- [ ] Teammate: `ws_server.py` working locally against the simulator
- [x] ESP32-S3 node: on FireNet, publishing heartbeats (as `N2`, verified Sat Nov 7)
- [ ] N1 (La Crosse): fan test for direction, measure transmit interval, move SDR + bridge to the Pi (§1.3)
- [ ] Staleness monitor (fresh / aging / stale from `last_seen`) + worst-case values (§5.3)
- [ ] Decide the tabletop ↔ UTM transform (§1.4)
- [ ] Trail graph for the tabletop in PostGIS + pgRouting, then the margin engine
- [ ] Build pyrothermel on the Pi 5
- [ ] Download LANDFIRE (FBFM40, elevation, slope, aspect) + 3DEP 10 m DEM for the chosen Yarnell patch; reproject to EPSG:32612; load into PostGIS
- [ ] RH/temp → 1-h fuel moisture table
- [ ] Grab the thermal camera, LoRa modules and ESP32s from the shared library
- [ ] Questions for the operator mentor: travel speed in chaparral vs trail, acceptable margin thresholds, a quote for the pitch

**Open questions**
- Tabletop size, `SCALE`, origin `E0/N0`, and Yarnell patch (§1.4).
- Worst-case numbers `WORST_WIND_MS`, `WORST_TEMP_C`, `WORST_RH` (§5.3).
- N1 (La Crosse) transmit interval, and whether its `wind_dir` is "from" (§1.3).
- `batt` units: the simulator sends 90 (percent?); N1 (La Crosse) sends 100/10 for ok/low; N2 sends -1. Confirm what the dashboard expects.
- Which LoRa module does the shared library have (SX1262 or SX1276)?
- Which thermal camera (MLX90640, FLIR Lepton, other)? Decides the Python driver.
- Is pre-written code / pre-built hardware allowed? Ask the organizers.
- Project name: the team doc says "FireGuard", this repo says "LCES Live".

**Gotchas**
- Never put 5 V on an ESP32 pin; power sensors from 3V3. Avoid strapping pins GPIO2, 3, 7, 25, 26, 27, 28.
- Venue Wi-Fi isolates devices: use FireNet (`10.42.1.1`) or the cable (`10.42.0.1`).
- FireNet has no internet, and turning it on takes the Pi off venue Wi-Fi. Install packages first (Pi and laptop).
- New terminals on the Pi: `cd ~/fire` and use `uv run`, or activate `.venv`.
- **Don't run the simulator alongside the real nodes:** it also publishes `sensors/N1/wx` and `sensors/N2/wx`.
- `mosquitto_sub` looks stuck when nothing is publishing; it is just waiting (Ctrl+C to stop).
- paho-mqtt 2.x needs `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)`.
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
