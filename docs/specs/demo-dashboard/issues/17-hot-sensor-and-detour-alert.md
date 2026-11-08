# 17 Hair-dryer beat: hot sensor rule + "re-planned around the fire"

Status: resolved
Type: task
Blocked by: none

Spec: `../spec.md`, design §1.4 (hot sensor rule), §5 step 8. Yugesh, Nov 8: "the demo only changes colours". The spot fire at N2 (box canyon) bends the ranch route; the dashboard animates it (ticket 11).

## What
- `engine.on_wx`: a reading ≥ `HOT_TEMP_C` (45 °C), or a rise of more than `HOT_RISE_C` (8 °C) within `HOT_RISE_S` (30 s), puts a fire point at the node ("N2 hot sensor (55 C)"). Ignored before Start; one fire point per node (30 m merge keeps the first time). Hot nodes are left out of temp/RH (the hair dryer would heat the whole map).
- `margin.graph_routes` marks `Route.detour` (path ≠ fire-blind path). `engine.announce_detours`: event "Route <id> re-planned around the fire (<n> min walk)" + per route `replanned_ts` when a detour moved > `ROUTE_CHANGE_M` (50 m) from the last announced one, at most once per `ROUTE_CHANGE_HOLD_S` (20 s).
- `simulator.py --hot-after <s>`: `--fire-node` reads 55 °C from then on. Simulator temp noise narrowed (30–32 °C, +3 at the shift) so it can't trip the rule.
- `margin.fire_areas` deleted (unused since ticket 16).

## Comments
- Done (Nov 8): checks `test_hot_sensor_creates_one_fire_at_the_node_and_is_left_out_of_the_weather`, `test_spot_fire_detour_announces_a_route_replan_once`; 33 pass.
- **The detour needs N1 ≥ ~5.5 m/s from the SSW.** Spot fire at N2 (engine sweep): 3–4 m/s → ranch red −18 min, no way around; 5.5 m/s → re-planned, margin ~0 (red); 7.5 m/s → re-planned, then amber/green ~+5 min. At low wind the beat is still "spot fire → ranch route cut". Measure the fan's `wind_ms` on N1; `WIND_SCALE` (§5.6) would fix a weak fan.
- The spot fire drops the black's margin from ~140 to ~40 min, so the black shows amber ("dropped > 30 %") with a big margin for ~30 s (handoff 5c).
