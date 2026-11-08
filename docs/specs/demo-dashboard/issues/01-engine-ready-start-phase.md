# 01 Engine: ready phase and Start command

Status: resolved
Type: task
Blocked by: none

Spec: `../spec.md` (Implementation Decisions → Engine → Phase).

## What
- The engine starts in phase `ready`: no `FireGrid`, routes computed without fire (margin `null`, green), no fire alerts. Sensor health and weather still update.
- `engine/cmd` `{"cmd":"start"}` → `running`: create `FireGrid(graph, now)` (= 16:20). A second `start` while running is ignored.
- `{"cmd":"reset"}` → back to `ready`; clears fire points, history, events (as today).
- Remove the "clock starts at the first sensor reading" rule (`engine.py` `compute`, `self.fire is None` branch).
- `engine/state` gets `phase`, `started_ts` (null in ready), `scenario_start: "16:20"`.

## Files
`engine.py`, `test_margin.py`. Update the `docs/pi-setup.md` §5 sentence about the fire clock starting at the first reading.

## Acceptance
- New check: ready → no `areas` / all margins null; after start → `areas.black` non-empty, margins are numbers; reset → ready again.
- All existing checks still pass (graph tests that rely on the old auto-start send `start` first).

## Comments

- Done (Vivek + Claude, Nov 8 ~05:50): `engine.py` phase `ready`/`running`, `{"cmd":"start"}` creates the FireGrid, reset -> ready (also clears events + route-status memory). Fire inputs (flame, spot fire cmd) are ignored in ready. Check `test_ready_phase_no_fire_until_start_then_reset_back_to_ready`; existing graph tests send `start` first. pi-setup.md §5 updated.
