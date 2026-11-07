# LCES Live: Design Document
Companion to `LCES_Live.md` (pitch, hardware, schedule). This file is the build reference: what each part computes, with which data, and why.

Core question the system answers, continuously:
**margin = (when fire reaches a point) − (when the crew reaches it)**, taken at the worst point along each escape route.

---

## 1. Pipeline overview
```
ESP32 nodes (wind, temp, RH, flame) ──MQTT──┐
Thermal lookout (hot-spot blobs → ground XY) ┤
                                             ▼
                         Python ingest → PostGIS (readings, fire_points)
                                             │
            ┌────────────────────────────────┼─────────────────────────────┐
            ▼                                ▼                             ▼
   FIRE MODEL (pyrothermel)        CREW MODEL (pgRouting)          STATIC DATA (loaded once)
   fire arrival time per node      crew arrival time per node      DEM, slope, aspect, fuel,
                                                                   trails, grid, safety zones
            └──────────────► MARGIN ENGINE ◄─┘
                     margin, colour, reroute, alerts
                                     ▼
                     FastAPI + WebSocket → MapLibre
```
Recompute on every new reading or detection (target: < 2 s end to end).

**One coordinate system for everything: EPSG:32612 (UTM zone 12N, metres)** for Yarnell Hill / Arizona. Reproject every input on load. Lengths, distances and costs all assume metres.

---

## 2. Fire model

### 2.1 Decision: use pyrothermel
- `pip install pyrothermel`. Python bindings to the Missoula Fire Lab's Behave C++ engine (Rothermel surface model, the same engine BehavePlus and FlamMap use).
- A community wrapper, not an official Fire Lab release. Pitch line: "Rate of spread from the Fire Lab's Behave engine, via pyrothermel."
- **The Pi needs a source build.** PyPI only ships a Windows wheel. It needs `build-essential` + `cmake`. It built fine on x86 Linux / Python 3.14. **Test on the Pi before Saturday.**
- Reference demo: `pyrothermel_demo.py` (runs, 9 self-checks pass).

### 2.2 Inputs
| Input | Value / source | Notes |
|---|---|---|
| `fuel_model` | `FuelModel.from_existing(<int>)` with the LANDFIRE **FBFM40** cell value | The integer IDs equal LANDFIRE codes (101 = GR1, 145 = SH5). 91–99 = non-burnable → spread 0, so they act as natural barriers. Demo default: `SH5` (chaparral, Yarnell) |
| `moisture_scenario` | Demo: `MoistureScenario.from_existing(1, 2)`. Live: `MoistureScenario(one_hr, ten_hr, hundred_hr, live_herb, live_woody)` as fractions | **TODO:** 1-h moisture from node temp + RH using the Rothermel 1983 (INT-GTR-143) table, about 10 lines. 10-h ≈ 1-h + 1%, 100-h ≈ 1-h + 2% |
| `wind_speed` | Node anemometer, **km/h** (sensor gives m/s → ×3.6) | Use the highest recent gust (conservative) |
| `wind_input_mode` | `'direct_midflame'` for nodes mounted ~1–2 m | Use `'ten_meter'` (+ canopy_cover/height/ratio) for **forecast** winds. 20 km/h at 10 m becomes ~9.5 km/h midflame, which more than halves the spread rate |
| `wind_direction` | **Raw vane value, direction the wind blows FROM** | Verified: 225 in → head runs toward 45. **No +180 needed** |
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
def arrival_min(run, fire_xy, pt_xy):           # metres, x=east, y=north
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
raster2pgsql -s 32612 -t 100x100 -I dem.tif dem | psql lces
```
Point lookup:
```sql
SELECT ST_Value(rast, pt) FROM dem WHERE ST_Intersects(rast, pt) LIMIT 1;
```

---

## 4. Crew model (routing)

### 4.1 Graph: two layers joined together
1. **Trail graph.** Demo: **hand-drawn in QGIS** (2–3 routes + alternates, snapped at junctions). Later: OSM via `osm2pgrouting`, or USFS trails + `pgr_extractVertices`. Check OSM coverage at Yarnell before relying on it.
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
**Placeholders I made up. Calibrate with Campbell et al. 2017 and the operator mentor on Saturday.** Keep them in `config.py`.

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
- Start: crew position snapped to the nearest node (`pgr_withPoints` to start partway along an edge).
- Targets: all safety zones + vehicle locations. `pgr_dijkstra(sql, crew_node, ARRAY[...])`, cheapest result wins.

### 4.6 Tool decision
**pgRouting** (handles uphill vs downhill costs, same database as everything else). Considered and rejected for now: `skimage.graph.MCP_Geometric` (can't make uphill cost more than downhill), GRASS `r.walk` (good but a heavy extra tool).

---

## 5. Margin engine

Every recompute:
1. Pull the latest readings per node and the active fire points. Skip any node older than the stale threshold.
2. Build one pyrothermel run per fire point (fuel/slope/aspect at that point, current wind and moisture).
3. Run Dijkstra from the crew to each target. That gives `t_crew(i)` for every node `i` on each route.
4. For every route node: `t_fire(i) = min over fire points of arrival_min(...) / safety_factor`.
5. `margin(route) = min_i ( t_fire(i) − t_crew(i) )`. The safety zone itself is one of the nodes.
6. **Reroute:** mark edges where `t_fire < t_crew` (plus the flame-distance buffer) as impassable, then run Dijkstra again. The surviving cheapest path becomes the active escape route.
7. **Leave-by time** (dynamic trigger) = `now + margin − buffer`. Show it as a countdown.
8. Push the GeoJSON (routes with margin/colour, fire points, node health) over the WebSocket.

### 5.1 Thresholds (defaults, in `config.py`)
| Status | Rule |
|---|---|
| Green | margin ≥ 5 min |
| Amber | 2 min ≤ margin < 5 min, **or** margin dropped > 30% since the last update |
| Red | margin < 2 min → reroute + alert |
| Grey (node) | no reading for > 30 s (≈ 3× publish interval) |

### 5.2 Alerts
1. Route red / rerouted.
2. Safety zone too small for current flame length (4× rule).
3. Flame length over 1.2 m / 2.4 m near the crew.
4. Node stale / lookout lost (data age).
5. Forecast vs sensor wind disagree by > X (shows the Yarnell failure: the forecast didn't match the ground).

---

## 6. Data model additions (on top of `LCES_Live.md` §5)
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

**Routing** (to write):
- Uphill cost > downhill cost (100 m / +10 m ≈ 101 s vs 71 s).
- The grid route detours around a barrier.
- No diagonal corner-cutting past an impassable cell.
- A trail is preferred over a parallel chaparral path.

**Margin** (to write):
- A route the fire cuts mid-way is red even when its safety zone is still reachable.
- Rerouting picks the alternate route.
- A stale node goes grey.

---

## 8. Before Saturday
- [ ] Build pyrothermel on the Pi 5.
- [ ] Download LANDFIRE (FBFM40, elevation, slope, aspect) + 3DEP 10 m DEM for Yarnell. Reproject to EPSG:32612 and load into PostGIS.
- [ ] Hand-draw the trail network + safety zones in QGIS. Check OSM coverage.
- [ ] Trail cost SQL + routing checks.
- [ ] RH/temp → 1-h fuel moisture table.
- [ ] Questions for the operator mentor: travel speed in chaparral vs trail, acceptable margin thresholds, a quote for the pitch.

---

## 9. Sources
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
