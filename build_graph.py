"""Routing graph for the crew (design doc §4, spec seam A, ADR 0001).

    uv run python build_graph.py           # build data/yarnell/graph.npz
    uv run python build_graph.py --test    # routing self-checks (synthetic grids)

Vertices: grid cells (id = row * ncols + col, row 0 = north), then trail vertices.
Edges: directed, cost in seconds, each direction its own entry.
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

DATA = Path(__file__).parent / "data"
YARNELL = DATA / "yarnell"
LANDFIRE = next(DATA.glob("landfire_*/"), None)

RES = 10.0                      # m
CROP_CENTRE = (34.222858, -112.78056)  # lat, lon: takes in lunch spot, anchor point, the black edge, ranch
CROP_CELLS = 200                # 2 km x 2 km
MAX_SLOPE_DEG = 38
TRAIL_STEP_M = 25
RANCH = (34.21952, -112.77091)  # SAIT Fig. 7; see data/yarnell/scenario.geojson
RANCH_RADIUS_M = 100
REPLAY_2013 = True              # SH5 fuel + REPLAY_VEG inside the 2013 perimeter; no post-2013 footways
REPLAY_VEG = 0.6                # calibrated: 2013 descent line (435 m, -130 m) in ~20 min (16:20 -> 16:42)
SH5 = 145

# veg factor (multiplier on Tobler speed) by shrub height x cover, §4.3. Placeholders: calibrate.
HEIGHT_EDGES_M = [0.5, 1.5]     # < 0.5, 0.5-1.5, > 1.5
COVER_EDGES_PCT = [25, 50]      # < 25, 25-50, > 50
SHRUB_TABLE = np.array([[0.9, 0.8, 0.7],
                        [0.7, 0.5, 0.3],
                        [0.5, 0.3, 0.1]])
TREE_FACTOR, OPEN_FACTOR = 0.7, 0.8   # pinyon-juniper; grass / bare / developed


def veg_factor(evc, evh):
    """LANDFIRE EVC/EVH codes -> veg factor. Shrub codes: EVC 2xx = cover %, EVH 2xx = height in dm."""
    evc, evh = np.asarray(evc), np.asarray(evh)
    out = np.full(evc.shape, OPEN_FACTOR)
    out[(evc > 100) & (evc < 200)] = TREE_FACTOR
    shrub = (evc >= 200) & (evc < 300)
    height = np.where((evh >= 200) & (evh < 300), (evh - 200) / 10, 1.0)  # unknown height: assume chest high
    hi = np.digitize(height, HEIGHT_EDGES_M)
    ci = np.digitize(evc - 200, COVER_EDGES_PCT, right=True)
    out[shrub] = SHRUB_TABLE[hi, ci][shrub]
    return out


def tobler_s(length, dz, factor):
    """Seconds to walk `length` m climbing `dz` m (§4.2)."""
    kmh = 6 * np.exp(-3.5 * np.abs(dz / length + 0.05)) * factor
    return length / (kmh / 3.6)


def build(elev, slope, veg, blocked, trails=(), x0=0.0, ytop=0.0, res=RES):
    """Terrain arrays (same shape) + trail lines (lists of UTM x, y) -> graph dict.

    blocked: cells that are impassable for any other reason (water, buildings, fences)."""
    nr, nc = elev.shape
    n = nr * nc
    bad = (blocked | (slope > MAX_SLOPE_DEG)).ravel()
    z, v = elev.ravel(), veg.ravel()
    ids = np.arange(n).reshape(nr, nc)
    src, dst, cost = [], [], []

    def add(a, b, length):
        dz = z[b] - z[a]
        f = np.minimum(v[a], v[b])
        for s, t, d in ((a, b, dz), (b, a, -dz)):
            src.append(s); dst.append(t); cost.append(tobler_s(length, d, f))

    # store E, SE, S, SW per cell; the other 4 are the same edges from the neighbour
    for dr, dc in ((0, 1), (1, 1), (1, 0), (1, -1)):
        a = ids[:nr - dr, max(0, -dc):nc - max(0, dc)].ravel()
        b = a + dr * nc + dc
        ok = ~bad[a] & ~bad[b]
        if dr and dc:  # no corner-cutting past an impassable cell
            ok &= ~bad[a + dc] & ~bad[a + dr * nc]
        add(a[ok], b[ok], res * np.hypot(dr, dc))

    # cell centres
    cols, rows = np.meshgrid(np.arange(nc), np.arange(nr))
    xs = list(x0 + (cols.ravel() + 0.5) * res)
    ys = list(ytop - (rows.ravel() + 0.5) * res)
    zs = list(z)

    # trails: cut into <= TRAIL_STEP_M pieces; shared coordinates become one vertex (junctions)
    vid = {}

    def vertex(x, y):
        k = (round(x, 1), round(y, 1))
        if k not in vid:
            c, r = int((x - x0) // res), int((ytop - y) // res)
            vid[k] = len(xs)
            xs.append(x); ys.append(y); zs.append(z[r * nc + c])
            if not bad[r * nc + c]:  # connector edge to the cell it sits in
                ta, tb = np.array([vid[k]]), np.array([r * nc + c])
                d = max(np.hypot(x - xs[r * nc + c], y - ys[r * nc + c]), 0.1)
                dz = z[r * nc + c] - zs[vid[k]]
                for s, t, dd in ((ta, tb, dz), (tb, ta, -dz)):
                    src.append(s); dst.append(t); cost.append(np.atleast_1d(tobler_s(d, dd, min(1.0, v[tb[0]]))))
        return vid[k]

    tz = []  # trail edges (a, b, length)
    for line in trails:
        for (xa, ya), (xb, yb) in zip(line, line[1:]):
            length = np.hypot(xb - xa, yb - ya)
            k = max(1, int(np.ceil(length / TRAIL_STEP_M)))
            pts = [(xa + (xb - xa) * i / k, ya + (yb - ya) * i / k) for i in range(k + 1)]
            vs = [vertex(*p) for p in pts]
            tz += [(p, q, length / k) for p, q in zip(vs, vs[1:]) if p != q]
    if tz:
        a, b, L = (np.array(t) for t in zip(*tz))
        zz = np.array(zs)
        for s, t, sign in ((a, b, 1), (b, a, -1)):
            src.append(s); dst.append(t); cost.append(tobler_s(L, sign * (zz[b] - zz[a]), 1.0))

    return dict(x=np.array(xs), y=np.array(ys), src=np.concatenate(src).astype(np.int32),
                dst=np.concatenate(dst).astype(np.int32), cost_s=np.concatenate(cost),
                shape=np.array([nr, nc]), x0=x0, ytop=ytop, res=res)


def matrix(g, closed=None):
    """Sparse matrix over the graph; `closed` (bool per vertex) drops every edge touching those vertices."""
    keep = slice(None) if closed is None else ~(closed[g["src"]] | closed[g["dst"]])
    n = len(g["x"])
    return csr_matrix((g["cost_s"][keep], (g["src"][keep], g["dst"][keep])), shape=(n, n))


def route(g, start, targets, closed=None):
    """Fastest walk from `start` to any of `targets` -> (seconds, [vertex ids]); (inf, []) if none."""
    t, pred = dijkstra(matrix(g, closed), indices=start, return_predecessors=True)
    targets = np.asarray(targets)
    end = targets[np.argmin(t[targets])]
    if not np.isfinite(t[end]):
        return np.inf, []
    path = [end]
    while path[-1] != start:
        path.append(pred[path[-1]])
    return t[end], path[::-1]


def cell(g, x, y):
    """Snap a UTM point to its grid cell id."""
    nr, nc = g["shape"]
    return int((g["ytop"] - y) // g["res"]) * nc + int((x - g["x0"]) // g["res"])


# ---------------------------------------------------------------- real data

def build_yarnell():
    import rasterio
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform, transform_geom

    def utm(lat, lon):
        (x,), (y,) = transform("EPSG:4326", "EPSG:32612", [lon], [lat])
        return x, y

    with rasterio.open(YARNELL / "dem.tif") as s:
        T = s.transform
        cx, cy = utm(*CROP_CENTRE)
        c0 = round((cx - T.c) / RES) - CROP_CELLS // 2
        r0 = round((T.f - cy) / RES) - CROP_CELLS // 2
        win = ((r0, r0 + CROP_CELLS), (c0, c0 + CROP_CELLS))
        x0, ytop = T.c + c0 * RES, T.f - r0 * RES
    crop_t = from_origin(x0, ytop, RES, RES)
    shape = (CROP_CELLS, CROP_CELLS)

    def read(name):
        with rasterio.open(YARNELL / name) as s:
            return s.read(1, window=win)
    elev, slope, aspect, fuel = (read(f"{k}.tif") for k in ("dem", "slope", "aspect", "fuel"))

    def landfire(kind):
        out = np.full(shape, 32767, np.int32)
        with rasterio.open(LANDFIRE / f"LF2025_{kind}_CONUS" / f"LF2025_{kind}_CONUS.tif") as s:
            reproject(rasterio.band(s, 1), out, dst_transform=crop_t, dst_crs="EPSG:32612",
                      resampling=Resampling.nearest)
        return out
    veg = veg_factor(landfire("EVC"), landfire("EVH"))

    def burn(geoms, **kw):
        geoms = [transform_geom("EPSG:4326", "EPSG:32612", gm) for gm in geoms]
        return rasterize(geoms, out_shape=shape, transform=crop_t, dtype=np.uint8, **kw).astype(bool) \
            if geoms else np.zeros(shape, bool)

    osm = json.loads((YARNELL / "osm.geojson").read_text())["features"]
    p = lambda f, k: f["properties"].get(k)
    blocked = burn([f["geometry"] for f in osm if p(f, "building") or p(f, "natural") == "water"]) \
        | burn([f["geometry"] for f in osm if p(f, "barrier")], all_touched=True)
    # streams are left passable: dry washes here (the 2013 crew walked down them)

    if REPLAY_2013:
        perim = json.loads((YARNELL / "perimeter.geojson").read_text())["features"]
        inside = burn([f["geometry"] for f in perim])
        veg[inside] = REPLAY_VEG
        fuel = np.where(inside, SH5, fuel)

    trails = []
    xmax, ybot = x0 + CROP_CELLS * RES, ytop - CROP_CELLS * RES
    for f in osm:
        if REPLAY_2013 and p(f, "highway") == "footway":
            continue  # Hotshot Journey Trail (2016) runs down the 2013 descent line
        if p(f, "highway") and f["geometry"]["type"] == "LineString":
            lon, lat = zip(*f["geometry"]["coordinates"])
            xs, ys = transform("EPSG:4326", "EPSG:32612", lon, lat)
            run = []
            for pt in zip(xs, ys):  # keep runs of points inside the crop
                if x0 <= pt[0] < xmax and ybot < pt[1] <= ytop:
                    run.append(pt)
                else:
                    if len(run) > 1: trails.append(run)
                    run = []
            if len(run) > 1: trails.append(run)

    g = build(elev, slope, veg, blocked, trails, x0, ytop)
    rx, ry = utm(*RANCH)
    n = CROP_CELLS * CROP_CELLS
    g["ranch"] = np.flatnonzero(np.hypot(g["x"][:n] - rx, g["y"][:n] - ry) <= RANCH_RADIUS_M)
    g.update(elev=elev, slope=slope, aspect=aspect, fuel=fuel, veg=veg, blocked=blocked)
    np.savez_compressed(YARNELL / "graph.npz", **g)

    # sanity print: deployment site -> ranch (~600 yd)
    sx, sy = utm(34.22047, -112.77757)
    t, path = route(g, cell(g, sx, sy), g["ranch"])
    print(f"crop x0={x0} ytop={ytop}; vertices {len(g['x'])} (trail {len(g['x']) - n}); "
          f"edges {len(g['src'])}; blocked cells {blocked.sum()}; ranch cells {len(g['ranch'])}")
    print(f"deployment site -> ranch: {t / 60:.1f} min over {len(path)} vertices")

    # calibration print: the 2013 descent line walked at this veg factor (SAIT: ~20 min)
    sc = json.loads((YARNELL / "scenario.geojson").read_text())["features"]
    lon, lat = zip(*next(f for f in sc if f["properties"]["name"] == "2013 descent line")["geometry"]["coordinates"])
    ex, ny = transform("EPSG:4326", "EPSG:32612", lon, lat)
    z = np.array([elev.ravel()[cell(g, a, b)] for a, b in zip(ex, ny)])
    t = tobler_s(np.hypot(np.diff(ex), np.diff(ny)), np.diff(z), REPLAY_VEG if REPLAY_2013 else 0.5).sum()
    print(f"2013 descent line: {t / 60:.1f} min (SAIT: ~20 min)")


# ---------------------------------------------------------------- self-checks

def _flat(nr, nc, veg=1.0):
    z = np.zeros((nr, nc))
    return z, z.copy(), np.full((nr, nc), veg), np.zeros((nr, nc), bool)


def test():
    # uphill slower than downhill: 100 m climbing 10 m ~ 101 s vs ~ 71 s
    elev, slope, veg, blk = _flat(1, 11)
    elev[0] = np.arange(11)  # +1 m per 10 m cell
    g = build(elev, slope, veg, blk)
    up, _ = route(g, 0, [10])
    down, _ = route(g, 10, [0])
    assert abs(up - 101.5) < 1 and abs(down - 71.5) < 1, (up, down)

    # detours around a barrier
    elev, slope, veg, blk = _flat(10, 10)
    blk[:9, 5] = True
    g = build(elev, slope, veg, blk)
    t, path = route(g, 0, [9])
    assert path and not blk.ravel()[path].any() and max(p // 10 for p in path) == 9, path

    # no diagonal corner-cutting; both corners blocked -> no route
    elev, slope, veg, blk = _flat(2, 2)
    blk[0, 1] = True
    g = build(elev, slope, veg, blk)
    t, path = route(g, 0, [3])
    assert path == [0, 2, 3], path
    blk[1, 0] = True
    assert route(build(elev, slope, veg, blk), 0, [3]) == (np.inf, [])

    # steep cells are impassable
    elev, slope, veg, blk = _flat(1, 3)
    slope[0, 1] = 40
    assert route(build(elev, slope, veg, blk), 0, [2])[1] == []

    # trail preferred over a parallel stretch of brush
    elev, slope, veg, blk = _flat(3, 21, veg=0.3)
    g = build(elev, slope, veg, blk, trails=[[(5, -5), (205, -5)]])
    t, path = route(g, 0, [20])
    n = 3 * 21
    assert any(p >= n for p in path), path
    brush, _ = route(build(elev, slope, veg, blk), 0, [20])
    assert t < brush / 2, (t, brush)

    # brush slows but never blocks
    fast, _ = route(build(*_flat(5, 5)), 0, [24])
    slow, _ = route(build(*_flat(5, 5, veg=0.1)), 0, [24])
    assert np.isfinite(slow) and abs(slow / fast - 10) < 0.01

    # closures drop edges; route goes around
    elev, slope, veg, blk = _flat(3, 3)
    g = build(elev, slope, veg, blk)
    closed = np.zeros(9, bool); closed[[1, 4]] = True
    path = route(g, 0, [2], closed)[1]
    assert path and not set(path) & {1, 4}, path

    # veg factor lookup: shrub 1.2 m / 30 % -> 0.5; 2.0 m / 51 % -> 0.1; tree -> 0.7; herb -> 0.8
    assert list(veg_factor([230, 251, 120, 330], [212, 220, 105, 302])) == [0.5, 0.1, 0.7, 0.8]
    print("build_graph: all checks pass")


if __name__ == "__main__":
    test() if "--test" in sys.argv else build_yarnell()
