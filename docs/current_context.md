# FireGuard — Team Context & Status (Nov 7)

Nov 7, 2026 · @Vivek Choudhary

## Project summary

We are building an offline "escape-route margin" system for wildland fire crews: it answers, every few seconds, "can this crew still reach safety before the fire does, and how many minutes of margin are left?"

**Event:** Code Metal Forge 2026 ("Operation Lost and Found"), BASE-X Boston, sponsored by USSOCOM. Build started Sat 10:00, submissions close Sun 12:00, then live demos. Judged on mission relevance, a working live demo, and deployability.

**How it works**

- Cheap sensor nodes along the escape route report temperature, humidity, flame detection, wind speed/direction and battery every 10 s.
- A thermal-camera "lookout" detects spot fires.
- A Raspberry Pi 5 computes, per route: margin = the smallest value along the route of (fire arrival time − crew arrival time). Route turns green / amber / red; red triggers a reroute and an alert.
- Every reading shows its age. A node that misses 3 heartbeats turns grey and the engine assumes worst-case values for it.

**Why it matters:** the 2013 Yarnell Hill fire investigation (19 firefighters died) highlighted gaps in real-time information about weather, fire and crew location. Pitch wording: our system targets those gaps. Do not claim it would have prevented the tragedy.

**Objectives fit:** strong on Map changing conditions and Deliver critical information, good on Detect hazards, weak on Locate people unless we add the crew tag / "can rescuers still reach the trapped hiker safely" angle.

**Demo:** tabletop terrain model. A fan simulates a wind shift and a hair dryer/heat gun (never open flame) simulates a spot fire. The margin collapses, the route flips green → red, an alternate route appears, an alert fires.

## Architecture and message format

Every component talks through the Mosquitto broker on the Pi, so the simulator and the real nodes are interchangeable.

&#91;embedded content: System architecture · nodes → MQTT → database and engine → WebSocket → dashboard\]

The engine publishes its results back to MQTT (`engine/state`), so the WebSocket server only needs to subscribe to MQTT.

| MQTT topic | Sent by | Content |
| --- | --- | --- |
| `sensors/<node>/wx` | Sensor nodes, simulator | Weather heartbeat every 10 s |
| `sensors/<src>/fire` | Thermal lookout | Spot-fire event with tabletop position |
| `crew/<id>/pos` | Crew tag (optional) | Crew position |
| `engine/state` | Margin engine | Route margins and colours (format TBD) |

**Message format** (JSON; `-1` / `-99` = no data yet; the Pi adds its own receive time because nodes have no reliable clock):

```json
{"type":"wx","node":"N1","seq":184,"temp_c":31.2,"rh":18.0,"wind_ms":-1,"wind_dir":-1,"flame":-1,"batt":-1}
{"type":"fire","src":"THERM1","x":42.0,"y":18.5,"conf":0.9}
{"type":"crew","id":"C1","x":10.0,"y":55.0}
```

**Coordinates:** tabletop in centimetres from one corner (x, y). Everything (nodes, trails, safety zones, hotspots, crew) uses this.

**Heartbeat and staleness:** nodes send every 10 s with a counter `seq`; a node silent for 3 heartbeats (about 30 s) is stale, turns grey, and the engine uses worst-case values for it.

## Current status and owners

As of Sat 17:30: node 1 reads temperature/humidity, and the Pi runs MQTT plus a simulator that publishes fake heartbeats for N1–N3.

| Area | Status | Owner | Next step |
| --- | --- | --- | --- |
| Node 1: XIAO ESP32-C5 + DHT11 | Reading temp/humidity on Serial | Teammate | Heartbeat JSON, then Wi-Fi + MQTT publish |
| Pi 5 base: OS, packages, Mosquitto | Done and verified | Vivek | — |
| Laptop ↔ Pi SSH over Ethernet | Done, VS Code Remote-SSH works | Vivek | Share access with the team |
| Simulator (fake N1–N3) | Running, verified | Vivek | Teammates copy it for local dev |
| PostGIS database + ingest | Not started | Vivek | Steps 5–6 (create DB, write ingest.py) |
| WebSocket server (FastAPI) | Not started | Teammate | See "Next task: WebSocket server" below |
| Dashboard (MapLibre) | Not started | TBD | Consume the WebSocket feed |
| Margin engine | Not started | Vivek | After ingest: staleness, trail graph, fire/escape model |
| Flame + wind sensors | Not started | TBD | Wind sensor not in hardware list: improvise (cups + Hall sensor, vane + magnetometer) |
| LoRa link | Not started | TBD | Module model unknown; RadioLib likely |
| Thermal lookout | Not started | TBD | Get the thermal camera from the shared library first |
| Tabletop terrain model | Not started | TBD | Cardboard/foam, photo from above becomes the map |

## Node 1: XIAO ESP32-C5 + DHT11

Node 1 is a Seeed Studio XIAO ESP32-C5 reading a Grove DHT11 on pin D0 (GPIO1), powered from 3V3.

**Wiring** (Grove-to-male-jumper cable, XIAO straddling the breadboard's center gap)

| DHT11 pin | Wire | XIAO pin |
| --- | --- | --- |
| GND | Black | GND |
| VCC | Red | 3V3 (never 5V) |
| SIG | Yellow | D0 = GPIO1 |
| NC | White | Not connected |

With USB-C at the top: left side = D0…D6 (top to bottom), right side = 5V, GND, 3V3, D10…D7. With USB-C pointing toward you the sides swap: D0 is the right-side pin next to the USB, GND/3V3 are the 2nd/3rd left-side pins from the USB end.

**XIAO pin map:** D0 = GPIO1, D1 = GPIO0, D2 = GPIO25 (strapping, avoid), D3 = GPIO7 (strapping, avoid), D4 = GPIO23 (SDA), D5 = GPIO24 (SCL), D6 = GPIO11 (TX). Avoid strapping pins GPIO2, 3, 7, 25, 26, 27, 28 for sensors.

**Arduino IDE settings:** Arduino IDE 2.3.10, board "ESP32C5 Dev Module", port COM3, Serial Monitor at 115200. Libraries: "DHT sensor library" by Adafruit + "Adafruit Unified Sensor". With the Dev Module board selected, use `#define DHTPIN 1` (the `D0` name only exists if board "XIAO\_ESP32C5" is selected). Fallback library if reads fail: "DHT sensor library for ESPx" by beegee\_tokyo.

**Lessons learned**

- The Output panel only shows compile/upload messages; program prints appear in the Serial Monitor.
- `#define DHTPIN 25` was wrong (that is D2, a strapping pin) and caused "Sensor reading failed".
- Grove-to-Grove cables do not fit the XIAO; use Grove-to-male-jumper into the breadboard.
- If the XIAO sits on one half of the breadboard, left and right pins short together. It must straddle the center gap.
- Upload stuck: hold BOOT, tap RESET, release BOOT, upload again.

**Next for the node:** heartbeat JSON every 10 s in the agreed format (wind/flame/batt = -1 for now), then Wi-Fi + MQTT publish to `sensors/N1/wx` (PubSubClient by Nick O'Leary).

## Raspberry Pi 5 setup

The Pi runs Raspberry Pi OS based on Debian 13 "Trixie" (64-bit, aarch64), user `thunder`, with Mosquitto and PostgreSQL 17 installed and active.

**Installed system packages** (Trixie uses PostgreSQL 17, not 15)

```
sudo apt install -y mosquitto mosquitto-clients \
  postgresql postgis postgresql-17-postgis-3 postgresql-17-pgrouting \
  python3-venv git
```

**Mosquitto config** in `/etc/mosquitto/conf.d/local.conf`:

```
listener 1883
allow_anonymous true
```

Verified listening on `0.0.0.0:1883` and `[::]:1883` (accepts other devices, not just localhost). Enabled at boot.

**Python project (uv)** at `/home/thunder/fire`: created with `uv init --python 3.13`, environment in `.venv`, dependencies tracked in `pyproject.toml` + `uv.lock`. Intended libraries: paho-mqtt, pyserial, fastapi, uvicorn\[standard\], websockets, psycopg\[binary\], shapely, numpy, opencv-python-headless (optional until the thermal camera). Check what is actually installed with `uv pip list`. Teammates reproduce the environment with `uv sync`. Run scripts with `uv run python <script>.py`.

**Files so far:** `pyproject.toml`, `uv.lock`, `README.md`, `src/`, `simulator.py`.

**Pi 5 tips:** power with the official 5V/5A (27W) supply; video only via the micro-HDMI port nearest the USB-C power port; the power button on the board edge shuts down (one press) and starts (press again, LED red → green).

## Networking and SSH

The venue Wi-Fi blocks device-to-device traffic, so we reach the Pi over a direct Ethernet cable at `10.42.0.1`; the Pi still uses the venue Wi-Fi for internet.

**What we found:** laptop `10.1.200.193` and Pi `10.1.200.217` (both on venue Wi-Fi) could not ping each other ("Destination Host Unreachable"). That is client isolation. No device on the venue Wi-Fi can reach the Pi, including teammates' laptops and the ESP32.

**Direct Ethernet link** (one-time, on the Pi):

```
sudo systemctl enable --now ssh
sudo nmcli con add type ethernet ifname eth0 con-name direct ipv4.method shared
sudo nmcli con up direct
```

`ipv4.method shared` makes the Pi a mini router on the cable: Pi = `10.42.0.1`, the laptop gets an address automatically.

**Connect from a laptop:** plug the cable in, then `ssh thunder@10.42.0.1`. Password: ask Vivek (reset on the Pi with `sudo passwd thunder`; not stored in this doc).

**Passwordless login:** reuse your existing key (don't overwrite it with `ssh-keygen`). From WSL/Linux: `ssh-copy-id thunder@10.42.0.1`. From Windows PowerShell (no ssh-copy-id):

```
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh thunder@10.42.0.1 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

WSL and Windows keep separate keys; VS Code (a Windows app) uses the Windows one.

**VS Code Remote-SSH:** install the "Remote - SSH" extension → F1 → Remote-SSH: Connect to Host → `thunder@10.42.0.1` → Open Folder `/home/thunder/fire`. Install the Python extension "in SSH: 10.42.0.1" and select the `./.venv` interpreter. Terminal → New Terminal opens shells on the Pi; split them for side-by-side tests.

**Pi venue Wi-Fi IP** was `10.1.200.217` (may change; `hostname -I` on the Pi).

**FireNet hotspot (planned, not yet on):** when the ESP32 nodes need to reach the Pi, run `sudo nmcli device wifi hotspot ssid FireNet password firenet123` on the Pi. The Pi then loses venue Wi-Fi internet. Because `eth0` already uses `10.42.0.x`, the hotspot will likely get a different range; check with `ip addr show wlan0` and give that IP to the ESP32 code as `BROKER`.

## Simulator

`simulator.py` pretends to be nodes N1–N3 so the backend and dashboard can be built before the hardware is ready; it is verified working on the Pi.

Every 10 s it publishes one heartbeat per node to `sensors/<node>/wx` in the agreed format, with small random variation. It also acts out the two demo situations:

- **Wind shift** after `--wind-shift-after` seconds: direction 270° → 180°, speed about 2 → 7.5 m/s, temperature +4 °C, humidity −5%.
- **Silent node** after `--silent-after` seconds: `--silent-node` (default N3) stops sending, to test the grey/stale logic.

```python
import json, time, random, argparse
import paho.mqtt.client as mqtt

p = argparse.ArgumentParser()
p.add_argument("--wind-shift-after", type=int, default=60)
p.add_argument("--silent-node", default="N3")
p.add_argument("--silent-after", type=int, default=90)
a = p.parse_args()

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.connect("localhost", 1883)
c.loop_start()

seq = {"N1": 0, "N2": 0, "N3": 0}
t0 = time.time()
while True:
    elapsed = time.time() - t0
    shifted = elapsed > a.wind_shift_after
    for node in seq:
        if node == a.silent_node and elapsed > a.silent_after:
            continue
        msg = {
            "type": "wx", "node": node, "seq": seq[node],
            "temp_c": round(random.uniform(28, 33) + (4 if shifted else 0), 1),
            "rh": round(random.uniform(15, 22) - (5 if shifted else 0), 1),
            "wind_ms": round(random.uniform(2, 3) + (5 if shifted else 0), 1),
            "wind_dir": 270 if not shifted else 180,
            "flame": 0, "batt": 90,
        }
        c.publish(f"sensors/{node}/wx", json.dumps(msg))
        seq[node] += 1
    print(f"t={int(elapsed)}s sent  shifted={shifted}")
    time.sleep(10)
```

**Run and watch (two terminals on the Pi):**

```
mosquitto_sub -h localhost -t 'sensors/#' -v
cd ~/fire && uv run python simulator.py --wind-shift-after 30 --silent-after 60
```

`mosquitto_sub` is a listener: it sits silent until a message arrives and never "finishes" (Ctrl+C to stop).

**Verified output:** seq 0–2 calm at 270°, about 2 m/s; seq 3 (t = 30 s) wind 180°, about 7.5 m/s, temperature up, humidity down; N3 stops after 60 s.

**Local dev on your own laptop** (needed because the venue Wi-Fi blocks access to the Pi): install Mosquitto locally, copy `simulator.py`, run it the same way. Topics and format are identical, so your code works unchanged when pointed at the Pi.

## Next task: WebSocket server

The WebSocket server (FastAPI) subscribes to MQTT and pushes every message to the dashboard in real time; build it on your laptop against the simulator, then move it to the Pi.

**Contract with the dashboard** (each WebSocket message):

```json
{"topic": "sensors/N1/wx", "rx_ts": 1762554000.12, "data": {"type": "wx", "node": "N1", "seq": 4, "temp_c": 30.8, "rh": 16.2, "wind_ms": 2.1, "wind_dir": 270, "flame": 0, "batt": 90}}
```

`rx_ts` = server receive time in Unix seconds (the nodes have no reliable clock). On connect, the server first sends the latest message per topic, so a reloaded dashboard is not empty. Later the engine will publish `engine/state` (route margins and colors) and it flows through the same pipe.

**Skeleton** (`ws_server.py`):

```python
import asyncio, json, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import paho.mqtt.client as mqtt

BROKER = "localhost"
clients: set[WebSocket] = set()
latest: dict[str, dict] = {}
queue: asyncio.Queue = asyncio.Queue()
loop = None

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
    except json.JSONDecodeError:
        return
    item = {"topic": msg.topic, "rx_ts": time.time(), "data": data}
    loop.call_soon_threadsafe(queue.put_nowait, item)   # MQTT thread -> asyncio

async def broadcaster():
    while True:
        item = await queue.get()
        latest[item["topic"]] = item
        for ws in list(clients):
            try:
                await ws.send_json(item)
            except Exception:
                clients.discard(ws)

@asynccontextmanager
async def lifespan(app):
    global loop
    loop = asyncio.get_running_loop()
    m = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    m.on_message = on_message
    m.connect(BROKER, 1883)
    m.subscribe("sensors/#")
    m.subscribe("engine/#")
    m.loop_start()
    task = asyncio.create_task(broadcaster())
    yield
    task.cancel()
    m.loop_stop()
    m.disconnect()

app = FastAPI(lifespan=lifespan)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    for item in list(latest.values()):
        await ws.send_json(item)
    try:
        while True:
            await ws.receive_text()   # keep the connection open
    except WebSocketDisconnect:
        clients.discard(ws)
```

**Run it** (with Mosquitto + `simulator.py` running):

```
uv add fastapi "uvicorn[standard]" paho-mqtt
uv run uvicorn ws_server:app --host 0.0.0.0 --port 8000
```

**Test in a browser:** open any page, press F12 → Console, paste:

```
const ws = new WebSocket("ws://localhost:8000/ws"); ws.onmessage = e => console.log(JSON.parse(e.data));
```

You should see a new object every 10 s per node, `wind_dir` flipping to 180 after the shift, and N3 messages stopping.

**Deploy on the Pi later:** copy `ws_server.py` into `~/fire`, run the same `uvicorn` command; the dashboard connects to `ws://<Pi IP>:8000/ws` (`10.42.0.1` over the cable, or the FireNet IP once the hotspot is on).

## Next steps, open questions, gotchas

**Next steps**

- [ ] Vivek: create PostGIS database `fire` (`nodes`, `readings` tables) and `ingest.py` (MQTT → database)
- [ ] Teammate: `ws_server.py` working locally against the simulator
- [ ] Node 1: heartbeat JSON, then Wi-Fi + MQTT publish
- [ ] Turn on the FireNet hotspot when node 1 is ready to send; point the ESP32 `BROKER` at the hotspot IP
- [ ] Staleness monitor (fresh / aging / stale from `last_seen`)
- [ ] Trail graph for the tabletop in PostGIS + pgRouting, then the margin engine
- [ ] Grab the thermal camera, LoRa modules and ESP32s from the shared library
- [ ] Build the wind sensor (cups + Hall sensor; vane + magnetometer or AS5600)

**Open questions**

- Which LoRa module does the shared library have (SX1262 or SX1276)?
- Which thermal camera (MLX90640, FLIR Lepton, other)? Decides the Python driver.
- Can teammates reach the Pi before FireNet is on? Only via the Ethernet cable or a shared phone hotspot.
- Is pre-written code / pre-built hardware allowed? Ask the organizers.

**Gotchas**

- Never put 5 V on an ESP32 pin; power sensors from 3V3.
- Avoid strapping pins GPIO2, 3, 7, 25, 26, 27, 28.
- Venue Wi-Fi isolates devices: use the cable (`10.42.0.1`) or FireNet.
- Turning on FireNet takes the Pi off the venue Wi-Fi (no internet on the Pi). Install packages first.
- New terminals: `cd ~/fire` and use `uv run`, or activate `.venv`.
- `mosquitto_sub` looks stuck when nothing is publishing; it is just waiting.
- paho-mqtt 2.x needs `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)`.