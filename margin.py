"""Margin computation (docs/LCES_Design.md §4, §5): terrain, routes, crew and fire times.

Pure computation, no MQTT. engine.py feeds it live data.

Routes: with data/yarnell/graph.npz (build_graph.py), the fastest route from the crew's cell to
each safety zone over the routing graph (graph_routes, ADR 0001). Without it (v1, testing only),
the LineStrings in routes.geojson, each evaluated on its own along its densified points.
"Rerouting" = switching the active route to the one with the best margin.
"""
import base64
import json
import math
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import shapes
from rasterio.transform import from_origin
from scipy.ndimage import minimum_filter
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

import build_graph
import config
from fire_model import Spread, Weather, arrival_min

_TO_UTM = Transformer.from_crs(4326, 32612, always_xy=True)
_TO_LONLAT = Transformer.from_crs(32612, 4326, always_xy=True)


def to_utm(lon: float, lat: float) -> tuple[float, float]:
    return _TO_UTM.transform(lon, lat)


def to_lonlat(x: float, y: float) -> tuple[float, float]:
    return _TO_LONLAT.transform(x, y)


def tobler_kmh(rise_over_run: float) -> float:
    """Tobler's hiking function: fastest (6 km/h) at 5 % downhill."""
    return 6 * math.exp(-3.5 * abs(rise_over_run + 0.05))


def offtrail_factor(fuel_code: int) -> float:
    for first, last, factor in config.OFFTRAIL_FACTOR_BY_FUEL:
        if first <= fuel_code <= last:
            return factor
    return config.OFFTRAIL_FACTOR_DEFAULT


class Terrain:
    """DEM, slope, aspect and fuel on the shared 10 m EPSG:32612 grid (§3.1)."""

    def __init__(self, rasters: dict[str, Path] = config.RASTERS):
        self.layers = {}
        for name, path in rasters.items():
            with rasterio.open(path) as r:
                self.layers[name] = r.read(1)
                self._inverse = ~r.transform
        self.rows, self.cols = self.layers["dem"].shape

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        col, row = self._inverse * (x, y)
        row, col = int(math.floor(row)), int(math.floor(col))
        if not (0 <= row < self.rows and 0 <= col < self.cols):
            raise ValueError(f"point ({x:.0f}, {y:.0f}) is outside the terrain patch (§3.1)")
        return row, col

    def value(self, layer: str, x: float, y: float) -> float:
        row, col = self._cell(x, y)
        return float(self.layers[layer][row, col])

    def fuel(self, x: float, y: float) -> int:
        """FBFM40 code used by the fire model, after the scenario override."""
        return self._scenario_fuel(int(self.value("fuel", x, y)))

    def fire_fuel(self, x: float, y: float) -> int:
        """Fuel for a fire point: the most common burnable code in the 3 x 3 cells (30 m) around it.

        A spot fire on one bare-rock / road cell still spreads from its neighbours; it only
        counts as non-burnable if the whole neighbourhood is.
        """
        row, col = self._cell(x, y)
        block = self.layers["fuel"][max(row - 1, 0):row + 2, max(col - 1, 0):col + 2].ravel()
        burnable = [int(c) for c in block if int(c) not in config.NON_BURNABLE]
        code = max(set(burnable), key=burnable.count) if burnable else int(block[0])
        return self._scenario_fuel(code)

    @staticmethod
    def _scenario_fuel(code: int) -> int:
        if config.SCENARIO == "2013" and code not in config.NON_BURNABLE:
            return config.REPLAY_FUEL
        return code


class GraphTerrain(Terrain):
    """Terrain from graph.npz. Its fuel already has SH5 inside the 2013 perimeter, so no scenario override."""

    def __init__(self, g: dict):
        self.layers = {"dem": g["elev"], "slope": g["slope"], "aspect": g["aspect"], "fuel": g["fuel"]}
        self._inverse = ~from_origin(float(g["x0"]), float(g["ytop"]), float(g["res"]), float(g["res"]))
        self.rows, self.cols = g["elev"].shape

    @staticmethod
    def _scenario_fuel(code: int) -> int:
        return code


@dataclass
class Route:
    id: str
    name: str
    to: str                      # safety zone name
    trail: bool
    sz_radius_m: float | None
    xy: np.ndarray               # (n, 2) densified points, metres
    seg_s: np.ndarray            # (n-1,) walking seconds from point i to i+1
    factor: np.ndarray           # (n,) travel factor at each point

    def lonlat(self, i: int) -> tuple[float, float]:
        return to_lonlat(*self.xy[i])

    def path_lonlat(self) -> list[list[float]]:
        lon, lat = _TO_LONLAT.transform(self.xy[:, 0], self.xy[:, 1])
        return [[round(a, 6), round(b, 6)] for a, b in zip(lon, lat)]

    def crew_seconds(self, crew_xy) -> tuple[int, np.ndarray]:
        """(start index, seconds from the crew to every point from there on).

        The crew joins the route at its nearest point, walking straight there at the
        flat off-trail speed of that point.
        """
        d = np.hypot(self.xy[:, 0] - crew_xy[0], self.xy[:, 1] - crew_xy[1])
        k = int(np.argmin(d))
        approach = d[k] / (tobler_kmh(0.0) * self.factor[k] / 3.6) if d[k] > 5 else 0.0
        t = np.concatenate([[0.0], np.cumsum(self.seg_s[k:])]) + approach
        return k, t


def _densify(xy: np.ndarray, step: float) -> np.ndarray:
    out = [xy[0]]
    for a, b in zip(xy[:-1], xy[1:]):
        n = max(int(math.ceil(math.hypot(*(b - a)) / step)), 1)
        for j in range(1, n + 1):
            out.append(a + (b - a) * j / n)
    return np.array(out)


def load_routes(path: Path, terrain: Terrain) -> list[Route]:
    """routes.geojson: one LineString per route (lon/lat), drawn FROM the crew start TO a safety zone.

    Properties: id (required), name, to (safety zone name), trail (bool: on a trail/road),
    travel_factor (optional: overrides the trail / fuel-based factor for the whole route),
    sz_radius_m (safety zone radius, for the 4 x flame length check).
    """
    routes = []
    for feature in json.loads(Path(path).read_text())["features"]:
        geom, props = feature["geometry"], feature.get("properties") or {}
        if geom["type"] != "LineString":
            raise ValueError(f"route {props.get('id')}: expected LineString, got {geom['type']}")
        xy = _densify(np.array([to_utm(lon, lat) for lon, lat, *_ in geom["coordinates"]]),
                      config.ROUTE_STEP_M)
        trail = bool(props.get("trail", False))
        elev = np.array([terrain.value("dem", x, y) for x, y in xy])
        if props.get("travel_factor") is not None:
            factor = np.full(len(xy), float(props["travel_factor"]))
        else:
            factor = np.array([config.TRAIL_FACTOR if trail else offtrail_factor(terrain.fuel(x, y))
                               for x, y in xy])
        seg_s = []
        for i in range(len(xy) - 1):
            length = math.hypot(*(xy[i + 1] - xy[i]))
            speed_kmh = tobler_kmh((elev[i + 1] - elev[i]) / length) * min(factor[i], factor[i + 1])
            seg_s.append(length / (speed_kmh / 3.6))
        routes.append(Route(id=str(props["id"]), name=props.get("name", str(props["id"])),
                            to=props.get("to", ""), trail=trail,
                            sz_radius_m=props.get("sz_radius_m"),
                            xy=xy, seg_s=np.array(seg_s), factor=factor))
    if not routes:
        raise ValueError(f"{path}: no routes")
    return routes


def load_graph(path: Path = config.GRAPH_FILE) -> dict:
    """graph.npz + the sparse matrix (edge costs) and which vertices have any edge."""
    g = dict(np.load(path))
    g["_m"] = build_graph.matrix(g)
    g["_ok"] = np.bincount(g["src"], minlength=len(g["x"])) > 0
    nr, nc = (int(v) for v in g["shape"])                # grid cell of every vertex (trail vertices too)
    g["_cell"] = (np.clip(((g["ytop"] - g["y"]) // g["res"]).astype(int), 0, nr - 1) * nc
                  + np.clip(((g["x"] - g["x0"]) // g["res"]).astype(int), 0, nc - 1))
    return g


def crew_start_xy(path: Path = config.SCENARIO_FILE) -> tuple[float, float]:
    """The descent point (the spec's "saddle"): crew start at 16:20."""
    f = next(f for f in json.loads(Path(path).read_text())["features"]
             if f["properties"]["name"] == "Descent point (crew start)")
    return to_utm(*f["geometry"]["coordinates"][:2])


# (route id, safety zone name, graph key of its cells, radius for the 4 x flame check)
SAFETY_ZONES = (("ranch", "Boulder Springs Ranch", "ranch", build_graph.RANCH_RADIUS_M),
                ("black", "the black", "black0", 500))


def graph_routes(g: dict, crew_xy, fire: "FireGrid | None" = None, now: float = 0.0) -> list[Route]:
    """Fastest route from the crew's cell to each safety zone. Times are the graph's edge costs.

    With fire (§5 steps 3-7): crew times with no closures; close every vertex the fire makes unsafe before
    the crew gets there (burning and within 4 x flame of burning included); route around; repeat until no new
    closures. The black target = every black cell by now. A zone with no path left keeps its fire-blind
    path, so it shows red and the engine raises "no safe route".
    """
    n = int(g["elev"].size)
    start = build_graph.cell(g, *crew_xy)
    if not g["_ok"][start]:              # impassable cell (steep, building): start from the nearest open one
        open_ = np.flatnonzero(g["_ok"][:n])
        start = int(open_[np.argmin(np.hypot(g["x"][open_] - crew_xy[0], g["y"][open_] - crew_xy[1]))])
    targets = {"ranch": g["ranch"], "black0": g["black0"] if fire is None else np.flatnonzero(fire.black(now))}
    t, pred = dijkstra(g["_m"], indices=start, return_predecessors=True)
    blind = {k: build_graph.path_to(t, pred, start, v)[1] for k, v in targets.items()}
    paths = blind
    if fire is not None:
        unsafe = fire.unsafe_at(now)[g["_cell"]]
        t_fire = np.maximum(unsafe - now, 0) / config.SAFETY_FACTOR      # seconds, as in fire_min
        closed = (unsafe <= now) | (t_fire < t)
        for _ in range(10):              # closures only grow, so this settles; 10 is a guard
            t, pred = dijkstra(build_graph.matrix(g, closed), indices=start, return_predecessors=True)
            new = closed | (t_fire < t)
            if (new == closed).all():
                break
            closed = new
        paths = {k: build_graph.path_to(t, pred, start, v)[1] or blind[k] for k, v in targets.items()}
    veg = g["veg"].ravel()
    routes = []
    for rid, to, key, radius in SAFETY_ZONES:
        path = paths[key]
        if not path:
            continue
        path = np.array(path)
        seg_s = np.asarray(g["_m"][path[:-1], path[1:]]).ravel() if len(path) > 1 else np.zeros(0)   # crew in the zone
        factor = np.where(path < n, veg[np.minimum(path, n - 1)], config.TRAIL_FACTOR)
        routes.append(Route(rid, f"To {to}", to, False, radius,
                            np.column_stack([g["x"][path], g["y"][path]]), seg_s, factor))
    return routes


@dataclass
class FirePoint:
    id: str
    x: float
    y: float
    t0: float        # unix time first detected
    source: str


@dataclass
class RouteResult:
    route: Route
    margin_min: float | None     # None = no fire
    worst: int | None            # index of the worst point
    t_fire_min: float | None     # at the worst point
    t_crew_min: float | None     # at the worst point
    eta_min: float               # crew to safety zone
    crew_pts: np.ndarray | None = None   # (n,) minutes until the crew reaches each route point (nan before join)
    fire_pts: np.ndarray | None = None   # (n,) safety-factored minutes until fire there (inf = never, nan before join)


def spreads_for(fires: list[FirePoint], wx: Weather, terrain: Terrain) -> list[tuple[FirePoint, Spread]]:
    return [(f, Spread(terrain.fire_fuel(f.x, f.y), terrain.value("slope", f.x, f.y),
                       terrain.value("aspect", f.x, f.y), wx)) for f in fires]


def v1_fire_min(spreads, now: float):
    """v1 (routes.geojson): straight-line spread from each fire point -> fire_min for evaluate()."""
    if not spreads:
        return None
    return lambda pts: np.array([min(arrival_min(sp, (f.x, f.y), p, (now - f.t0) / 60) for f, sp in spreads)
                                 for p in pts])


def evaluate(routes: list[Route], fire_min, crew_xy) -> list[RouteResult]:
    """margin(route) = min over its points of (fire arrival - crew arrival), in minutes (§5).

    fire_min(points) -> minutes until the fire makes each point unsafe (inf = never); None = no fire.
    """
    results = []
    for route in routes:
        k, t_crew_s = route.crew_seconds(crew_xy)
        t_crew = t_crew_s / 60
        eta = float(t_crew[-1])
        n = len(route.xy)
        crew_pts = np.full(n, np.nan)
        crew_pts[k:] = t_crew
        fire_pts = np.full(n, np.nan)
        if fire_min is None:
            fire_pts[k:] = np.inf
            results.append(RouteResult(route, None, None, None, None, eta, crew_pts, fire_pts))
            continue
        t_fire = fire_min(route.xy[k:])
        fire_pts[k:] = t_fire
        m = t_fire - t_crew
        i = int(np.argmin(m))
        if not math.isfinite(m[i]):          # fire never reaches this route (e.g. non-burnable)
            results.append(RouteResult(route, None, None, None, None, eta, crew_pts, fire_pts))
            continue
        results.append(RouteResult(route, float(m[i]), k + i, float(t_fire[i]), float(t_crew[i]), eta,
                                   crew_pts, fire_pts))
    return results


class FireGrid:
    """Fire arrival clock time per grid cell, spread by minimum travel time (ADR 0002, §2.4).

    Start: the black (`black0`, fire area at 16:15) reached at scenario start, already black. Only its
    `front0` cells (burned 16:00-16:15) spread; the rest of its edge held since 16:00 and stays cold.
    advance(): cells reached by now keep their times; Dijkstra spreads from them into the rest
    under the current weather. Grid cells only (no trail vertices); non-burnable cells never burn.
    """
    STEPS = [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc]
    BEARINGS = [math.degrees(math.atan2(dc, -dr)) % 360 for dr, dc in STEPS]   # row -1 = north

    def __init__(self, g: dict, start: float):
        self.g = g
        nr, nc = (int(v) for v in g["shape"])
        n = nr * nc
        fuel = g["fuel"].ravel().astype(int)
        self.burnable = ~np.isin(fuel, list(config.NON_BURNABLE))
        key = np.column_stack([fuel, np.round(g["slope"].ravel() / config.SLOPE_BAND_DEG) * config.SLOPE_BAND_DEG,
                               np.round(g["aspect"].ravel() / config.ASPECT_BAND_DEG) * config.ASPECT_BAND_DEG % 360])
        self._combos, self._combo = np.unique(key, axis=0, return_inverse=True)
        self._combo = self._combo.ravel()
        idx = np.arange(n).reshape(nr, nc)
        src, step = [], []
        for k, (dr, dc) in enumerate(self.STEPS):
            a = idx[max(-dr, 0):nr - max(dr, 0), max(-dc, 0):nc - max(dc, 0)].ravel()
            src.append(a)
            step.append(np.full(len(a), k))
        src, step = np.concatenate(src), np.concatenate(step)
        offset = np.array([dr * nc + dc for dr, dc in self.STEPS])
        dst = src + offset[step]
        self.black0 = np.zeros(n, bool)
        self.black0[g["black0"]] = True
        cold = self.black0.copy()
        cold[g.get("front0", g["black0"])] = False
        ok = self.burnable[src] & self.burnable[dst] & ~cold[src]
        self.src, self.dst, self.step = src[ok], dst[ok], step[ok]
        self.length = float(g["res"]) * np.hypot(*np.array(self.STEPS).T)[self.step]
        self._step_of = {int(o): k for k, o in enumerate(offset)}
        self.arrival = np.where(self.black0 & self.burnable, start, np.inf)   # black0 still counts as black (black())
        self.flame = np.zeros(n)          # flame length (m) of the fire as it reached each cell
        self._wx = None

    def _rates(self, wx: Weather) -> None:
        """Spread rate (m/min) and flame length (m) per (cell type, step direction), cached per weather."""
        if wx == self._wx:
            return
        spreads = [Spread(int(f), s, a, wx) for f, s, a in self._combos]
        self._ros = np.array([[sp.ros_m_min(b) for b in self.BEARINGS] for sp in spreads])
        self._flame = np.nan_to_num(np.array([[sp.flame_toward_m(b) for b in self.BEARINGS] for sp in spreads]))
        self._wx = wx

    def ignite(self, x: float, y: float, t: float) -> None:
        """Spot fire at clock time t: its cell and the burnable cells around it (a node on a road still spreads)."""
        nr, nc = (int(v) for v in self.g["shape"])
        r, c = divmod(build_graph.cell(self.g, x, y), nc)
        rows, cols = np.mgrid[max(r - 1, 0):min(r + 2, nr), max(c - 1, 0):min(c + 2, nc)]
        cells = (rows * nc + cols).ravel()
        cells = cells[self.burnable[cells] & (self.arrival[cells] > t)]
        self.arrival[cells] = t
        self.flame[cells] = self._flame[self._combo[cells]].max(axis=1)

    def advance(self, now: float, wx: Weather, fires=()) -> None:
        """fires: (x, y, ignition time) of spot fires; ignite() is idempotent, so pass all of them each time."""
        self._rates(wx)
        for x, y, t in fires:
            self.ignite(x, y, t)
        a = self.arrival
        fixed = a <= now
        ros = self._ros[self._combo[self.src], self.step]
        keep = ~fixed[self.dst] & (ros > 0)
        src, dst = self.src[keep], self.dst[keep]
        front = np.unique(src[fixed[src]])
        if not len(front):
            return
        n, t0 = len(a), a[front].min()
        # one virtual source (vertex n) with an edge to each front cell, weighted by its arrival time
        m = csr_matrix((np.concatenate([self.length[keep] / ros[keep] * 60, a[front] - t0 + 1e-3]),
                        (np.concatenate([src, np.full(len(front), n)]), np.concatenate([dst, front]))),
                       shape=(n + 1, n + 1))
        t, pred = dijkstra(m, indices=n, return_predecessors=True)
        t, pred = t[:n] + t0, pred[:n]
        new = ~fixed & np.isfinite(t)
        self.arrival = np.where(fixed, a, np.maximum(t, now))   # never in the past
        cells = np.flatnonzero(new)
        steps = np.array([self._step_of[int(o)] for o in cells - pred[cells]], dtype=int)
        self.flame[cells] = self._flame[self._combo[cells], steps]

    def black(self, now: float) -> np.ndarray:
        return self.black0 | (self.arrival <= now - config.BURNOUT_S)

    def burning(self, now: float) -> np.ndarray:
        return (self.arrival <= now) & ~self.black(now)

    def unsafe_at(self, now: float) -> np.ndarray:
        """Clock time each cell gets within 4 x flame length of a burning cell; inf = never. The black is safe.

        ponytail: square window instead of a circle (up to 1.4x the distance at the corners, conservative).
        """
        nr, nc = (int(v) for v in self.g["shape"])
        black = self.black(now)
        a = np.where(black, np.inf, self.arrival)
        r = np.ceil(config.SEPARATION_FLAME_MULT * self.flame / float(self.g["res"])).astype(int)
        out = a.copy()
        for radius in np.unique(r[np.isfinite(a) & (r > 0)]):
            ar = np.where(r == radius, a, np.inf).reshape(nr, nc)
            out = np.minimum(out, minimum_filter(ar, size=2 * radius + 1, mode="constant", cval=np.inf).ravel())
        out[black] = np.inf
        return out

    def fire_min(self, now: float):
        """fire_min for evaluate(): minutes until unsafe, divided by the safety factor (§2.4)."""
        unsafe = self.unsafe_at(now)
        g = self.g
        nr, nc = (int(v) for v in g["shape"])

        def f(pts):
            row = np.clip(((g["ytop"] - pts[:, 1]) // g["res"]).astype(int), 0, nr - 1)
            col = np.clip(((pts[:, 0] - g["x0"]) // g["res"]).astype(int), 0, nc - 1)
            return np.maximum(unsafe[row * nc + col] - now, 0) / config.SAFETY_FACTOR / 60
        return f


def fire_areas(fire: FireGrid, now: float) -> dict:
    """engine/state `areas` (§5.5): the black, burning, and the bands the fire reaches in the next 0-5 / 5-10 min,
    each a GeoJSON MultiPolygon in lon/lat traced from the grid."""
    g, a = fire.g, fire.arrival
    nr, nc = (int(v) for v in g["shape"])
    transform = from_origin(float(g["x0"]), float(g["ytop"]), float(g["res"]), float(g["res"]))
    masks = {"black": fire.black(now), "burning": fire.burning(now),
             "front_5min": (a > now) & (a <= now + 300), "front_10min": (a > now + 300) & (a <= now + 600)}
    out = {}
    for name, mask in masks.items():
        m = mask.reshape(nr, nc).astype(np.uint8)
        polys = []
        for geom, _ in shapes(m, mask=m.astype(bool), transform=transform):
            rings = []
            for ring in geom["coordinates"]:
                lon, lat = _TO_LONLAT.transform(*np.array(ring).T)
                rings.append(np.round(np.column_stack([lon, lat]), 6).tolist())
            polys.append(rings)
        out[name] = {"type": "MultiPolygon", "coordinates": polys}
    return out


def fire_grid_msg(fire: FireGrid, now: float, t0: float) -> dict:
    """engine/state `fire_grid`: fire arrival per grid cell, so the dashboard can redraw the fire every frame
    (smooth, sub-cell fronts) instead of 1 s polygon steps. data = base64(zlib(uint16 little-endian, row-major,
    row 0 = north)): 0 = the black at Start (black0); k >= 1 = arrival at t0 + (k - 1) * unit_s;
    65535 = not within the next FIRE_GRID_HORIZON_S (or never). corners: lon/lat tl, tr, br, bl."""
    g, a = fire.g, fire.arrival
    nr, nc = (int(v) for v in g["shape"])
    unit, horizon = config.FIRE_GRID_UNIT_S, now + config.FIRE_GRID_HORIZON_S
    q = np.full(a.shape, 65535, np.uint16)
    soon = np.isfinite(a) & (a <= horizon)
    q[soon] = np.clip(np.round((a[soon] - t0) / unit) + 1, 1, 65534)
    q[fire.black0] = 0
    x0, ytop, res = float(g["x0"]), float(g["ytop"]), float(g["res"])
    xs, ys = [x0, x0 + nc * res, x0 + nc * res, x0], [ytop, ytop, ytop - nr * res, ytop - nr * res]
    lon, lat = _TO_LONLAT.transform(xs, ys)
    return {"cols": nc, "rows": nr, "t0": round(t0, 3), "unit_s": unit, "burnout_s": config.BURNOUT_S,
            "corners": [[round(float(o), 6), round(float(l), 6)] for o, l in zip(lon, lat)],
            "data": base64.b64encode(zlib.compress(q.astype("<u2").tobytes(), 6)).decode()}


def status_for(margin_min: float | None) -> str:
    if margin_min is None or margin_min >= config.GREEN_MIN:
        return "green"
    return "amber" if margin_min >= config.RED_MIN else "red"
