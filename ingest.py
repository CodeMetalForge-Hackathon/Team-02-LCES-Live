"""Store validated MQTT weather heartbeats in PostGIS."""

import json
import math
import os
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import psycopg
from dotenv import load_dotenv

load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME") or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD") or None
DATABASE_URL = os.environ["DATABASE_URL"]
ALLOW_SIMULATOR_DATA = os.getenv("ALLOW_SIMULATOR_DATA", "false").lower() in {"1", "true", "yes"}
MISSING_VALUES = {-1, -99}


def numeric(value: object) -> float | None:
    if value is None or value in MISSING_VALUES:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("numeric value must be finite")
    return number


def save_reading(topic: str, data: dict) -> None:
    if data.get("type") != "wx":
        raise ValueError("unexpected message type")
    node_id = data["node"]
    if not isinstance(node_id, str) or topic != f"sensors/{node_id}/wx":
        raise ValueError("topic/node mismatch")
    seq = int(data["seq"])
    if seq < 0:
        raise ValueError("seq must be non-negative")

    values = (
        node_id, seq, numeric(data.get("temp_c")), numeric(data.get("rh")),
        numeric(data.get("wind_ms")), numeric(data.get("wind_dir")),
        numeric(data.get("flame")), numeric(data.get("batt")), json.dumps(data),
    )
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO nodes (id, last_seen) VALUES (%s, NOW()) "
                "ON CONFLICT (id) DO UPDATE SET last_seen = EXCLUDED.last_seen",
                (node_id,),
            )
            cur.execute(
                "INSERT INTO readings (node_id, seq, temp_c, rh, wind_ms, wind_dir, flame, batt, raw) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (node_id, seq) DO NOTHING",
                values,
            )
    print(f"Saved {node_id} seq={seq}")


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        print(f"MQTT connection failed: {reason_code}")
        return
    client.subscribe("sensors/+/wx")
    print("Subscribed to sensors/+/wx")


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
        if data.get("_simulated") and not ALLOW_SIMULATOR_DATA:
            return
        save_reading(msg.topic, data)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, psycopg.Error) as error:
        print(f"Ignoring {msg.topic}: {error}")


def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
