"""Margin computation (docs/LCES_Design.md §4, §5): terrain, routes, crew and fire times.

Pure computation, no MQTT. engine.py feeds it live data.

Routes: with data/yarnell/graph.npz (build_graph.py), the fastest route from the crew's cell to
each safety zone over the routing graph (graph_routes, ADR 0001). Without it (v1, testing only),
the LineStrings in routes.geojson, each evaluated on its own along its densified points.
"Rerouting" = switching the active route to the one with the best margin.
"""
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

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
    return g


def crew_start_xy(path: Path = config.SCENARIO_FILE) -> tuple[float, float]:
    """The descent point (the spec's "saddle"): crew start at 16:20."""
    f = next(f for f in json.loads(Path(path).read_text())["features"]
             if f["properties"]["name"] == "Descent point (crew start)")
    return to_utm(*f["geometry"]["coordinates"][:2])


# (route id, safety zone name, graph key of its cells, radius for the 4 x flame check)
SAFETY_ZONES = (("ranch", "Boulder Springs Ranch", "ranch", build_graph.RANCH_RADIUS_M),
                ("black", "the black", "black0", 500))


def graph_routes(g: dict, crew_xy) -> list[Route]:
    """Fastest route from the crew's cell to each safety zone. Times are the graph's edge costs."""
    n = int(g["elev"].size)
    start = build_graph.cell(g, *crew_xy)
    if not g["_ok"][start]:              # impassable cell (steep, building): start from the nearest open one
        open_ = np.flatnonzero(g["_ok"][:n])
        start = int(open_[np.argmin(np.hypot(g["x"][open_] - crew_xy[0], g["y"][open_] - crew_xy[1]))])
    veg = g["veg"].ravel()
    routes = []
    for rid, to, key, radius in SAFETY_ZONES:
        _, path = build_graph.route(g, start, g[key])
        if not path:
            continue
        path = np.array(path)
        seg_s = np.asarray(g["_m"][path[:-1], path[1:]]).ravel()
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


def spreads_for(fires: list[FirePoint], wx: Weather, terrain: Terrain) -> list[tuple[FirePoint, Spread]]:
    return [(f, Spread(terrain.fire_fuel(f.x, f.y), terrain.value("slope", f.x, f.y),
                       terrain.value("aspect", f.x, f.y), wx)) for f in fires]


def evaluate(routes: list[Route], spreads, crew_xy, now: float) -> list[RouteResult]:
    """margin(route) = min over its points of (fire arrival - crew arrival), in minutes (§5)."""
    results = []
    for route in routes:
        k, t_crew_s = route.crew_seconds(crew_xy)
        t_crew = t_crew_s / 60
        eta = float(t_crew[-1])
        if not spreads:
            results.append(RouteResult(route, None, None, None, None, eta))
            continue
        pts = route.xy[k:]
        t_fire = np.array([min(arrival_min(sp, (f.x, f.y), p, (now - f.t0) / 60) for f, sp in spreads)
                           for p in pts])
        m = t_fire - t_crew
        i = int(np.argmin(m))
        if not math.isfinite(m[i]):          # fire never reaches this route (e.g. non-burnable)
            results.append(RouteResult(route, None, None, None, None, eta))
            continue
        results.append(RouteResult(route, float(m[i]), k + i, float(t_fire[i]), float(t_crew[i]), eta))
    return results


def status_for(margin_min: float | None) -> str:
    if margin_min is None or margin_min >= config.GREEN_MIN:
        return "green"
    return "amber" if margin_min >= config.RED_MIN else "red"
