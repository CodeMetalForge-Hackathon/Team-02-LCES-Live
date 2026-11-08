"""Publish N1-N3 weather heartbeats for local integration tests."""

import argparse
import json
import random
import time

import paho.mqtt.client as mqtt

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--wind-shift-after", type=int, default=60)
parser.add_argument("--silent-node", default="N3")
parser.add_argument("--silent-after", type=int, default=90)
parser.add_argument("--fire-node", default="N2", help="spot fire placed at this node's position")
parser.add_argument("--fire-after", type=int, default=-1,
                    help="seconds until one spot fire is reported (-1 = never; e.g. 20)")
args = parser.parse_args()
fire_sent = False

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect(args.host, 1883)
client.loop_start()
started = time.monotonic()
sequence = {node: 0 for node in ("N1", "N2", "N3")}

try:
    while True:
        elapsed = time.monotonic() - started
        shifted = elapsed >= args.wind_shift_after
        for node in sequence:
            if node == args.silent_node and elapsed >= args.silent_after:
                continue
            payload = {
                "type": "wx", "node": node, "seq": sequence[node],
                "temp_c": round(random.uniform(28, 33) + (4 if shifted else 0), 1),
                "rh": round(random.uniform(15, 22) - (5 if shifted else 0), 1),
                "wind_ms": round(random.uniform(2, 3) + (5 if shifted else 0), 1),
                "wind_dir": 180 if shifted else 270, "flame": 0, "batt": 90,
                "_simulated": True,
            }
            client.publish(f"sensors/{node}/wx", json.dumps(payload))
            sequence[node] += 1
        if args.fire_after >= 0 and elapsed >= args.fire_after and not fire_sent:
            client.publish("sensors/SIM/fire", json.dumps(
                {"type": "fire", "src": "SIM", "node": args.fire_node, "conf": 0.9, "_simulated": True}))
            fire_sent = True
            print(f"t={int(elapsed)}s spot fire at {args.fire_node}")
        print(f"t={int(elapsed)}s sent; shifted={shifted}")
        time.sleep(10)
finally:
    client.loop_stop()
    client.disconnect()
