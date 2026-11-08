# Handoff: LCES Live (Sun Nov 8, 2026, after session 5)

Repo: `/home/yugesh/Documents/codemetalHackathon/Team-2`, branch `main`, last commit `c305724` ("dashboard connected"),
working tree clean at the time of writing. Update this file at the end of each session (it replaces `/tmp` handoffs).

## Read first
- `CLAUDE.md` → `docs/specs/escape-margin/spec.md` (demo story near the top, "Testing Decisions"), `docs/LCES_Design.md`
  §2.4, §5 (steps 1–10), §5.6 (all "Update Nov 8" blocks; the closures one is from session 5), §8.4–8.6, §9 track 8,
  `docs/adr/0001`, `0002`, `docs/GLOSSARY.md` ("perimeter" = final 2013 outline only; say "fire area").
- Running it: `README.md`, `./run_demo.sh` (laptop), `docs/pi-setup.md` (Pi).
- Code: `margin.py` (`FireGrid`, `graph_routes`, `fire_areas`, `evaluate`), `engine.py` (`MarginEngine.compute`),
  `build_graph.py` (`route`, `path_to`), `load_static.py`, `simulator.py`, `dashboard/src/App.tsx`, `ws_server.py`.

## State: a complete pipeline runs
Sensors / simulator → Mosquitto → `engine.py` → `engine/state` → `ws_server.py` → dashboard (`:8000`, served from
`dashboard/dist`). Checked end to end in a browser on the laptop: the 2013 story plays (SSW both green → wind turns
→ ranch red → black amber → both red + "NO SAFE ROUTE"). **Never run on the Pi yet.**

## Done in session 5
1. **Closures + reroute loop (§9 8d, §5 steps 3–7)** in `margin.graph_routes(g, crew_xy, fire, now)`, called every
   recompute (no route cache): crew Dijkstra without closures → close vertices that are burning, within 4 × flame of
   burning, or reached by the fire (÷ 1.5) before the crew → Dijkstra again → repeat until no new closures (cap 10).
   `black` target = every black cell by now (grows). Zone with no path left keeps its fire-blind path, so it shows red
   and the existing all-red rule raises "NO SAFE ROUTE"; routes never vanish from `engine/state`.
   2013 sweep (N1 7.5 m/s, +10°/min from 200°): green to 280°, ranch red 290°, black amber 330°, red 340° → NO SAFE
   ROUTE; no detour from the saddle. 40–60 ms per recompute, ~270 ms on a weather change.
2. **`areas` in `engine/state` (part of §9 8e):** `margin.fire_areas` → black, burning, front_5min (0–5 min),
   front_10min (5–10 min) as lon/lat MultiPolygons (`rasterio.features.shapes`), ~15 KB, ~30 ms.
3. **Fire clock starts at the first sensor reading**, not engine start (before, ~10 s of worst-case weather before
   the first reading was kept as fire history: −6 min margin for the whole run).
4. **Dashboard integrated** (`dashboard/src/App.tsx`, `types.ts`): routes from `path` (status colour, active thicker),
   nodes from engine `nodes`, spot fires, crew, the four `areas`; hillshade corners from `meta.json`; 2013 perimeter
   (dashed) + descent line (dotted); map fits the crew area; WebSocket reconnects every 2 s. Removed the hardcoded
   R1/R2 lines. **Fix:** MapLibre v6 loads its worker from a separate file the Vite build didn't ship, so no GeoJSON
   layer drew (also in the teammate's version) → `setWorkerUrl` to the bundled worker. `package.json` pinned (was
   `latest`).
5. **Simulator story wind:** `--dir-before 200` turning clockwise to `--dir-after 20` over `--turn-s` after
   `--wind-shift-after` (was 270° → 180°).
6. **`run_demo.sh`:** Docker Mosquitto + engine `--allow-sim` + `ws_server` (`ALLOW_SIMULATOR_DATA=true`) +
   simulator (N3 kept alive); args pass to the simulator. Dashboard at http://127.0.0.1:8000.
7. **PostGIS static load, `load_static.py`:** rasters `dem/slope/aspect/fuel` via `raster2pgsql` (100 × 100 tiles),
   `perimeter/sites/scenario/osm` into `static_features(layer, name, props, geom)`, `nodes.geom` from `nodes.json`;
   EPSG:32612; rerunnable; no GDAL needed. Needs `CREATE EXTENSION postgis_raster` once as superuser (clear error
   if missing). Tested twice on PostGIS 3.6 as a non-superuser. `deploy_pi.ps1` runs it after the schema. Nothing
   live reads these tables (SQL / QGIS only).
8. **uv locked down:** Python `==3.13.*` (`.python-version`, the Pi's Debian 13 Python), `default-groups = ["engine"]`
   (plain `uv sync` installs everything), `build-constraint-dependencies` pins pyrothermel's build tools
   (scikit-build-core 1.1.2, pybind11 3.1.0, cmake 4.4.4). Every package has an aarch64 cp313 wheel except
   pyrothermel (sdist only, compiles; needs `build-essential cmake`). Use `uv sync --locked` (deploy, README, guide).
9. **`docs/pi-setup.md`:** step-by-step for the teammate with SSH: one-time setup (deps while online, DB/user,
   extensions, `.env`, Mosquitto), copy (`deploy_pi.ps1` or scp), PostGIS load + check query, start/stop services,
   dashboard URLs (FireNet `http://10.42.1.1:8000`, cable `http://10.42.0.1:8000`), simulator test, troubleshooting.
10. **Decided: N1 (SDR wind sensor) stays on a laptop** on FireNet, publishing with `MQTT_HOST=10.42.1.1`; not moved to
    the Pi (design §1.3 updated).
11. Checks: `uv run python test_margin.py` → **24 pass** (new: closures detour around a spot fire with margin ≥ 0
    where the fire-blind route is negative; fire on the crew keeps both routes listed and red + the black target
    grows; `areas` present). `uv run python build_graph.py --test` passes.

## Next tasks (in order)
1. **Pi bring-up** with `docs/pi-setup.md`: first `uv sync --locked` on the Pi (pyrothermel compile, untested on
   ARM), then recompute times in `logs/engine.log` (ROS table rebuild may be ~1 s on the Pi and N1 changes every
   message; if too slow, cache tables per weather or band the wind).
2. **Hot-sensor rule (§1.4, design §5.6 v1→v2 table):** ≥ 45 °C, or +8 °C within 30 s → one fire point at that node,
   first ignition time kept. **Not implemented**, and no node ever sends `"flame": 1` (all send −1), so the hair
   dryer beat currently does nothing. ~15 lines in `engine.on_wx` + one check (§7 lists it).
3. **Exclude hot / fire nodes from temp/RH** in `engine.weather` (today the hottest reading sets moisture for the whole
   grid, so a hair dryer on N2 speeds the fire everywhere). Offered together with item 2; **user hasn't answered.**
   Full local weather (nearest-node zones) discussed: little gain now (only N1 has wind; no node near the front),
   ~3× ROS table cost; recommended later with more nodes.
4. **Crew simulator (§9 item 10):** crew walks the 2013 descent line from 16:20, real time, on `crew/<id>/pos`. Today
   the crew marker stays at the descent point.
5. **Rest of `engine/state` §5.5:** `fires` with `ignition_ts` (now `age_s`); optional reference layers.
6. **Other v1→v2 gaps (§5.6 table):** wind from N1 only × `WIND_SCALE`; stale direction unknown only if N1 stale;
   worst case 13 m/s / 40 °C / 8 %; aging at 1 missed heartbeat.
7. **Cleanup:** delete `export_routes()` / `--routes` / `routes.geojson` / `travel_factor` path (user undecided; v1
   placeholder checks still use `load_routes`). Move the 440 MB DEM to `data/raw/`.
- Still open: veg table calibration with the operator mentor, N1 transmit interval, `WIND_SCALE` / worst-case
  calibration vs the slides, reroute alert on a path change (§5 step 8; skipped, the story has no reroute beat),
  bash port of `deploy_pi.ps1` (offered; this laptop has no `pwsh`).

## Not obvious from the files
- **Active route at start is `black`** (fastest non-red). The 2013 crew headed for the ranch. "Intended safety zone"
  setting offered earlier; no answer.
- **Margin ≠ ETA.** Keep them apart in answers.
- Amber with a big margin (e.g. 112 min) = the "dropped > 30 %" rule (§5.1), expected while the wind turns.
- All routes red at startup with "stale N1,N2,N3" = no reading yet (worst-case weather); the fire clock waits for the
  first reading.
- `.env` on this laptop points at the Pi (`MQTT_HOST=10.42.1.1`); `run_demo.sh` overrides it with exported env
  (`load_dotenv` doesn't override). Simulator messages carry `_simulated`; both engine (`--allow-sim`) and
  `ws_server` (`ALLOW_SIMULATOR_DATA`) drop them unless allowed; `run_live.sh` refuses simulator data on purpose.
- `ws_server.py` serves `dashboard/dist` (gitignored): rebuild (`npm run build`) after dashboard changes and before
  copying to the Pi; reload the page.
- This laptop's other Docker PostGIS (`backend-db-1`) belongs to another project; don't touch it.
- Wind direction convention: "from". Test helper `feed()` feeds N1 wind; N2/N3 send −1.

## User preferences
- Docs under `docs/`, no tickets / GitHub Issues; user commits/pushes; ponytail (minimal code, assert-only checks,
  no pytest); go by the spec; keep the demo simple and close to the 2013 truth.
- User owns engine integration (`engine.py`, `margin.py`, `fire_model.py`). Dashboard came from a teammate and was
  integrated in session 5 with the user's go-ahead.
- Answers: outcome first.

## Suggested skills
- `tdd` for the hot-sensor rule; `diagnosing-bugs` if the Pi is slow or the map misbehaves; `domain-modeling` if terms
  change (`front0`, "fire area", "cold edge" aren't in the glossary yet).

## Session 6 (Sun Nov 8): Yarnell Hill story animation
`animation/index.html`: 34 s story on USGS satellite imagery; fire layer Bayer-dithered on a 1/4 grid; modern Inter panel (`window.seek(t)`, `?render`
disables playback). Terrain = USGS 1/3" DEM (`docs/USGS_13_n35w113.tif`); fire spread = per-cell arrival time interpolated
between nested fire areas (overview slides Jun 29 AM → 17:00 traced by colour, `scenario.geojson` 16:00–16:50, NIFC final);
crew = lunch spot → two-track road → descent line. Rebuild data (also needs `SAT=<path to the USGS export, curl line in build_data.py>`): render slides with `pdftoppm -r 200 -png
docs/YarnellOverview.pdf $SLIDES/s`, then `SLIDES=... uv run --with rasterio --with scipy --with pillow python animation/build_data.py`.
