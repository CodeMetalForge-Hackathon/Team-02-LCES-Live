# LCES Live dashboard

The dashboard uses the existing local Yarnell hillshade, perimeter, and sites data and receives live updates from `ws_server.py`.

```powershell
cd dashboard
npm install
npm run dev -- --host 0.0.0.0
```

Open the URL printed by Vite (normally `http://127.0.0.1:5173`). It connects to `ws://<current-host>:8000/ws` by default. Set `VITE_WS_URL` only when the WebSocket server is on a different host.

Temporary routes are in `src/App.tsx`. Replace them with `data/yarnell/routes.geojson` when Person A delivers it. Person B publishes route state to MQTT topic `engine/state`; `ws_server.py` forwards it to the dashboard.
