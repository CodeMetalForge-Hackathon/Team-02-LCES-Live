# 10 Dashboard: escape route tooltips with crew and fire times

Status: resolved
Type: task
Blocked by: 03

Spec: `../spec.md` (Dashboard → Route tooltips; stories 7, 8).

## What
- Hover an escape route → popup at the nearest `path` point: "Crew here in X min (16:mm)", "Fire here in Y min (16:mm)" or "Fire not expected", "Margin here Z min" (coloured by the thresholds 5 / 2 min). Clock times = scenario clock (`16:20 + ts − started_ts + minutes`); before Start, minutes only.
- Footnote in the popup: "fire time includes the 1.5× safety factor".
- Ring marker at each route's tightest point (min of `t_fire_min − t_crew_min`).
- Widen the hover target (invisible 16 px line layer on the same source).

## Files
`dashboard/src/App.tsx`, `dashboard/src/types.ts`, `dashboard/src/styles.css`.

## Acceptance (manual)
- After the reroute, hovering the red ranch route shows fire before crew near the cut; the tightest-point ring sits there.

## Comments

- Implemented (Nov 8 ~05:50), builds; browser check on the Pi still to do. Invisible 16 px `routes-hit` layer; hover/tap → popup at the nearest path point, refreshed every engine/state; ring layer `tightest` per route coloured by status.
