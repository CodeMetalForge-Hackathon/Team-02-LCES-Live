# Spec: Demo dashboard and reroute story

Status: ready-for-agent

> **Revised Nov 8, ~06:00 (Vivek):** at Start the crew takes the fastest route that isn't red (the black), not the ranch route. The active route still changes only by a reroute. Consequence: the WNW–NW fan cuts the ranch route but no longer causes a reroute (the crew is already on the black). A reroute beat now needs the fire to cut the black: wind from the **E–ENE (60–120°)** → black red, ranch green → "REROUTED: black -> ranch" (see the sweep under Demo story). **Decided ~06:05 (Vivek): the demo ends on the ranch route cut by the WNW wind**, crew safe on the black (no reroute in the main story).

Vocabulary follows `docs/GLOSSARY.md`. Builds on `docs/specs/escape-margin/spec.md` (engine, engine state message) and changes its demo story (see "Demo story" below). Tickets: `issues/`.

## Problem Statement

The dashboard works, but it doesn't carry the demo:
- The map is a grey, desaturated hillshade. Nothing says where Boulder Springs Ranch is, and the yellow dots include post-2013 places (an observation deck next to the crew, the 2016 Hotshot Journey Trailhead).
- The numbers that matter (when the crew and the fire reach each point of an escape route) are only in the side panel, as one margin per route.
- Sensor readings sit in a side panel, away from the sensor nodes on the map.
- The scenario clock (16:20) starts at the first sensor reading after the engine starts, so the fire is already moving before the audience is ready, and a missing N1 burns the whole map before the demo begins.
- The current demo ends on "no safe route". The team wants it to end on the system keeping the crew safe: the outflow cuts the ranch route (where the 2013 crew went) while the crew's route back to the black stays green.

## Solution

- A **Start** button begins the scenario (16:20) when the audience is ready; **Reset** returns to the ready screen.
- At Start the crew takes the fastest safe route (the black) and keeps it until it turns red. When the outflow arrives from the WNW–NW, the ranch route turns red and the black stays green. If the black is cut (wind from the E–ENE), the engine reroutes to the ranch. The dashboard animates the planned route at Start and on any reroute.
- Hovering an escape route shows when the crew and the fire reach that point, as live times. Hovering a sensor node shows its live reading.
- The map gets a coloured relief and fuel backdrop (rendered offline from our own rasters), roads and buildings, and labelled 2013 places: Boulder Springs Ranch (with its 100 m safety zone) and the deployment site. Post-2013 sites are removed. The 2013 descent line stays.

### Demo story (replaces the end of the escape-margin spec's story; decided Nov 8, ~05:30, ending revised ~06:05)

1. Ready screen: the crew at the descent point, the black behind them, both escape routes shown. Nothing moves.
2. **Start** → the scenario clock reads 16:20, the crew's route back to the black is drawn (animated): the fastest route; the 2013 crew headed for the ranch instead. Fan on N1 from the SSW (as before 16:00): the fire grows north-east, away from the crew; both routes green.
3. The fan swings to the **WNW** (~290–300°; the outflow's first direction in 2013, SAIT p.64): the ranch route goes red ("Route ranch RED"), the route the 2013 crew took; the crew's route back to the black stays green with a leave-by time. Hover the ranch route to show where the fire gets there before the crew.
4. Stop there. (Q&A only: if the fan keeps turning to the N/NNE, the black route closes too: "no safe route", what the crew faced after ~16:30.)
5. Unplug N3 last (it greys out and the worst case shrinks every margin); Reset before the next run.

Checked on the 2013 data from the descent point (engine step, wind from 200° at 2.5 m/s, then the outflow; margins in min 1 / 3 / 5 min after the shift):

| Wind from | 4 m/s: ranch / black | 7.5 m/s: ranch / black |
|---|---|---|
| 270° | red / green (+18) | green / green |
| 285° | red / green (+13) | amber / green |
| 300° | red / green (+6, amber after 3 min) | **red / green (+33)** |
| 315° | red / red | **red / green (+12)** |
| 330° | red / red | red / amber |
| 0–20° | red / red (no safe route) | red / red (no safe route) |

So the fan has to give N1 at least ~4 m/s and point from the WNW: 285–300° works at both speeds (at 4 m/s the black margin falls to amber after ~3 min at 300°; 285° keeps it green longer).

Not used in the demo (Q&A only), sweep with the crew starting on the black (Nov 8 ~06:00; same steps, margins 1 / 3 / 5 min after the shift): the black goes red and the ranch stays green (→ REROUTED black → ranch) for wind from **60–120° at 4 m/s** (ranch +15 … +51), **45–90° at 7.5 m/s** (ranch +7 … +84), **30–90° at 10 m/s**. 135–255°: both green. 0–30° and 330–345°: both red. The window is wider and the margins larger at 7.5 m/s (`WIND_SCALE`, escape-margin spec story 36).

## User Stories

### Demo operator
1. As the demo operator, I want a **Start** button on the dashboard, so that the scenario clock (16:20) starts when the audience is ready, not at the first sensor reading.
2. As the demo operator, I want a ready screen before Start that shows the crew, the black and both escape routes without any fire spread, so that I can explain the setup.
3. As the demo operator, I want a **Reset** button that clears fire points and returns to the ready screen, so that I can rerun the demo.
4. As the demo operator, I want the scenario clock (16:20 + elapsed) on screen, so that I can tie the demo to the 2013 timeline.

### Crew boss
5. As a crew boss, I want the crew to start on the fastest safe route and keep it until it turns red, so that a reroute is a visible event.
6. As a crew boss, I want a reroute to the black shown as an animated route being planned, so that I see the system calculated a new route.
7. As a crew boss, I want to hover any point of an escape route and see when the crew gets there and when the fire does (minutes from now and scenario clock time), so that I understand where the route is tight.
8. As a crew boss, I want the tightest point of each escape route marked, so that I see where the margin comes from.
9. As a crew boss, I want to hover a sensor node and see its live reading (temperature, humidity, wind, age, status), so that I read conditions where they are measured.
10. As a crew boss, I want "direction unknown" instead of "null°" when the wind direction is unknown.

### Audience
11. As a judge, I want Boulder Springs Ranch labelled with its safety zone circle, and the 2013 deployment site and descent line marked, so that I can follow the 2013 story.
12. As a judge, I want a map that shows terrain, vegetation (fuel) and roads in colour, so that the setting reads as real ground.

## Implementation Decisions

**Engine (`engine.py`, `margin.py`, `config.py`)**
- **Phase.** The engine starts in `ready`: no fire grid, routes computed without fire (margin `null`, status green), no fire alerts. `engine/cmd` `{"cmd":"start"}` → `running`: the fire grid is created at that moment (= 16:20, the black at its start state). `{"cmd":"reset"}` → back to `ready` (clears fire points too). The old "clock starts at the first sensor reading" rule is dropped.
- **Initial plan.** At Start (first running step) the active route is the fastest route that isn't red (the black on the 2013 data). It then changes only when it turns red and another escape route isn't ("REROUTED" event, as today). Fixes the old behaviour, which re-picked the fastest route on every step while there were no spot fires. (Was `config.INITIAL_ROUTE = "ranch"`; dropped Nov 8.)
- **engine/state additions:** `phase` (`"ready"` / `"running"`), `started_ts` (unix s or null), `scenario_start` (`"16:20"`), `replan_ts` (unix s of the last reroute, or of Start; the dashboard animates when it changes). Per escape route: `t_crew_min` and `t_fire_min`, lists aligned with `path` (minutes from `ts`; `t_fire_min` is the same safety-factored fire time the margin uses; `null` = fire never reaches that point). Existing fields stay.

**Command path (`ws_server.py`)**
- `POST /api/cmd` with a JSON body; `cmd` must be one of `start`, `reset`, `fire` (others → 400). Publishes the body unchanged to `engine/cmd` with the server's existing MQTT client. No auth: FireNet is a closed network (demo only).

**Map data (new `render_map_layers.py`, run once on a laptop; outputs committed to `data/yarnell/`)**
- `terrain.png`: hypsometric colour ramp from `dem.tif` (desert palette) multiplied by the hillshade. `fuel.png`: FBFM40 classes coloured (grass, grass-shrub, shrub/SH5 chaparral, timber, non-burnable), with the 2013 replay override (SH5 inside the 2013 perimeter), semi-transparent. Same 500 × 500 grid and corners as `hillshade.png` (`meta.json`).
- `map_osm.geojson`: roads/tracks and buildings from `osm.geojson`, cropped to the map, properties trimmed.
- Runtime stays offline: no tile servers, no web fonts.

**Dashboard (`dashboard/src/`)**
- **No MapLibre text labels:** text symbols need a glyph server, which FireNet doesn't have. Labels, tooltips and banners are HTML (`maplibregl.Marker`, `maplibregl.Popup`, React).
- **Backdrop:** `terrain.png` by default, `fuel.png` as a toggle, hillshade no longer desaturated; roads/tracks as thin lines, buildings as small fills.
- **Places:** labelled markers from `scenario.geojson`: "Boulder Springs Ranch" + a 100 m safety zone circle, "2013 deployment site". The `sites.geojson` layer is removed (observation deck, Hotshot Journey Trailhead, town centre). The lunch spot is not drawn. The 2013 descent line stays, labelled "2013 crew route".
- **Controls:** Start / Reset buttons and the scenario clock in the header; a "Ready: press Start" overlay on the map in `ready`.
- **Node tooltips:** hover (tap on touch) a sensor node → popup with id, label, status, age, temp, RH, wind speed + direction (N1), seq. The "Sensor feed" side panel is removed; the header keeps "N / 3 live".
- **Route tooltips:** hover an escape route → popup for the nearest path point: "Crew here in X min (16:mm)", "Fire here in Y min (16:mm)" or "Fire not expected", "Margin here Z min". The tightest point (minimum of `t_fire_min − t_crew_min`) gets a ring marker.
- **Re-plan animation:** when `replan_ts` changes: a "Re-planning escape route…" banner (~1.5 s), a pulse ring expanding from the crew, then the active route drawn from the crew to the safety zone (`line-gradient` over `line-progress`, source `lineMetrics: true`); the cut route stays red and dashed. Visual only (the engine step takes ~40 ms on the Pi).

**Glossary change:** "Active route" becomes "the escape route the crew is following: the fastest safe route at Start, then changes only by a reroute" (was "the escape route with the best margin right now").

## Testing Decisions

- Seam B (engine step, `test_margin.py`, same assert-only style):
  - `ready`: no fire areas, margins `null`, no alerts; after `start`, the black appears and margins are numbers; `reset` returns to `ready`.
  - On the 2013 data: SSW wind → both green, active = black; outflow from 300° at 7.5 m/s → ranch red, black green, active stays black, no reroute. Wind from 90° at 4 m/s → black red, ranch green, "REROUTED: black -> ranch", `replan_ts` updated; wind back → stays on the ranch.
  - Each route's `t_crew_min` / `t_fire_min` have the length of `path`, and the minimum of their difference equals `margin_min`.
  - The existing NNE → "no safe route" check stays.
- Manual: `POST /api/cmd` (start, reset, an unknown cmd → 400); the dashboard in the end-to-end rehearsal on the Pi (`npm run build`, copy `dist`).

## Out of Scope

- The simulated crew walking the 2013 line (escape-margin spec story 35), the hot-sensor rule and `WIND_SCALE` (escape-margin spec): still wanted, tracked there.
- Aerial imagery backdrop (optional ticket 12; needs a one-time download while online).
- Showing the engine's real search (Dijkstra frontier) in the animation.

## Further Notes

- Deadline Sun Nov 8, 12:00. Priority for the demo: tickets 01, 02, 04, 05 (Start + reroute), then 03 + 10 (route times), 09, 06, 07 + 08, 11.
- Engine tickets touch `engine.py` / `margin.py`, which Yugesh is also editing; dashboard tickets touch `App.tsx` (Glenn). Agree who takes each before starting, to avoid merge conflicts.
- After any dashboard change: `npm run build` on a laptop, copy `dashboard/dist` to the Pi (`docs/pi-setup.md` step 2). After engine / ws_server changes: copy the `.py` files and restart the services (step 4).
- `~/fire` on the Pi is not a git clone; code reaches it by `scp`.
