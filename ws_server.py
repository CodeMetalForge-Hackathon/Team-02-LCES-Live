"""Bridge MQTT messages to dashboard WebSocket clients."""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME") or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD") or None
ALLOW_SIMULATOR_DATA = os.getenv("ALLOW_SIMULATOR_DATA", "false").lower() in {"1", "true", "yes"}

CMD_TOPIC = "engine/cmd"
ALLOWED_CMDS = {"start", "reset", "fire", "plan"}   # dashboard buttons; "crew" etc. stay MQTT-only

clients: set[WebSocket] = set()
latest: dict[str, dict] = {}
mqtt_client: mqtt.Client | None = None      # set in lifespan; used by POST /api/cmd


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"MQTT connection failed: {reason_code}")
            return
        print("MQTT connected")
        client.subscribe("sensors/#")
        client.subscribe("engine/#")
        client.subscribe("crew/#")

    def enqueue(item: dict) -> None:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            print("WebSocket message queue full; dropping message")

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            print(f"Ignoring invalid JSON on {msg.topic}")
            return
        if not isinstance(data, dict):
            print(f"Ignoring non-object JSON on {msg.topic}")
            return
        if data.get("_simulated") and not ALLOW_SIMULATOR_DATA:
            return
        loop.call_soon_threadsafe(enqueue, {"topic": msg.topic, "rx_ts": time.time(), "data": data})

    async def broadcaster() -> None:
        while True:
            item = await queue.get()
            latest[item["topic"]] = item
            for websocket in list(clients):
                try:
                    await websocket.send_json(item)
                except Exception:
                    clients.discard(websocket)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    global mqtt_client
    mqtt_client = client
    task = asyncio.create_task(broadcaster())
    try:
        yield
    finally:
        mqtt_client = None
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        client.loop_stop()
        client.disconnect()


app = FastAPI(title="LCES Live MQTT bridge", lifespan=lifespan)
# The Vite dev server (another port) calls /api/cmd too.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


@app.post("/api/cmd")
async def engine_cmd(body: dict = Body(...)) -> dict:
    """Dashboard Start / Reset / Ignite -> engine/cmd, body published unchanged.

    No auth: FireNet is a closed network and this is a demo. Do not expose port 8000 elsewhere.
    """
    if body.get("cmd") not in ALLOWED_CMDS:
        raise HTTPException(400, f"cmd must be one of {sorted(ALLOWED_CMDS)}")
    if mqtt_client is None or not mqtt_client.is_connected():
        raise HTTPException(503, "MQTT broker not connected")
    info = mqtt_client.publish(CMD_TOPIC, json.dumps(body), qos=0)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        raise HTTPException(503, f"MQTT publish failed (rc {info.rc})")
    return {"ok": True}


@app.get("/api/sensors/latest")
async def latest_messages() -> dict[str, dict]:
    return latest


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    clients.add(websocket)
    try:
        for item in latest.values():
            await websocket.send_json(item)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(websocket)


# When dashboard/dist exists (after `npm run build`), expose it at `/`.
# API and WebSocket routes above are registered first and remain available.
dashboard_dist = Path(__file__).parent / "dashboard" / "dist"
if dashboard_dist.is_dir():
    app.mount("/", StaticFiles(directory=dashboard_dist, html=True), name="dashboard")
