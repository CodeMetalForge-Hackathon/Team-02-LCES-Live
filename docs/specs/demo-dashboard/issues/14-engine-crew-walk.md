# 14 Engine: simulated crew walks the active route

Status: resolved
Type: task
Blocked by: none

Spec: `../spec.md`. Escape-margin spec story 35: the crew marker stayed at the descent point.

## What
- After Start (graph mode, `config.CREW_WALK`), every step the crew moves `now − last step` seconds along the active route at the modelled walking times (`seg_s`, Tobler × travel factor); routes are then re-planned from the new position, so ETAs and margins shrink as the crew walks. Follows a reroute.
- Decided Nov 8 (Vivek): along the active route, not the 2013 descent line.
- A real `crew/<id>/pos` stops the simulated walk (until Reset). Reaching the end raises "Crew reached <zone>". `engine/state.crew` gets `walking`, `arrived`.

## Comments

- Done (Nov 8 ~06:35): Check `test_crew_walks_the_active_route_after_start_and_stops_on_real_positions` (~1 min ETA drop per minute walked). Demo run: ~1.1 m/s, ~330 m in 5 min; the black is ~19 min away so the crew doesn't arrive during the demo.
