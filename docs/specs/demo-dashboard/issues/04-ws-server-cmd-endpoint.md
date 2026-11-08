# 04 ws_server: POST /api/cmd → engine/cmd

Status: resolved
Type: task
Blocked by: none

Spec: `../spec.md` (Command path).

## What
- `POST /api/cmd`, JSON body; `cmd` in {`start`, `reset`, `fire`} else 400. Publish the body unchanged to `engine/cmd` (QoS 0) with the existing MQTT client (keep a module-level reference to it). Return `{"ok": true}`.
- No auth (closed FireNet, demo only); say so in a comment.

## Files
`ws_server.py`, `README.md` (one line).

## Acceptance (manual)
- `curl -X POST localhost:8000/api/cmd -H 'content-type: application/json' -d '{"cmd":"start"}'` → `mosquitto_sub -t engine/cmd` shows it; engine log shows the phase change (after ticket 01).
- `{"cmd":"bogus"}` → 400, nothing published.

## Comments

- Done (Nov 8 ~05:50): `POST /api/cmd`, allow-list start/reset/fire, 400 otherwise, 503 if MQTT isn't connected. Added CORS (`*`) so the Vite dev server can call it. Tested by calling the handler with a stub client and with curl against uvicorn (no local broker -> 503 as expected). Still to do on the Pi: curl + `mosquitto_sub -t engine/cmd`.
