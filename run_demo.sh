#!/usr/bin/env bash
# Offline demo on one machine: local broker (Docker) + margin engine + WebSocket bridge + simulator.
# Dashboard: http://127.0.0.1:8000 (ws_server.py serves dashboard/dist; rebuilt here if missing).
# Simulator args pass through, e.g.  ./run_demo.sh --wind-shift-after 30 --turn-s 120 --fire-after 90
set -euo pipefail
cd "$(dirname "$0")"

export MQTT_HOST=127.0.0.1 MQTT_PORT=1883 ALLOW_SIMULATOR_DATA=true   # beats .env (load_dotenv doesn't override)

docker rm -f lces-mosquitto >/dev/null 2>&1 || true
docker run -d --rm --name lces-mosquitto -p 1883:1883 \
  -v "$PWD/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" eclipse-mosquitto:2 >/dev/null
[[ -d dashboard/dist ]] || (cd dashboard && npm install --no-audit --no-fund && npm run build)

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; docker rm -f lces-mosquitto >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

sleep 1
uv run --group engine python engine.py --allow-sim & pids+=($!)
uv run uvicorn ws_server:app --host 0.0.0.0 --port 8000 --log-level warning & pids+=($!)
# N3 stays up by default (the "unplug N3" beat is its own step: pass --silent-after <s>)
uv run python simulator.py --silent-after 100000 "$@" & pids+=($!)

echo "Dashboard: http://127.0.0.1:8000   (Ctrl+C stops everything)"
wait
