# 03 Engine: crew and fire times per route point

Status: resolved
Type: task
Blocked by: none

Spec: `../spec.md` (engine/state additions).

## What
- `margin.evaluate` keeps the per-point arrays it already computes (`t_crew`, `t_fire`) on `RouteResult`.
- Each route in `engine/state` gets `t_crew_min` and `t_fire_min`: lists aligned with `path`, minutes from `ts`, 1 decimal; `null` for points before the crew's join point and for fire = never (inf). No fire (ready phase) → `t_fire_min` all `null`.
- Keep the message small: no other per-point fields.

## Files
`margin.py`, `engine.py`, `test_margin.py`, `dashboard/src/types.ts` (add the two fields).

## Acceptance
- Check: for every route, `len(t_crew_min) == len(t_fire_min) == len(path)`, and min over points of (`t_fire_min − t_crew_min`) equals `margin_min` (± 0.1).
- `engine/state` stays JSON-serialisable (existing check).

## Comments

- Done (Nov 8 ~05:50): `RouteResult.crew_pts` / `fire_pts` (full path length, nan before the join point); state `t_crew_min` / `t_fire_min`. Check `test_per_point_times_align_with_path_and_give_the_margin`. Adds ~1.5 KB to a ~60 KB state message. `types.ts` left for the dashboard ticket.
