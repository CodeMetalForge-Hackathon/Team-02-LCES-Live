# 16 Smooth fire animation over a larger fire grid

Status: resolved
Type: task
Blocked by: none

Spec: `../spec.md`. Vivek, Nov 8 ~08:00: "make fire spread animation smoother; show it spread in the upper areas".

## What
- Fire grid enlarged from 200 × 200 to 335 × 322 cells (`build_graph.CROP_BOUNDS`, up to the top of `dem.tif`): the 2013 black and the live front now cover the upper map instead of a 2 km box. `graph.npz` rebuilt (~9.6 MB). Non-burnable cells inside black0 get no arrival time (still black via `black0`).
- `engine/state.areas` (polygons, 1 s steps) replaced by `fire_grid` (arrival time per cell, `margin.fire_grid_msg`); the dashboard redraws the fire every frame at the interpolated scenario time (`dashboard/src/fireLayer.ts`, MapLibre canvas source). Legend: black, burning, within 5 min, 5–10 min.

## Comments
- Done (Nov 8 ~08:30): 31 checks, `build_graph.py --test`, `npm run build` pass; checked in headless Chrome against the real engine on the WNW story (16:25, ranch red, black green, margin 15.3). Design doc §5.5 "Update Nov 8 ~08:30". Engine step ~130 ms laptop (was 40–60), ~730 ms on a weather change.
