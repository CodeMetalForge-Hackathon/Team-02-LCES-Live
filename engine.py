"""Margin engine service (docs/LCES_Design.md §5).

MQTT in:  sensors/+/wx (weather; flame == 1 = spot fire at that node), sensors/+/fire,
          crew/+/pos, engine/cmd ({"cmd": "start"} | {"cmd": "reset"} | {"cmd": "fire", "node"|"lat"+"lon"} |
          {"cmd": "crew", "lat", "lon"} | {"cmd": "plan", "route": "black"|"ranch"} (start route, before Start)).
Phases: the engine starts in "ready" (no fire, margins null, nothing moves). {"cmd": "start"} -> "running":
the scenario clock (config.SCENARIO_START, 16:20) and the fire grid start then. {"cmd": "reset"} -> "ready".
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

import numpy as np
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

import config
from fire_model import MODEL_NAME, Weather
from margin import (FireGrid, FirePoint, GraphTerrain, Terrain, crew_start_xy, evaluate, graph_routes, load_graph,
                    fire_grid_msg, load_routes, spreads_for, status_for, to_lonlat, to_utm, v1_fire_min)

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
    def __init__(self, terrain: Terrain, routes, nodes: dict[str, dict], placeholder: bool = False,
                 graph: dict | None = None):
        """graph: routing graph (margin.load_graph); routes are then re-planned from the crew's cell."""
        self.terrain, self.routes, self.nodes, self.placeholder = terrain, routes, nodes, placeholder
        self.graph = graph
        self.fire: FireGrid | None = None                    # graph mode: created at Start
        self.phase = "ready"                                 # "ready" until {"cmd": "start"}, then "running"
        self.start_route: str | None = config.START_ROUTE    # route the crew is on at Start; {"cmd": "plan"} sets it
        self.started_ts: float | None = None                 # unix s of Start (= SCENARIO_START)
        self.replan_ts: float | None = None                  # unix s of the last reroute, or of Start
        self._ready_xy = None                                # crew cell the ready-phase routes were planned from
        self.wind_avg: dict[str, tuple[float, float, float, float]] = {}   # node -> (t, ux, uy, speed) moving average
        self.crew_external = False                           # a real crew/<id>/pos arrived: stop the simulated walk
        self.crew_arrived = False
        self._last_step: float | None = None
        self.temps: dict[str, deque] = {}                    # node -> (t, temp_c) of the last HOT_RISE_S
        self.hot: set[str] = set()                           # nodes that lit a fire point (hot sensor rule)
        self.replanned: dict[str, tuple[float, object]] = {}   # route -> (ts, xy) of the last announced detour
        self.latest: dict[str, tuple[dict, float]] = {}      # node -> (last wx message, rx time)
        self.fires: list[FirePoint] = []
        self.start_xy = self.crew_xy = tuple(routes[0].xy[0])   # default: start of the first route
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
        w, d = _num(data, "wind_ms"), _num(data, "wind_dir")
        if w is not None and d is not None:
            self._smooth_wind(node, w, d, now)
        if data.get("flame") == 1 and node in self.nodes:
            self.add_fire(self.nodes[node]["x"], self.nodes[node]["y"], now, f"{node} flame sensor")
        temp = _num(data, "temp_c")
        if temp is not None and node in self.nodes:          # hot sensor node rule (§1.4): the hair dryer
            h = self.temps.setdefault(node, deque())
            h.append((now, temp))
            while now - h[0][0] > config.HOT_RISE_S:
                h.popleft()
            if temp >= config.HOT_TEMP_C or temp - min(c for _, c in h) > config.HOT_RISE_C:
                n = self.nodes[node]
                if self.add_fire(n["x"], n["y"], now, f"{node} hot sensor ({temp:.0f} C)") is not None:
                    self.hot.add(node)                       # same spot again merges: first time kept

    def _smooth_wind(self, node: str, w: float, d: float, now: float) -> None:
        """Exponential moving average (time constant WIND_SMOOTH_S) of the wind, the direction as a unit vector
        so 350 deg and 10 deg average to 0 deg, not 180."""
        ux, uy = math.sin(math.radians(d)), math.cos(math.radians(d))
        prev = self.wind_avg.get(node)
        if prev is None or now - prev[0] > config.health_s(node)[1]:
            self.wind_avg[node] = (now, ux, uy, w)
            return
        t0, px, py, pw = prev
        a = 1 - math.exp(-max(now - t0, 0) / config.WIND_SMOOTH_S)
        self.wind_avg[node] = (now, px + a * (ux - px), py + a * (uy - py), pw + a * (w - pw))

    def smoothed_wind(self, node: str) -> tuple[float | None, float | None]:
        """(speed m/s, direction from, deg) of the moving average, rounded; (None, None) if no wind yet."""
        if node not in self.wind_avg:
            return None, None
        _, ux, uy, w = self.wind_avg[node]
        step, sstep = config.WIND_DIR_STEP_DEG, config.WIND_SPEED_STEP_MS
        d = None if math.hypot(ux, uy) < 1e-6 else round(math.degrees(math.atan2(ux, uy)) % 360 / step) * step % 360
        return math.ceil(w / sstep - 1e-9) * sstep, d

    def add_fire(self, x: float, y: float, now: float, source: str) -> FirePoint | None:
        if self.phase != "running":
            print(f"[engine] ignoring fire ({source}): scenario not started", flush=True)
            return None
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
        xy = to_utm(float(data["lon"]), float(data["lat"]))
        self.terrain.value("dem", *xy)                       # raises if outside the terrain patch
        self.crew_xy = xy
        self.crew_external = True                            # real positions win over the simulated walk

    def on_cmd(self, data: dict, now: float) -> None:
        cmd = data.get("cmd")
        if cmd == "start":
            if self.phase == "running":
                print("[engine] start ignored: already running", flush=True)
                return
            self.phase, self.started_ts, self.replan_ts = "running", now, now
            self.active = None                               # picked again at the first running step
            self._last_step, self.crew_arrived = None, False
            if self.graph is not None:
                self.fire = FireGrid(self.graph, now)        # the black at its start state = SCENARIO_START
            self.event(now, f"Scenario started ({config.SCENARIO_START})")
        elif cmd == "plan":                                  # dashboard "Start on": black / ranch, before Start only
            if self.phase == "ready" and data.get("route") in {r.id for r in self.routes}:
                self.start_route, self.active = data["route"], None
        elif cmd == "reset":
            self.phase, self.started_ts, self.replan_ts = "ready", None, None
            self.fires.clear()
            self.fire = None
            self.active = None
            self.crew_xy = self.start_xy
            self._ready_xy = None
            self.crew_external, self.crew_arrived, self._last_step = False, False, None
            self.temps.clear()
            self.hot.clear()
            self.replanned.clear()
            for h in self.history.values():
                h.clear()
            self.drop_until.clear()
            self.prev_route_status.clear()
            self.events.clear()
            self.event(now, "Reset: ready, press Start")
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
            aging_s, stale_s = config.health_s(nid)
            status = "stale" if age >= stale_s else "aging" if age >= aging_s else "fresh"
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
            if nid in self.wind_avg:                          # moving average, not the last jumpy reading
                w, d = self.smoothed_wind(nid)
            else:
                d = _num(data, "wind_dir")
            if w is not None:
                winds.append(w)
            if d is not None and age < wind_from_age:
                wind_from, wind_from_age = d % 360, age
            for value, bucket in ((_num(data, "temp_c"), temps), (_num(data, "rh"), rhs)):
                if value is not None and nid not in self.hot:   # a node in the fire says nothing of the air
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
            if 0 < margin < config.DROP_AMBER_BELOW_MIN and (peak - margin) / peak > config.DROP_AMBER_FRACTION:
                self.drop_until[rid] = now + config.DROP_WINDOW_S   # hold, so it doesn't flicker
        if status == "green" and now < self.drop_until.get(rid, -math.inf):
            status = "amber"                                 # dropping fast (§5.1)
        return status

    def walk_crew(self, dt: float, now: float) -> None:
        """Move the crew dt seconds along the active route at the modelled walking times (seg_s)."""
        r = next((x for x in self.routes if x.id == self.active), None)
        if r is None or dt <= 0 or len(r.xy) < 2:
            return
        xy, seg, c = r.xy, r.seg_s, np.asarray(self.crew_xy, float)
        a, ab = xy[:-1], xy[1:] - xy[:-1]
        l2 = (ab ** 2).sum(1)
        f = np.clip(((c - a) * ab).sum(1) / np.where(l2 > 0, l2, 1), 0, 1)
        i = int(np.argmin(np.hypot(*(a + f[:, None] * ab - c).T)))
        f, left = float(f[i]), dt
        while i < len(seg):
            s_i = float(seg[i])
            if not math.isfinite(s_i):
                break                                        # impassable: stay put
            rest = (1 - f) * s_i
            if left < rest:
                f += left / s_i
                break
            left -= rest
            i, f = i + 1, 0.0
        if i >= len(seg):
            self.crew_xy = tuple(float(v) for v in xy[-1])
            if not self.crew_arrived:
                self.crew_arrived = True
                self.event(now, f"Crew reached {r.to}")
        else:
            self.crew_xy = tuple(float(v) for v in xy[i] + f * (xy[i + 1] - xy[i]))

    def announce_detours(self, now: float) -> None:
        """Event + replanned_ts when the fire bends a route somewhere new; held, as routes re-plan every step."""
        for r in self.routes:
            ts, xy = self.replanned.get(r.id, (-math.inf, None))
            if not r.detour:
                if xy is not None:
                    self.replanned[r.id] = (ts, None)        # back on its fire-blind path
                continue
            moved = xy is None or np.hypot(*(r.xy[:, None] - xy[None]).T).min(0).max() > config.ROUTE_CHANGE_M
            if moved and now - ts >= config.ROUTE_CHANGE_HOLD_S:
                self.replanned[r.id] = (now, r.xy)
                self.event(now, f"Route {r.id} re-planned around the fire ({r.seg_s.sum() / 60:.1f} min walk)")

    def compute(self, now: float) -> dict:
        health = self.node_health(now)
        for nid, (status, _) in health.items():
            if status == "stale" and self.prev_node_status.get(nid) in ("fresh", "aging"):
                self.event(now, f"Node {nid} stale (no data for {config.health_s(nid)[1]:.0f} s): using worst-case values")
            self.prev_node_status[nid] = status

        wx, wx_notes = self.weather(health)
        spreads = spreads_for(self.fires, wx, self.terrain)
        if self.phase != "running":                         # ready: routes without fire, nothing moves
            if self.graph is not None and self._ready_xy != tuple(self.crew_xy):
                self.routes = graph_routes(self.graph, self.crew_xy)
                self._ready_xy = tuple(self.crew_xy)
            fire_min, max_flame = None, 0.0
        elif self.graph is not None:
            if config.CREW_WALK and not self.crew_external and self._last_step is not None:
                self.walk_crew(now - self._last_step, now)
            self._last_step = now
            self.fire.advance(now, wx, [(f.x, f.y, f.t0) for f in self.fires])
            self.routes = graph_routes(self.graph, self.crew_xy, self.fire, now)   # closures change every step
            self.announce_detours(now)
            fire_min = self.fire.fire_min(now)
            max_flame = float(self.fire.flame[self.fire.burning(now)].max(initial=0.0))
        else:
            fire_min = v1_fire_min(spreads, now)
            max_flame = max((sp.flame_m for _, sp in spreads if math.isfinite(sp.flame_m)), default=0.0)
        results = evaluate(self.routes, fire_min, self.crew_xy)
        by_id = {r.route.id: r for r in results}
        status = {r.route.id: self._route_status(r.route.id, r.margin_min, now) for r in results}
        for rid, s in status.items():
            if s == "red" and self.prev_route_status.get(rid) != "red":
                self.event(now, f"Route {rid} RED: margin {by_id[rid].margin_min:.1f} min")
        self.prev_route_status = dict(status)

        def score(r):  # best margin first, then the faster route
            return (math.inf if r.margin_min is None else r.margin_min, -r.eta_min)

        # At Start the crew takes the fastest route that isn't red (on the 2013 data: the black). After that the
        # active route only changes when it turns red and another route isn't ("rerouted"), so it doesn't flicker.
        best = max(results, key=score)
        not_red = [r for r in results if status[r.route.id] != "red"]
        if self.active not in by_id:
            start = by_id.get(self.start_route)
            self.active = (start if start and status[start.route.id] != "red" else
                           min(not_red, key=lambda r: r.eta_min) if not_red else best).route.id
        if status[self.active] == "red" and not_red:        # all red -> no reroute, NO SAFE ROUTE below
            old, best = by_id[self.active], max(not_red, key=score)
            self.event(now, f"REROUTED: {self.active} -> {best.route.id} "
                            f"({self.active} margin {old.margin_min:.1f} min, "
                            f"{best.route.id} {'clear' if best.margin_min is None else f'{best.margin_min:.1f} min'})")
            self.active = best.route.id
            self.replan_ts = now
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
        r_sz = active.route.sz_radius_m
        if r_sz is not None and max_flame > 0 and r_sz < config.SEPARATION_FLAME_MULT * max_flame:
            alerts.append(f"Safety zone '{active.route.to}' too small: radius {r_sz:.0f} m < "
                          f"{config.SEPARATION_FLAME_MULT:g} x flame {max_flame:.1f} m")
        while self.events and now - self.events[0][0] > config.ALERT_HOLD_S:
            self.events.popleft()
        alerts += [text for _, text in self.events]

        state = self.build_state(now, health, wx, wx_notes, spreads, results, status, alerts, leave_by)
        state["fire_grid"] = fire_grid_msg(self.fire, now, self.started_ts) if self.fire is not None else None
        return state

    def build_state(self, now, health, wx, wx_notes, spreads, results, status, alerts, leave_by) -> dict:
        def r1(v):
            return None if v is None or not math.isfinite(v) else round(v, 1)

        def pts(a):     # per-point minutes aligned with path; null = before the crew joins / fire never
            return [None] * len(r.route.xy) if a is None else [r1(float(v)) for v in a]

        routes = []
        for r in results:
            worst = None
            if r.worst is not None:
                lon, lat = r.route.lonlat(r.worst)
                worst = {"lon": round(lon, 6), "lat": round(lat, 6),
                         "t_fire_min": r1(r.t_fire_min), "t_crew_min": r1(r.t_crew_min)}
            routes.append({"id": r.route.id, "name": r.route.name, "to": r.route.to,
                           "margin_min": r1(r.margin_min), "status": status[r.route.id],
                           "active": r.route.id == self.active, "eta_min": r1(r.eta_min), "worst": worst,
                           "replanned_ts": r1(self.replanned[r.route.id][0]) if r.route.id in self.replanned else None,
                           "path": r.route.path_lonlat(),
                           "t_crew_min": pts(r.crew_pts), "t_fire_min": pts(r.fire_pts)})
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
            "phase": self.phase, "started_ts": None if self.started_ts is None else round(self.started_ts, 1),
            "scenario_start": config.SCENARIO_START,
            "replan_ts": None if self.replan_ts is None else round(self.replan_ts, 1),
            "placeholder_data": self.placeholder,
            "weather": {"wind_ms": r1(wx.wind_ms), "wind_from": r1(wx.wind_from), "temp_c": r1(wx.temp_c),
                        "rh": r1(wx.rh), "m1h_pct": round(wx.m1h * 100), "notes": wx_notes},
            "crew": {"lat": round(clat, 6), "lon": round(clon, 6),
                     "walking": self.phase == "running" and self.graph is not None and config.CREW_WALK
                                and not self.crew_external and not self.crew_arrived,
                     "arrived": self.crew_arrived},
            "active_route": self.active, "start_route": self.start_route,
            "leave_by_ts": None if leave_by is None else round(leave_by),
            "routes": routes, "nodes": nodes, "fires": fires, "alerts": alerts,
        }


def summary(state: dict) -> str:
    rs = "  ".join(f"{r['id']}{'*' if r['active'] else ''}={r['status']}"
                   f"({'-' if r['margin_min'] is None else r['margin_min']})" for r in state["routes"])
    ns = " ".join(f"{n['id']}:{n['status'][0]}" for n in state["nodes"])
    w = state["weather"]
    return (f"{state['phase']} | routes {rs} | wind {w['wind_ms']} m/s from {w['wind_from']} | "
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
    if config.GRAPH_FILE.exists():
        graph = load_graph()
        terrain, routes_file = GraphTerrain(graph), config.GRAPH_FILE
        placeholder = not config.NODES_FILE.exists()
        engine = MarginEngine(terrain, graph_routes(graph, crew_start_xy()), load_nodes(nodes_file),
                              placeholder, graph)
    else:  # v1, testing only
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
            key = (state["phase"], state["active_route"], tuple(r["status"] for r in state["routes"]),
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
