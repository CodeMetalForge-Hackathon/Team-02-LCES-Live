# 08 Dashboard: coloured backdrop, fuel toggle, roads and buildings

Status: ready-for-agent
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
