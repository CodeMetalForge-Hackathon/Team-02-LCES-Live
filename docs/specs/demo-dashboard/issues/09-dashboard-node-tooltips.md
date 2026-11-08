# 09 Dashboard: sensor node tooltips, remove the sensor panel

Status: resolved
Type: task
Blocked by: none

Spec: `../spec.md` (Dashboard → Node tooltips; stories 9, 10).

## What
- Hover (and tap) a sensor node on the map → `maplibregl.Popup`: id, label (from `engine/state.nodes`), status (fresh/aging/stale) + age, temp, RH, wind speed and "from" direction when ≥ 0 (N1), seq. Updates live while open.
- Remove the "Sensor feed" side panel; header keeps "N / 3 live".
- FIRE card: "direction unknown" when `weather.wind_from` is null (today: "from null°").

## Files
`dashboard/src/App.tsx`, `dashboard/src/styles.css`.

## Acceptance (manual)
- Hover N2 → its current temp/RH; unplug N3 → its popup shows stale + growing age.

## Comments

- Done (Nov 8 ~06:35): hover/tap a sensor node → popup (status, age, temp, RH, wind speed + compass direction or "direction unknown", seq), refreshed live. "Sensor feed" panel removed; header shows "N / 3 sensors live" from engine node health.
