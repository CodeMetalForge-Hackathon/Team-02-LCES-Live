# 05 Dashboard: Start / Reset buttons, scenario clock, ready overlay

Status: resolved
Type: task
Blocked by: 01, 04

Spec: `../spec.md` (Dashboard → Controls; stories 1–4).

## What
- Header: **Start** (enabled in `ready`), **Reset** (always; confirm with a second click within 3 s), scenario clock `16:20 + (ts − started_ts)` as `HH:MM:SS`, shown only when running.
- Buttons `fetch("http://<host>:8000/api/cmd", {method: "POST", body: JSON.stringify({cmd})})` (works from the Vite dev server too).
- Map overlay in `ready`: "Ready: press Start to begin the 16:20 scenario".
- Optional: "Ignite at N2" button (`{"cmd":"fire","node":"N2"}`) as the stage backup (escape-margin spec story 32).

## Files
`dashboard/src/App.tsx`, `dashboard/src/styles.css`, `dashboard/src/types.ts`.

## Acceptance (manual)
- Ready screen → Start → clock runs from 16:20:00, fire areas appear; Reset → ready screen, no fire.

## Comments

- Implemented (Nov 8 ~05:50), builds (`npm run build`); browser check on the Pi still to do. Header: scenario clock (from `engine.ts - started_ts`), Start (enabled only in `ready`), Ignite at N2 (running only), Reset (second click within 3 s). `VITE_API_URL` overrides the `/api/cmd` URL. Ready overlay on the map.
