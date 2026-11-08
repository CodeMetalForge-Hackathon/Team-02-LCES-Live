"""Margin engine service (docs/LCES_Design.md §5).

MQTT in:  sensors/+/wx (weather; flame == 1 = spot fire at that node), sensors/+/fire,
          crew/+/pos, engine/cmd ({"cmd": "reset"} | {"cmd": "fire", "node"|"lat"+"lon"} |
          {"cmd": "crew", "lat", "lon"}).
MQTT out: engine/state every RECOMPUTE_S (format: see build_state / docs §5.5).

Run:  uv run --group engine python engine.py            (MQTT_HOST etc. from .env)
"""
import argparse
import json
import math
import os
import threading
import time
from collections import deque
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

import config
from fire_model import MODEL_NAME, Weather
from margin import FirePoint, Terrain, evaluate, load_routes, spreads_for, status_for, to_lonlat, to_utm

MISSING = {-1, -99}


def _num(data: dict, key: str) -> float | None:
    v = data.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v in MISSING or not math.isfinite(v) else v


def input_files() -> tuple[Path, Path, bool]:
    """(nodes file, routes file, using_placeholder). Real files win over placeholders."""
    nodes, routes = config.NODES_FILE, config.ROUTES_FILE
    placeholder = False
    if not nodes.exists():
        nodes, placeholder = config.PLACEHOLDER_DIR / "nodes.json", True
    if not routes.exists():
        routes, placeholder = config.PLACEHOLDER_DIR / "routes.geojson", True
    return nodes, routes, placeholder


def load_nodes(path: Path) -> dict[str, dict]:
    """nodes.json: {"N1": {"lat": .., "lon": .., "label": ".."}, ...}"""
    nodes = {}
    for nid, n in json.loads(Path(path).read_text()).items():
        if nid.startswith("_"):          # "_comment" etc.
            continue
        x, y = to_utm(n["lon"], n["lat"])
        nodes[nid] = {"label": n.get("label", nid), "lat": n["lat"], "lon": n["lon"], "x": x, "y": y}
    return nodes


class MarginEngine:
    def __init__(self, terrain: Terrain, routes, nodes: dict[str, dict], placeholder: bool = False):
        self.terrain, self.routes, self.nodes, self.placeholder = terrain, routes, nodes, placeholder
        self.latest: dict[str, tuple[dict, float]] = {}      # node -> (last wx message, rx time)
        self.fires: list[FirePoint] = []
        self.crew_xy = tuple(routes[0].xy[0])                # default: start of the first route
        self.active: str | None = None
        self.events: deque[tuple[float, str]] = deque()
        self.history = {r.id: deque() for r in routes}       # route -> (ts, margin)
        self.drop_until: dict[str, float] = {}               # route -> amber-for-fast-drop until
        self.prev_route_status: dict[str, str] = {}
        self.prev_node_status: dict[str, str] = {}
        self._fire_n = 0

    # ---- inputs ---------------------------------------------------------------------
    def event(self, now: float, text: str) -> None:
        self.events.append((now, text))
        print(f"[engine] {text}", flush=True)

    def on_wx(self, data: dict, now: float) -> None:
        node = data["node"]
        self.latest[node] = (data, now)
        if data.get("flame") == 1 and node in self.nodes:
            self.add_fire(self.nodes[node]["x"], self.nodes[node]["y"], now, f"{node} flame sensor")

    def add_fire(self, x: float, y: float, now: float, source: str) -> FirePoint:
        for f in self.fires:
            if math.hypot(f.x - x, f.y - y) < config.FIRE_MERGE_M:
                return f                                     # same fire; keep its first-seen time
        self.terrain.value("dem", x, y)                      # raises if outside the terrain patch
        self._fire_n += 1
        f = FirePoint(f"F{self._fire_n}", x, y, now, source)
        self.fires.append(f)
        self.event(now, f"Spot fire {f.id} detected ({source})")
        return f

    def _xy_from(self, data: dict) -> tuple[float, float]:
        if data.get("node") in self.nodes:
            n = self.nodes[data["node"]]
            return n["x"], n["y"]
        return to_utm(float(data["lon"]), float(data["lat"]))

    def on_fire(self, data: dict, now: float) -> None:
        x, y = self._xy_from(data)
        self.add_fire(x, y, now, data.get("src") or data.get("source") or "fire message")

    def on_crew(self, data: dict, now: float) -> None:
        self.crew_xy = to_utm(float(data["lon"]), float(data["lat"]))

    def on_cmd(self, data: dict, now: float) -> None:
        cmd = data.get("cmd")
        if cmd == "reset":
            self.fires.clear()
            self.active = None
            self.crew_xy = tuple(self.routes[0].xy[0])
            for h in self.history.values():
                h.clear()
            self.drop_until.clear()
            self.event(now, "Reset: fires cleared")
        elif cmd == "fire":
            x, y = self._xy_from(data)
            self.add_fire(x, y, now, data.get("source", "engine/cmd"))
        elif cmd == "crew":
            self.on_crew(data, now)
        else:
            raise ValueError(f"unknown cmd {cmd!r}")

    # ---- node health + weather (§5.1, §5.3) -----------------------------------------
    def node_health(self, now: float) -> dict[str, tuple[str, float | None]]:
        health = {}
        for nid in sorted(set(self.nodes) | set(self.latest)):
            if nid not in self.latest:
                health[nid] = ("stale", None)                # never heard from
                continue
            age = now - self.latest[nid][1]
            status = "stale" if age >= config.STALE_S else "aging" if age >= config.AGING_S else "fresh"
            health[nid] = (status, age)
        return health

    def weather(self, health) -> tuple[Weather, list[str]]:
        """Conservative weather for the fire model. Stale nodes add worst-case values (§5.3)."""
        winds, temps, rhs, notes = [], [], [], []
        wind_from, wind_from_age = None, math.inf
        stale = [nid for nid, (s, _) in health.items() if s == "stale"]
        for nid, (status, age) in health.items():
            if nid not in self.latest:
                continue
            data = self.latest[nid][0]
            w = _num(data, "wind_ms")
            if status == "stale":
                if w is not None:
                    winds.append(w)                          # its last reading still counts
                continue
            if w is not None:
                winds.append(w)
            d = _num(data, "wind_dir")
            if d is not None and age < wind_from_age:
                wind_from, wind_from_age = d % 360, age
            for value, bucket in ((_num(data, "temp_c"), temps), (_num(data, "rh"), rhs)):
                if value is not None:
                    bucket.append(value)
        if stale:
            # §5.3: we can no longer vouch for the wind in that area either, so its direction
            # is unknown -> head-fire rate toward every point.
            winds.append(config.WORST_WIND_MS)
            temps.append(config.WORST_TEMP_C)
            rhs.append(config.WORST_RH)
            wind_from = None
            notes.append(f"stale {','.join(stale)}: worst-case wind speed/direction/temp/RH")
        if not winds:
            winds.append(config.WORST_WIND_MS)
            notes.append("no wind speed: worst case")
        if wind_from is None and not stale:
            notes.append("no wind direction: head-fire rate in every direction")
        wx = Weather(wind_ms=max(winds), wind_from=wind_from,
                     temp_c=max(temps) if temps else config.WORST_TEMP_C,
                     rh=min(rhs) if rhs else config.WORST_RH)
        return wx, notes

    # ---- the margin step (§5) -------------------------------------------------------
    def _route_status(self, rid: str, margin: float | None, now: float) -> str:
        status = status_for(margin)
        h = self.history[rid]
        while h and now - h[0][0] > config.DROP_WINDOW_S:
            h.popleft()
        if margin is not None:
            peak = max((m for _, m in h), default=margin)
            h.append((now, margin))
            if peak > 0 and (peak - margin) / peak > config.DROP_AMBER_FRACTION:
                self.drop_until[rid] = now + config.DROP_WINDOW_S   # hold, so it doesn't flicker
        if status == "green" and now < self.drop_until.get(rid, -math.inf):
            status = "amber"                                 # dropping fast (§5.1)
        return status

    def compute(self, now: float) -> dict:
        health = self.node_health(now)
        for nid, (status, _) in health.items():
            if status == "stale" and self.prev_node_status.get(nid) in ("fresh", "aging"):
                self.event(now, f"Node {nid} stale (no data for {config.STALE_S} s): using worst-case values")
            self.prev_node_status[nid] = status

        wx, wx_notes = self.weather(health)
        spreads = spreads_for(self.fires, wx, self.terrain)
        results = evaluate(self.routes, spreads, self.crew_xy, now)
        by_id = {r.route.id: r for r in results}
        status = {r.route.id: self._route_status(r.route.id, r.margin_min, now) for r in results}
        for rid, s in status.items():
            if s == "red" and self.prev_route_status.get(rid) != "red":
                self.event(now, f"Route {rid} RED: margin {by_id[rid].margin_min:.1f} min")
        self.prev_route_status = dict(status)

        def score(r):  # best margin first, then the faster route
            return (math.inf if r.margin_min is None else r.margin_min, -r.eta_min)

        # The crew's plan is the fastest route that isn't red. It only changes when the
        # current one turns red ("rerouted"), so the active route doesn't flicker.
        best = max(results, key=score)
        not_red = [r for r in results if status[r.route.id] != "red"]
        plan = min(not_red, key=lambda r: r.eta_min) if not_red else best
        if not self.fires or self.active is None:
            self.active = plan.route.id
        elif status[self.active] == "red" and not_red:      # all red -> no reroute, NO SAFE ROUTE below
            old, best = by_id[self.active], max(not_red, key=score)
            self.event(now, f"REROUTED: {self.active} -> {best.route.id} "
                            f"({self.active} margin {old.margin_min:.1f} min, "
                            f"{best.route.id} {'clear' if best.margin_min is None else f'{best.margin_min:.1f} min'})")
            self.active = best.route.id
        active = by_id[self.active]

        alerts = []
        if not not_red:
            alerts.append(f"NO SAFE ROUTE: every route is red (least bad: {best.route.id}, "
                          f"margin {best.margin_min:.1f} min)")
        leave_by = None
        if active.margin_min is not None:
            leave_by = now + (active.margin_min - config.LEAVE_BUFFER_MIN) * 60
            if leave_by <= now:
                alerts.append(f"LEAVE NOW on {self.active}")
        max_flame = max((sp.flame_m for _, sp in spreads if math.isfinite(sp.flame_m)), default=0.0)
        r_sz = active.route.sz_radius_m
        if r_sz is not None and spreads and r_sz < config.SEPARATION_FLAME_MULT * max_flame:
            alerts.append(f"Safety zone '{active.route.to}' too small: radius {r_sz:.0f} m < "
                          f"{config.SEPARATION_FLAME_MULT:g} x flame {max_flame:.1f} m")
        while self.events and now - self.events[0][0] > config.ALERT_HOLD_S:
            self.events.popleft()
        alerts += [text for _, text in self.events]

        return self.build_state(now, health, wx, wx_notes, spreads, results, status, alerts, leave_by)

    def build_state(self, now, health, wx, wx_notes, spreads, results, status, alerts, leave_by) -> dict:
        def r1(v):
            return None if v is None or not math.isfinite(v) else round(v, 1)

        routes = []
        for r in results:
            worst = None
            if r.worst is not None:
                lon, lat = r.route.lonlat(r.worst)
                worst = {"lon": round(lon, 6), "lat": round(lat, 6),
                         "t_fire_min": r1(r.t_fire_min), "t_crew_min": r1(r.t_crew_min)}
            routes.append({"id": r.route.id, "name": r.route.name, "to": r.route.to,
                           "margin_min": r1(r.margin_min), "status": status[r.route.id],
                           "active": r.route.id == self.active, "eta_min": r1(r.eta_min), "worst": worst})
        nodes = []
        for nid, (s, age) in health.items():
            n = self.nodes.get(nid, {})
            nodes.append({"id": nid, "label": n.get("label", nid), "lat": n.get("lat"), "lon": n.get("lon"),
                          "status": s, "age_s": None if age is None else round(age)})
        fires = []
        for f, sp in spreads:
            lon, lat = to_lonlat(f.x, f.y)
            fires.append({"id": f.id, "lat": round(lat, 6), "lon": round(lon, 6), "source": f.source,
                          "age_s": round(now - f.t0), "head_ros_m_min": r1(sp.head_m_min),
                          "head_dir": r1(sp.head_dir), "flame_m": r1(sp.flame_m),
                          "omni": sp.omni, "burnable": sp.burnable})
        clon, clat = to_lonlat(*self.crew_xy)
        return {
            "type": "state", "ts": round(now, 1), "scenario": config.SCENARIO, "model": MODEL_NAME,
            "placeholder_data": self.placeholder,
            "weather": {"wind_ms": r1(wx.wind_ms), "wind_from": r1(wx.wind_from), "temp_c": r1(wx.temp_c),
                        "rh": r1(wx.rh), "m1h_pct": round(wx.m1h * 100), "notes": wx_notes},
            "crew": {"lat": round(clat, 6), "lon": round(clon, 6)},
            "active_route": self.active,
            "leave_by_ts": None if leave_by is None else round(leave_by),
            "routes": routes, "nodes": nodes, "fires": fires, "alerts": alerts,
        }


def summary(state: dict) -> str:
    rs = "  ".join(f"{r['id']}{'*' if r['active'] else ''}={r['status']}"
                   f"({'-' if r['margin_min'] is None else r['margin_min']})" for r in state["routes"])
    ns = " ".join(f"{n['id']}:{n['status'][0]}" for n in state["nodes"])
    w = state["weather"]
    return (f"routes {rs} | wind {w['wind_ms']} m/s from {w['wind_from']} | "
            f"T {w['temp_c']} RH {w['rh']} | fires {len(state['fires'])} | nodes {ns}")


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="LCES margin engine")
    ap.add_argument("--host", default=os.getenv("MQTT_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    ap.add_argument("--scenario", choices=["2013", "today"], default=config.SCENARIO)
    ap.add_argument("--allow-sim", action="store_true",
                    default=os.getenv("ALLOW_SIMULATOR_DATA", "false").lower() in {"1", "true", "yes"},
                    help="accept simulator messages (\"_simulated\": true)")
    args = ap.parse_args()
    config.SCENARIO = args.scenario

    nodes_file, routes_file, placeholder = input_files()
    terrain = Terrain()
    engine = MarginEngine(terrain, load_routes(routes_file, terrain), load_nodes(nodes_file), placeholder)
    print(f"[engine] model={MODEL_NAME} scenario={config.SCENARIO} nodes={nodes_file} routes={routes_file}"
          f"{'  ** PLACEHOLDER DATA **' if placeholder else ''}", flush=True)
    for r in engine.routes:
        print(f"[engine] route {r.id} '{r.name}' -> {r.to}: {len(r.xy)} pts, "
              f"crew ETA {r.seg_s.sum() / 60:.1f} min", flush=True)

    lock = threading.Lock()

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"[engine] MQTT connection failed: {reason_code}", flush=True)
            return
        for topic in ("sensors/+/wx", "sensors/+/fire", "crew/+/pos", config.CMD_TOPIC):
            client.subscribe(topic)
        print(f"[engine] connected to {args.host}:{args.port}", flush=True)

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload)
            if not isinstance(data, dict):
                raise ValueError("payload must be a JSON object")
            if data.get("_simulated") and not args.allow_sim:
                return
            now = time.time()
            with lock:
                if msg.topic == config.CMD_TOPIC:
                    engine.on_cmd(data, now)
                elif msg.topic.endswith("/wx"):
                    engine.on_wx(data, now)
                elif msg.topic.endswith("/fire"):
                    engine.on_fire(data, now)
                elif msg.topic.endswith("/pos"):
                    engine.on_crew(data, now)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            print(f"[engine] ignoring {msg.topic}: {error}", flush=True)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="margin-engine")
    client.on_connect = on_connect
    client.on_message = on_message
    if os.getenv("MQTT_USERNAME"):
        client.username_pw_set(os.getenv("MQTT_USERNAME"), os.getenv("MQTT_PASSWORD") or None)
    client.connect(args.host, args.port, keepalive=60)
    client.loop_start()

    last_key, last_print = None, 0.0
    try:
        while True:
            t0 = time.time()
            with lock:
                state = engine.compute(t0)
            client.publish(config.STATE_TOPIC, json.dumps(state))
            # Print when a status / the active route / node health changes, else every 10 s.
            key = (state["active_route"], tuple(r["status"] for r in state["routes"]),
                   tuple(n["status"] for n in state["nodes"]), len(state["fires"]))
            if key != last_key or t0 - last_print > 10:
                print(f"[engine] {time.strftime('%H:%M:%S')} {summary(state)}  "
                      f"({(time.time() - t0) * 1000:.0f} ms)", flush=True)
                last_key, last_print = key, t0
            time.sleep(max(config.RECOMPUTE_S - (time.time() - t0), 0.05))
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
