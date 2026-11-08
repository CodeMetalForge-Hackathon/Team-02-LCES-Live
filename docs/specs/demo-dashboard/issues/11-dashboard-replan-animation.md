# 11 Dashboard: animate the planned route

Status: resolved
Type: task
Blocked by: 02

Spec: `../spec.md` (Dashboard → Re-plan animation; story 6).

## What
- When `engine/state.replan_ts` changes (Start, each reroute). In the demo story (ending revised Nov 8 ~06:05) this plays at **Start** (the route back to the black is drawn); a reroute isn't in the main story any more, so keep the reroute banner but don't polish it:
  1. Banner "Re-planning escape route…" (~1.5 s), then "Rerouted: <safety zone>" for 5 s.
  2. Pulse ring expanding from the crew (CSS on an HTML marker).
  3. The active route drawn progressively from the crew to the safety zone over ~1.5 s: route source with `lineMetrics: true`, active-route layer `line-gradient` from `line-progress` with a moving cut-off, animated by `requestAnimationFrame`.
  4. The previous route stays red and dashed.
- Skip the animation on the first state after a page reload (only animate a change seen live).

## Files
`dashboard/src/App.tsx`, `dashboard/src/styles.css`.

## Acceptance (manual)
- Press Start → banner, pulse, black route drawn; reload during the demo shows the final state without replaying. Reroute path: simulator `--dir-before 135 --dir-after 90` → "REROUTED: black -> ranch", ranch route drawn, black red + dashed.

## Comments
- Done (Nov 8 ~08:45, Vivek's Claude session): `App.tsx` watches `replan_ts`. On a change seen live (not on the first state after a page load): banner "Planning escape route…" (Start) / "Re-planning escape route…" (reroute) for 1.5 s, then "Escape route: <name>" / "Rerouted: <name>" for 5 s; two pulse rings from the crew (HTML marker); the active route drawn crew → safety zone over 1.5 s (`route-draw` source with `lineMetrics`, `line-gradient` step on `line-progress`; the base `routes` layer hides the active route while drawing). The route left at a reroute moves to a dashed `routes-cut` layer until Reset. Legend entry added. `npm run build` passes; checked in headless Chrome against the real engine (Start → black drawn; wind 90° at 4 m/s → "REROUTED: black -> ranch", black dashed red, ranch drawn). (Superseded by Yugesh's version below when merging the two on Nov 8.)
- Done (Nov 8, Yugesh's session): `playReplan` in `App.tsx` (+ `.replan-banner`, `.replan-pulse` in `styles.css`). Plays on a `replan_ts` change (Start: "Escape route: …"; reroute: "Rerouted: …") and on a route's `replanned_ts` change (ticket 17: "…: re-planned around the fire"). Old path dashed in its old colour for 5 s. Not replayed after a page load. Checked in Chrome against the real engine: Start, and the hot N2 detour at 7.5 m/s SSW.
- Nov 8, later: banners removed at the user's request (the alert list carries the text); route drawing, pulse and dashed old path kept. Status word removed (the dot shows the route colour); active-route panel shows DISTANCE (path length), route tooltip shows metres from the crew.
