# 13 Engine: smooth the wind before the fire model

Status: resolved
Type: task
Blocked by: none

Spec: `../spec.md`. N1's wind readings jump around (fan gusts, La Crosse vane), so the fire grid and the dashboard arrow jumped too.

## What
- Per node, exponential moving average with time constant `config.WIND_SMOOTH_S` (20 s); the direction averaged as a unit vector (350° and 10° → 0°, not 180°).
- Smoothed direction rounded to `WIND_DIR_STEP_DEG` (5°), speed rounded up to `WIND_SPEED_STEP_MS` (0.5 m/s, conservative). `engine.weather` uses the smoothed values for fresh nodes; `engine/state.weather` therefore shows the smoothed wind.
- Average restarts if a node was silent for `STALE_S`.

## Comments

- Done (Nov 8 ~06:35): Checks `test_wind_direction_is_smoothed_as_a_vector`; the v1 reroute check now holds the shifted wind for 60 s. A sharp 90° change takes ~40 s to show fully; the 2013 story steps (60 s) are unaffected. Each 5° step still re-runs the fire grid (~250 ms on the laptop): watch `(… ms)` in the Pi's engine log; raise the step to 10° if slow.
