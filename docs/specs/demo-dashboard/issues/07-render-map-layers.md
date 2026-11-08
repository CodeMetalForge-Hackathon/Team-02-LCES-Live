# 07 Data: render terrain.png, fuel.png, map_osm.geojson

Status: ready-for-agent
Type: task
Blocked by: none

Spec: `../spec.md` (Map data).

## What
New `render_map_layers.py` (numpy + rasterio, already dependencies), run once on a laptop, outputs committed:
- `data/yarnell/terrain.png`: RGB, hypsometric ramp over the DEM range (low = warm tan, mid = olive, high = grey-brown) × hillshade (from `hillshade.png` or recomputed), mild contrast boost. Same 500 × 500 grid as `hillshade.png`.
- `data/yarnell/fuel.png`: RGBA by FBFM40 group (grass 101–109 light yellow, grass-shrub 121–124 khaki, shrub 141–149 olive / SH5 dark olive, timber 16x/18x green, non-burnable 91–99 transparent), alpha ~0.55; 2013 replay override: SH5 inside `perimeter.geojson`.
- `data/yarnell/map_osm.geojson`: `highway` lines and `building` polygons from `osm.geojson` inside the map bounds, properties reduced to `kind`.
- Print a one-line summary per output.

## Files
`render_map_layers.py` (new), the three outputs.

## Acceptance
- PNGs open, 500 × 500, line up with `hillshade.png` (same corners in `meta.json`); `map_osm.geojson` < 300 KB.
