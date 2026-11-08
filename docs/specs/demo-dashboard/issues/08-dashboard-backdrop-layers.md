# 08 Dashboard: coloured backdrop, fuel toggle, roads and buildings

Status: resolved
Type: task
Blocked by: 07

Spec: `../spec.md` (Dashboard → Backdrop; story 12).

## What
- Image sources `terrain.png` (default, full opacity, no desaturation) and `fuel.png` (hidden by default), same `corners` as today.
- Small layer switch on the map: Terrain / Fuel overlay (on/off).
- `map_osm.geojson`: roads/tracks as thin light lines, buildings as small fills, under the fire areas and routes.
- Check the fire areas and routes still read clearly on the new backdrop (adjust area opacities if needed).

## Files
`dashboard/src/App.tsx`, `dashboard/src/styles.css`.

## Acceptance (manual)
- Map in colour; fuel toggle works; roads line up with the escape routes where they follow tracks.

## Comments
- Done (Nov 8 ~09:20): `terrain.png` replaces the grey hillshade; "Fuel" button (top right, under the compass) toggles `fuel.png` and shows its colour key; roads (light lines), tracks (dashed), buildings (brown fills) under the fire and routes; dark casing under the routes so they read on the colour. Legend entries added. `npm run build` passes; checked in headless Chrome against the real engine (WNW story, 16:24: fire bands, ranch route red, black green, ranch buildings visible). Assets are bundled by Vite, so only `dashboard/dist` goes to the Pi.
- Update (Nov 8 ~09:20): the Fuel button became a **Layers** panel (top right, under the compass): base map Terrain (elevation, default) / Slope (`slope.png`, new in `render_map_layers.py`, flat green → 45°+ red) / Hillshade (grey); overlays Fuel, Roads and buildings, 2013 fire perimeter; colour key for slope / fuel when shown. Checked in headless Chrome: every base map and overlay toggles, the fire layer (black, burning, 5 / 10 min bands) draws on top of each, no console errors. Note: a `run_demo.sh` started before ticket 16 runs the old engine (sends `areas`, not `fire_grid`): no fire on the new dashboard until it is restarted.
- Update (Nov 8 ~09:40): **wider map**, display only. `render_map_layers.py` now fetches 12 km x 12 km (1200 x 1200 px, 10 m, same UTM grid and centre as `dem.tif`) into `data/yarnell/wide/` (USGS 3DEP, LANDFIRE FBFM40, OSM via Overpass; Overpass is often at capacity: the script backs off and falls back to the 5 km `osm.geojson`, `--osm` retries only that) and renders terrain / slope / hillshade / fuel PNGs + `map_osm.geojson` (224 roads, 88 tracks, 598 buildings) + `wide/meta.json` (corners). The engine still uses `dem.tif` / `fuel.tif` / `graph.npz` (unchanged). Dashboard: backdrop from `wide/`, first view = the engine's 5 km area, `maxBounds` = the 12 km backdrop, `minZoom` 11.5; the fire layer fades out over its outer 25 cells (250 m) so the engine's fire grid doesn't end in a hard box. Checked in headless Chrome on the local `run_demo.sh` stack (whole 2013 perimeter, Yarnell, Hwy 89 visible; slope / fuel / zoom in and out work).
