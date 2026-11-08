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
args = parser.parse_args()

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
        print(f"t={int(elapsed)}s sent; shifted={shifted}")
        time.sleep(10)
finally:
    client.loop_stop()
    client.disconnect()
