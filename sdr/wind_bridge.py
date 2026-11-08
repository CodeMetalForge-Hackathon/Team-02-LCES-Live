"""La Crosse TX145WSDTHv3 (433 MHz) -> MQTT `sensors/<node>/wx`, same format as the ESP32 nodes and simulator.py.

  rtl_433 -f 433.92M -R 73 -F json | SENSOR_ID=51625 python3 wind_bridge.py
  python3 wind_bridge.py --test

rtl_433 reports this sensor as "LaCrosse-TX141W" and alternates two packets: wind (speed + direction)
and temp/RH. We keep the latest of each and publish one full heartbeat per packet received, so a wind
packet never blanks the temperature (or the other way round). No packets = no heartbeats = node goes stale.
"""
import json, os, sys

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
NODE_ID = os.environ.get("NODE_ID", "W1")
SENSOR_ID = os.environ.get("SENSOR_ID")  # rtl_433 "id"; changes when the batteries come out. Unset = accept any.
# Vane reads relative to the housing: correct when the solar panel faces south (N on the label points north).
# Mounted another way? Add the turn here (degrees).
DIR_OFFSET = float(os.environ.get("DIR_OFFSET", "0"))

state = {"type": "wx", "node": NODE_ID, "seq": 0, "temp_c": -99, "rh": -1,
         "wind_ms": -1, "wind_dir": -1, "flame": -1, "batt": -1}


def update(pkt):
    """Fold one rtl_433 packet into `state`. Returns True if it was ours."""
    if pkt.get("model") != "LaCrosse-TX141W" or (SENSOR_ID and str(pkt.get("id")) != SENSOR_ID):
        return False
    if "wind_avg_km_h" in pkt:
        state["wind_ms"] = round(pkt["wind_avg_km_h"] / 3.6, 2)
        state["wind_dir"] = round((pkt["wind_dir_deg"] + DIR_OFFSET) % 360)  # direction wind blows FROM
    elif "temperature_C" in pkt:
        state["temp_c"] = round(pkt["temperature_C"], 1)
        state["rh"] = float(pkt["humidity"])
    else:
        return False
    if "battery_ok" in pkt:
        state["batt"] = 100 if pkt["battery_ok"] else 10  # sensor only reports ok/low
    return True


def main():
    import paho.mqtt.publish as publish
    topic = f"sensors/{NODE_ID}/wx"
    for line in sys.stdin:
        try:
            ours = update(json.loads(line))
        except (ValueError, KeyError, TypeError):
            continue  # rtl_433 noise or a half-decoded packet
        if not ours:
            continue
        msg = json.dumps(state)
        state["seq"] += 1
        print(topic, msg, flush=True)
        try:
            publish.single(topic, msg, hostname=MQTT_HOST)  # ponytail: new connection per packet, fine at this rate
        except OSError as e:
            print("mqtt:", e, file=sys.stderr, flush=True)


def test():
    assert update({"model": "LaCrosse-TX141W", "id": 1, "battery_ok": 1, "wind_avg_km_h": 18.0, "wind_dir_deg": 322})
    assert state["wind_ms"] == 5.0 and state["wind_dir"] == 322 and state["batt"] == 100 and state["temp_c"] == -99
    assert update({"model": "LaCrosse-TX141W", "id": 1, "temperature_C": 21.43, "humidity": 40})
    assert state["temp_c"] == 21.4 and state["rh"] == 40.0 and state["wind_ms"] == 5.0  # wind kept
    assert not update({"model": "Acurite-Tower", "temperature_C": 5})
    print("ok")


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
