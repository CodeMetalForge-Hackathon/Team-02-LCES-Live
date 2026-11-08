#!/usr/bin/env bash
# Start live MQTT ingestion and the WebSocket bridge together on the Pi.
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env and set DATABASE_URL." >&2
  exit 1
fi

set -a
# .env is project-owned configuration, not untrusted input.
source ./.env
set +a

case "${ALLOW_SIMULATOR_DATA:-false}" in
  1|true|TRUE|yes|YES)
    echo "ALLOW_SIMULATOR_DATA must be false for a live-data run." >&2
    exit 1
    ;;
esac

uv run python ingest.py &
ingest_pid=$!
uv run uvicorn ws_server:app --host "${WEB_HOST:-0.0.0.0}" --port "${WEB_PORT:-8000}" &
web_pid=$!

cleanup() {
  kill "$ingest_pid" "$web_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Live ingestion and WebSocket bridge are running. Press Ctrl+C to stop both."
wait "$ingest_pid" "$web_pid"
