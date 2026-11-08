# Spec: Live escape-route margin (2013 replay)

Status: ready-for-agent

Vocabulary follows `docs/GLOSSARY.md`. Decisions follow ADR 0001 (scipy routing) and ADR 0002 (fire spread by minimum travel time). The design doc (§1.4, §2.4, §4, §5) holds the full numbers and the 2013 facts with report page references.

## Problem Statement

A crew boss on a wildfire cannot see, in real time, whether the crew's escape route will still be open when they need it. Weather changes on the ground (like the 2013 Yarnell Hill outflow, which reached the fire's north end 12 minutes before it reached the crew) don't reach the people deciding whether to leave a safe place. In 2013 the Granite Mountain crew left the black for Boulder Springs Ranch through a box canyon of old, dense chaparral, the outflow turned the fire onto them, and they had under 2 minutes of warning.

Today we have three working sensor nodes on the Pi's MQTT broker, a database and a WebSocket feed, and all the Yarnell terrain and fuel data. Nothing yet turns those into the answer the crew boss needs: **how many minutes of margin each escape route has, which one to take, and when to leave.**

## Solution

A margin engine on the Pi that continuously answers: margin = (when the fire reaches a cell) − (when the crew reaches it), at the worst cell along each escape route.

- It models the fire spreading across a 10 m grid of the real Yarnell terrain and fuel, keeping its history, so a wind shift turns the fire from where it has already reached.
- It routes the crew over the same grid, both on trails and cross-country through brush at realistic speeds, to each safety zone: Boulder Springs Ranch and the black.
- It closes cells the fire will reach first, reroutes, colours each escape route green/amber/red, raises alerts and shows a leave-by countdown.
- It treats a silent sensor node as worst-case weather, so a lost sensor gives an early warning instead of false confidence.
- A dashboard map shows all of it live.

For the demo (2013 replay, real time): the scenario starts at 16:20 with the crew at the saddle and the black behind them. A fan on N1 is the outflow; the fire turns south toward the box canyon; the ranch route goes red; the system reroutes: back to the black. The simulated crew keeps walking the 2013 line, so the audience watches the margin collapse on the path the real crew took, while the screen shows the route that would have saved them. A hair dryer on N2 adds a spot fire across the canyon. Unplugging N3 greys it out and the margin drops.

## User Stories

### Crew boss: margin and routes
1. As a crew boss, I want to see each escape route's margin in minutes, so that I know how much time I have before the route is cut.
2. As a crew boss, I want one escape route per safety zone (the ranch and the black), so that I can compare my options side by side.
3. As a crew boss, I want each escape route coloured green (≥ 5 min), amber (2–5 min, or a drop of more than 30 %) or red (< 2 min), so that I can read the danger at a glance.
4. As a crew boss, I want the active route highlighted, so that I know which way the system advises.
5. As a crew boss, I want escape routes to go cross-country when that is faster than any trail, so that the advice matches how crews actually move.
6. As a crew boss, I want walking speed to account for slope (uphill slower than downhill), so that a route up a steep slope isn't shown as fast.
7. As a crew boss, I want walking speed to account for brush height and density, so that a short cut through dense chaparral isn't shown as quick.
8. As a crew boss, I want trails and roads walked at full speed, so that the system prefers them when they're genuinely faster.
9. As a crew boss, I want steep (> 38°), water, building and fence cells to be impassable, so that routes never cross ground the crew can't.
10. As a crew boss, I want a route the fire cuts part-way along to be red even when its safety zone is still reachable, so that I'm not sent through the fire.
11. As a crew boss, I want cells the fire reaches before the crew, or within 4 × flame length of burning ground, to be closed to routing, so that routes keep a safe distance from the flames.
12. As a crew boss, I want burning ground to be impassable, so that the system never routes me through active flames.
13. As a crew boss, I want ground that burned more than 5 minutes ago to count as the black, so that I can retreat into it as a safety zone.
14. As a crew boss, I want "the black" route to target any black cell, including newly burned ground, so that the nearest black is always considered.
15. As a crew boss, I want a reroute when the active route becomes unsafe, so that I'm shown the best surviving option immediately.
16. As a crew boss, I want a "no safe route" alert when every escape route is closed, so that I know to deploy or find refuge now.
17. As a crew boss, I want a leave-by countdown, so that I know the latest moment to start moving.
18. As a crew boss, I want fire arrival times divided by a safety factor of 1.5, so that model error errs on the side of safety.

### Crew boss: fire on the map
19. As a crew boss, I want to see the black, burning ground, and the predicted fire front in 5 and 10 minutes on the map, so that I can see where the fire is going.
20. As a crew boss, I want the fire to keep the ground it already burned when the wind shifts, so that the map shows how the fire turns from where it already is.
21. As a crew boss, I want the fire spread to use the real fuel, slope and aspect at each cell, so that it runs faster upslope and downwind.
22. As a crew boss, I want fuel moisture taken from the hottest, driest fresh sensor node, so that the fire model is conservative.
23. As a crew boss, I want a sensor node with a fire point excluded from the moisture input, so that a local hot spot doesn't dry out the whole map.
24. As a crew boss, I want N1's wind applied to the whole area, so that one exposed wind sensor is enough to see the shift.

### Crew boss: sensors and alerts
25. As a crew boss, I want each sensor node's data age shown, so that I know how fresh each reading is.
26. As a crew boss, I want a sensor node shown as aging after 1 missed heartbeat and stale after 3 (at its own interval), so that I can see a sensor going silent.
27. As a crew boss, I want a stale sensor node to be treated as worst-case weather (wind ≥ 13 m/s, temp ≥ 40 °C, RH ≤ 8 %; wind from every direction if N1 is stale), so that a lost sensor shrinks the margin instead of freezing it.
28. As a crew boss, I want a sensor node reading ≥ 45 °C, or rising more than 8 °C within 30 s, to create a fire point at its position, so that a spot fire near a sensor is picked up automatically.
29. As a crew boss, I want at most one fire point per sensor node, keeping its first ignition time, so that a node that stays hot doesn't restart the fire clock.
30. As a crew boss, I want alerts for: active route red or rerouted, no safe route, sensor node stale, and the ranch too small for the current flame length, so that the important changes interrupt me.
31. As a crew boss, I want margins recomputed on every message and at least every 5 s, so that the numbers track the clock and the crew's movement.

### Demo operator
32. As the demo operator, I want an "ignite" button on the dashboard, so that I can start a fire point if the hair dryer misbehaves on stage.
33. As the demo operator, I want a "reset" button that clears all fire points, so that I can rerun the demo.
34. As the demo operator, I want the scenario to start at 16:20 with the crew at the saddle and the black (~16:00) already burned, so that the demo begins at the moment of the 2013 decision.
35. As the demo operator, I want the simulated crew to walk the 2013 descent line in real time at its computed speed, ignoring reroutes, so that the audience sees what happened next to what the system advised.
36. As the demo operator, I want N1's wind speed multiplied by a tunable scale (default ×4), so that a table fan can stand in for a 30–40 mph outflow.
37. As the demo operator, I want the hot-sensor thresholds, wind scale and ranch radius as config values, so that I can tune them on the day with the real fan and hair dryer.
38. As the demo operator, I want the 2013 descent line drawn on the map as a reference layer, so that I can point to the route the crew took.
39. As the demo operator, I want the 2013 final perimeter, the hillshade and the key sites on the map, so that the audience recognises Yarnell.
40. As the demo operator, I want a reloaded dashboard to show the latest state immediately, so that a browser refresh during the demo isn't blank.

### Judge / audience
41. As a judge, I want the route to flip from green to red within seconds of the fan coming on, so that I can see the system reacts live.
42. As a judge, I want to see the sensor nodes placed at real Yarnell positions (N1 on the ridge near the saddle, N2 in the box canyon, N3 between canyon and ranch), so that the setup reads as field-deployable by the crew.

### Developer
43. As a developer, I want the routing graph built once from the terrain rasters and loaded in memory, so that each recompute takes milliseconds.
44. As a developer, I want the routing graph builder testable on tiny synthetic grids, so that routing rules can be checked without the Yarnell data.
45. As a developer, I want one engine step (inputs → engine state message) testable without MQTT, so that fire, margin and reroute behaviour can be checked in isolation.
46. As a developer, I want the engine state message to follow one agreed format, so that the dashboard can be built in parallel with the engine.
47. As a developer, I want fire spread rates cached per recompute by fuel, slope band, aspect band and direction, so that the fire model stays within the 2 s budget on the Pi.
48. As a developer, I want terrain rasters and map layers loaded into PostGIS, so that they can be queried and served alongside the sensor data.

## Implementation Decisions

**Modules**
- **Graph builder** (deep module, runs once). In: terrain arrays for the 2 km × 2 km routing area centred at ≈ 34.221 N, −112.777 W (elevation, slope, vegetation travel factor, impassable mask) and OSM trail lines. Out: a saved routing graph (cell UTM coordinates; directed edges with travel time in seconds, each direction its own entry; trail vertices and connector edges; safety-zone cell ids) plus fastest-time / path queries over it.
  - Grid edges: 8 neighbours, 10 m / 14.14 m, Tobler speed × min(vegetation factor of both cells); no diagonal corner-cutting past an impassable cell.
  - Trails: OSM roads/tracks only, cut into ~25 m pieces, factor 1.0, linked to the grid by connector edges. The 2013 descent line is not a trail.
  - Vegetation factor per cell from LANDFIRE shrub height × cover via the lookup table (floor 0.1, never impassable). 2013 replay flag: SH5 fuel and factor 0.1 inside the 2013 perimeter.
- **Fire model** (inside the engine). State = one fire arrival clock time per cell. Each recompute: cells already reached are fixed; spread outward from the fire front by Dijkstra on the grid, step time = length / directional spread rate from pyrothermel, cached by (fuel, slope band, aspect band, direction). Initial state: cells in the black polygon reached at scenario start. Cell states: unburned / burning (< 5 min) / the black.
- **Margin engine** (deep module). One step function: in = routing graph + terrain, latest reading per sensor node with receive times, fire points, crew position, current time, config; out = the engine state message. Steps: hot-sensor rule → fire spread → crew times (no closures) → close cells → route to each safety zone → repeat close/route until stable → margins, colours, active route, alerts, leave-by. A thin MQTT shell around it subscribes to the sensor, fire and crew topics, calls the step on each message and every 5 s, and publishes the engine state.
- **Scenario simulator** (thin). Publishes the crew position, walking the 2013 descent line from the saddle at 16:20 in real time. Optional fake sensor nodes for offline development stay marked as simulated.
- **Dashboard** (MapLibre, thin). Consumes the existing WebSocket feed: hillshade, 2013 perimeter, sites, the 2013 descent line, sensor nodes with age/status, fire areas, escape routes coloured by status, alerts, leave-by countdown, "ignite" and "reset" buttons (publishing to the fire topic).
- **Static data load.** Rasters and GeoJSON layers into PostGIS on the Pi (for map and SQL queries; routing does not depend on it).

**Engine state message contract** (published on the engine topic, forwarded unchanged by the WebSocket server):
- `type` = "state", `ts` (Unix s).
- `routes`: per escape route: `id` ("ranch" / "black"), `margin_min`, `status` (green/amber/red), `active` (bool), `path` (list of lon/lat).
- `nodes`: per sensor node: `id`, `status` (fresh/aging/stale), `age_s`.
- `fires`: per fire point: `lat`, `lon`, `ignition_ts`.
- `areas`: GeoJSON polygons for `black`, `burning`, `front_5min`, `front_10min`, traced from the cell grid (not the full grid).
- `alerts`: list of human-readable strings.
- `leave_by_ts`.

**Other decisions**
- Routing with scipy in memory; no pgRouting, no closure table (ADR 0001).
- Fire spread by minimum travel time with history (ADR 0002); straight-line spread dropped.
- Safety zones: Boulder Springs Ranch (circle, radius 100 m, ≈ 34.2205 N, −112.7716 W) and the black. Crew vehicles are not a safety zone.
- One wind for the whole area: N1's speed × wind scale, N1's direction ("from"). One moisture value for the whole area.
- Stale = 3 missed heartbeats at the sensor's own interval (N1's interval to be measured).
- Real time, no time compression. Single crew.
- New runtime dependencies: scipy, rasterio, pyrothermel (source build on the Pi; fallback: run the engine on a laptop on FireNet).

## Testing Decisions

- **Good tests check external behaviour** at the two seams below, on small synthetic inputs, and assert outcomes ("downwind before upwind", "route goes red"), not internal arrays or exact spread rates. pyrothermel runs for real inside the engine step (not mocked).
- **Style (prior art):** assert-only runnable self-checks, one file per area, as in the wind bridge's `--test` mode and the design doc's §7 list. No test framework.
- **Seam A, graph builder** (synthetic terrain arrays in, fastest-time / path out):
  - uphill slower than downhill (100 m climbing 10 m ≈ 101 s vs ≈ 71 s);
  - the route detours around an impassable barrier;
  - no diagonal corner-cutting past an impassable cell;
  - a trail is preferred over a parallel stretch of brush;
  - brush slows but never blocks (a route exists through factor-0.1 cells).
- **Seam B, engine step** (inputs + time in, engine state message out):
  - after a wind shift the fire turns from where it already reached; cells reached before the shift keep their times;
  - downwind cells are reached before upwind cells;
  - a cell is burning for 5 minutes, then black;
  - a route the fire cuts mid-way is red even when its safety zone is reachable;
  - when the ranch route is cut, the active route switches to the black (reroute + alert);
  - no path left → "no safe route" alert;
  - a stale sensor node greys out and its worst-case values shrink the margin;
  - a hot sensor node (≥ 45 °C) creates exactly one fire point, keeping its first ignition time;
  - the ranch-too-small alert fires when 4 × flame length exceeds the ranch radius.
- **Not automated:** the MQTT shell, the WebSocket server, the dashboard, the scenario simulator. Checked by hand in the end-to-end rehearsal on the Pi with the real fan and hair dryer.

## Out of Scope

- Flame sensor, LoRa link, thermal lookout, crew tag (cut).
- "If it happened today" scenario with LF2025 fuels (kept only as a config flag, not demoed).
- Spotting, terrain wind channeling, per-area wind, forecast wind and the forecast-vs-sensor alert.
- Flame-length thresholds near the crew as a separate alert.
- Crew scouting marks (scouted / no-go segments).
- The vegetation-height safety zone formula (Butler 2014), pending verification.
- Multiple crews, crew vehicles as safety zones, time compression.
- A button to make the simulated crew follow the system's advice.
- 16-neighbour grid and path smoothing.

## Further Notes

- Deadline: submissions Sun Nov 8, 12:00. The riskiest hand-off is the routing graph reaching the engine; fallback is a coarser 20 m grid.
- Still to tune on the day: hot-sensor thresholds (a DHT11 tops out near 50 °C), wind scale with the real fan, ranch radius against the hillshade.
- Still to draw by hand from the SAIT report maps: the black at ~16:00 and the 2013 descent line. Still to pick: exact sensor node coordinates.
- N1 still needs its fan test (wind "from" convention, direction offset) and transmit interval measured; its stale threshold depends on that interval.
- pyrothermel has not yet been built on the Pi (aarch64, Python 3.13).
