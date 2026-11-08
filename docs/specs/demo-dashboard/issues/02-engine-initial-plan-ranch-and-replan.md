# 02 Engine: active route picked once at Start; changes only by a reroute

Status: resolved
Type: task
Blocked by: none

Spec: `../spec.md` (Engine → Initial plan; Demo story; Glossary change).

## What
- ~~`config.INITIAL_ROUTE = "ranch"`~~ (dropped Nov 8 ~06:00: the crew starts on the fastest route that isn't red, the black).
- Active route: fastest non-red at the first running step after Start; changes only when it turns red and another escape route isn't ("REROUTED" event, existing text). Remove the branch that re-picks the fastest route every step while there are no spot fires (`if not self.fires or self.active is None`).
- `engine/state.replan_ts`: unix s of the last reroute; set at Start too (so the dashboard draws the first plan).
- Update `docs/GLOSSARY.md` "Active route" (text in the spec).

## Files
`config.py`, `engine.py`, `test_margin.py`, `docs/GLOSSARY.md`.

## Acceptance
- New checks on the 2013 data: start, SSW 2.5 m/s from 200° → both green, active = black; 7.5 m/s from 300° → ranch red, black green, active stays black, no reroute. 4 m/s from 90° → black red, ranch green, "REROUTED: black -> ranch", `replan_ts` = that step's time; wind back → stays on the ranch.
- Existing NNE → "NO SAFE ROUTE" check still passes.

## Comments

- Done (Nov 8 ~05:50): `config.INITIAL_ROUTE`, `config.SCENARIO_START`; active route picked once (initial / fallback fastest non-red), then only rerouted when red; `replan_ts` set at Start and at each reroute. Check `test_graph_2013_story_starts_on_ranch_and_reroutes_to_the_black` (300 deg 7.5 m/s: reroute on the first outflow step). Glossary updated.
- Nov 8 ~06:00, changed (Vivek): `INITIAL_ROUTE` removed; the active route is the fastest non-red route at Start (the black), then only rerouted. Checks `test_graph_2013_story_starts_on_the_black_and_keeps_it`, `test_graph_black_cut_reroutes_to_the_ranch`. 28 checks pass.
