#!/usr/bin/env python3
"""USB serial -> MQTT bridge, for testing a node without Wi-Fi.

The node firmware (e.g. src/node_n3) prints every heartbeat as
    payload sensors/N3/wx {"type":"wx",...}
This script reads those lines from the USB port and publishes them to a broker,
so you can check the message format / dashboard before the node is on FireNet.

Needs only pyserial (comes with PlatformIO). Run it with PlatformIO's Python:
    ~/.platformio/penv/bin/python tools/serial_bridge.py                 # local broker (localhost)
    ~/.platformio/penv/bin/python tools/serial_bridge.py --host 10.42.0.1  # the Pi's broker

Close `pio device monitor` first: only one program can hold the serial port.
"""
import argparse
import socket
import struct
import sys
import time

import serial


def _str(s: bytes) -> bytes:
    return struct.pack("!H", len(s)) + s


def _remaining_length(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


class TinyMqtt:
    """Just enough MQTT 3.1.1 to CONNECT and PUBLISH at QoS 0."""

    def __init__(self, host, port, client_id):
        self.host, self.port, self.client_id = host, port, client_id
        self.sock = None

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        body = _str(b"MQTT") + bytes([4, 0x02]) + struct.pack("!H", 60) + _str(self.client_id.encode())
        self.sock.sendall(bytes([0x10]) + _remaining_length(len(body)) + body)
        ack = self.sock.recv(4)
        if len(ack) < 4 or ack[0] != 0x20 or ack[3] != 0:
            raise ConnectionError(f"broker refused connection (CONNACK {ack.hex()})")

    def publish(self, topic, payload):
        body = _str(topic.encode()) + payload.encode()
        self.sock.sendall(bytes([0x30]) + _remaining_length(len(body)) + body)

    def close(self):
        if self.sock:
            try:
                self.sock.sendall(b"\xe0\x00")   # DISCONNECT
                self.sock.close()
            except OSError:
                pass
        self.sock = None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyACM0", help="serial port of the node (default /dev/ttyACM0)")
    ap.add_argument("--host", default="localhost", help="MQTT broker (default localhost)")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--quiet", action="store_true", help="don't echo the node's other serial lines")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)   # show lines immediately, even when piped

    mqtt = TinyMqtt(args.host, args.mqtt_port, f"serial-bridge-{int(time.time())}")
    ser = None
    print(f"bridge: {args.port} -> mqtt://{args.host}:{args.mqtt_port}  (Ctrl+C to stop)")
    buf = b""
    while True:
        try:
            if ser is None:
                ser = serial.Serial(args.port, 115200, timeout=0.5)
                print(f"bridge: serial {args.port} open")
            if mqtt.sock is None:
                mqtt.connect()
                print(f"bridge: mqtt connected to {args.host}")
            buf += ser.read(512)
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode(errors="replace").strip()
                if line.startswith("payload "):
                    parts = line.split(" ", 2)
                    if len(parts) == 3:
                        mqtt.publish(parts[1], parts[2])
                        print(f"{time.strftime('%H:%M:%S')} PUBLISHED {parts[1]} {parts[2]}")
                elif line and not args.quiet:
                    print(f"{time.strftime('%H:%M:%S')}   node| {line}")
        except KeyboardInterrupt:
            mqtt.close()
            print("\nbridge: stopped")
            return
        except (serial.SerialException, OSError) as e:
            print(f"bridge: {e.__class__.__name__}: {e}  (retrying in 2 s)")
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
            mqtt.close()
            time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
