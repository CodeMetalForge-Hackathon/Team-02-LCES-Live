# 15 Dashboard: active-route tile, wind arrow, compass, legend

Status: resolved
Type: task
Blocked by: none

Spec: `../spec.md`. Vivek's round-2 list (Nov 8 ~06:20).

## What
- Removed the sensor readiness banner and the Margin engine / Sensor health / Fire tiles; one wide **Active escape route** tile (name coloured by status, crew walking/arrived note, margin, crew ETA, leave-by in scenario time).
- "Wind and fire spread" panel replaces the sensor feed: compass rose with a big arrow pointing where the wind pushes the fire (wind from + 180°), "Fire pushed toward ESE / Wind from WNW (295°) at 4.5 m/s"; unknown direction → arrows in every direction + explanation.
- Compass (N needle) top-right on the map, follows map rotation. Map legend panel for every line, fill and dot. Ready overlay moved to the top of the map.
- Route colours unchanged (green ≥ 5 min, amber 2–5 min or dropping fast, red < 2 min), as decided.

## Comments

- Done (Nov 8 ~06:35): Checked with headless Chrome against a scratch server feeding real engine states (ready, and 150 s into the WNW story). Not yet seen on the Pi.
