"""Dashboard backdrop over a wider area than the engine (tickets 07, 08; docs/specs/demo-dashboard). Run on a laptop:

    uv run python render_map_layers.py           # fetches once (needs internet), then renders
    uv run python render_map_layers.py --fetch   # fetch everything again
    uv run python render_map_layers.py --osm     # retry only roads / buildings (Overpass is often busy)

Display only: the engine keeps data/yarnell/dem.tif, fuel.tif and graph.npz (5 km x 5 km). This covers
12 km x 12 km around the same centre, on the same 10 m UTM grid (pixel-aligned with dem.tif), so the
whole 2013 perimeter, Yarnell and Peeples Valley are on the map.

data/yarnell/wide/
    dem.tif, fuel.tif, osm.geojson   sources (USGS 3DEP, LANDFIRE LF2025 FBFM40, OpenStreetMap via Overpass)
    terrain.png      RGB: elevation colour ramp (desert palette) x hillshade
    slope.png        RGB: slope colour ramp (flat green -> steep red) x hillshade; slope drives fire spread
    hillshade.png    grey relief
    fuel.png         RGBA: FBFM40 fuel groups, semi-transparent; SH5 inside the 2013 perimeter (the engine's replay fuel)
    map_osm.geojson  roads / tracks (no post-2013 footways) and buildings, properties reduced to "kind"
    meta.json        corners (MapLibre image source), bounds, sources
"""
import datetime
import json
import sys
import time
import urllib.parse
import urllib.request
import warnings
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.warp import transform_geom

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)  # plain PNGs, placed by corners

YARNELL = Path(__file__).parent / "data" / "yarnell"
WIDE = YARNELL / "wide"
CRS, RES, HALF = 32612, 10, 6000            # 12 km x 12 km at 10 m = 1200 x 1200 px
CENTER = (-112.7680, 34.2205)               # same centre as data/fetch_yarnell.py, so the grids line up
DEM_SVC = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
FUEL_SVC = "https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2025/LF2025_FBFM40_CONUS/ImageServer"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
UA = {"User-Agent": "lces-hackathon-map/0.2 (+https://github.com/CodeMetalForge-Hackathon/Team-2)",
      "Accept": "application/json, image/tiff, */*"}

SH5 = 145
# elevation ramp, position 0..1 over the DEM range -> RGB
RAMP = [(0.00, (214, 196, 150)), (0.30, (190, 168, 112)), (0.55, (148, 152, 92)),
        (0.80, (124, 112, 84)), (1.00, (168, 158, 146))]
# FBFM40 group -> RGBA (non-burnable 91-99 stays transparent)
FUEL_RGBA = [((101, 109), (236, 214, 110, 140)),   # grass
             ((121, 124), (204, 178, 98, 140)),    # grass-shrub
             ((141, 149), (128, 146, 58, 150)),    # shrub
             ((SH5, SH5), (86, 104, 40, 120)),     # SH5: old dense chaparral (2013 replay)
             ((161, 165), (62, 122, 66, 160)),     # timber understory
             ((181, 189), (62, 122, 66, 160))]     # timber litter
# slope (deg) -> RGB
SLOPE_RAMP = [(0, (88, 150, 96)), (10, (186, 196, 92)), (20, (232, 188, 76)), (30, (226, 128, 60)), (45, (178, 52, 48))]
ROADS = {"primary", "secondary", "tertiary", "residential", "unclassified", "service"}

to_utm = Transformer.from_crs(4326, CRS, always_xy=True)
to_ll = Transformer.from_crs(CRS, 4326, always_xy=True)
cx, cy = (round(v / RES) * RES for v in to_utm.transform(*CENTER))
X0, Y0, X1, Y1 = cx - HALF, cy - HALF, cx + HALF, cy + HALF
N = 2 * HALF // RES


def get(url, params=None, data=None, tries=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = urllib.parse.urlencode(data).encode() if data else None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, body, UA), timeout=180) as r:
                return r.read()
        except Exception as e:
            print(f"  retry {i + 1}/{tries}: {e}", file=sys.stderr)
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"failed: {url[:120]}")


def export_image(svc, path, pixel, interp):
    """Ask the ImageServer for exactly our grid (no local reprojection)."""
    path.write_bytes(get(svc + "/exportImage", dict(
        bbox=f"{X0},{Y0},{X1},{Y1}", bboxSR=CRS, imageSR=CRS, size=f"{N},{N}",
        format="tiff", pixelType=pixel, interpolation=interp, f="image")))
    with rasterio.open(path) as r:
        assert r.crs.to_epsg() == CRS and (r.width, r.height) == (N, N), (r.crs, r.width, r.height)
        assert np.allclose(tuple(r.bounds), (X0, Y0, X1, Y1), atol=0.5), r.bounds


def fetch(force=False):
    WIDE.mkdir(exist_ok=True)
    print(f"fetching {2 * HALF // 1000} km x {2 * HALF // 1000} km ({N} x {N} px)...")
    if force or not (WIDE / "dem.tif").exists():
        export_image(DEM_SVC, WIDE / "dem.tif", "F32", "RSP_BilinearInterpolation")
    if force or not (WIDE / "fuel.tif").exists():
        export_image(FUEL_SVC, WIDE / "fuel.tif", "U16", "RSP_NearestNeighbor")
    if force or not (WIDE / "osm.geojson").exists():
        try:
            fetch_osm()
        except RuntimeError as e:   # Overpass is often at capacity: fall back to the 5 km osm.geojson
            print(f"  OSM: {e}; using data/yarnell/osm.geojson (5 km) for roads and buildings. Rerun later.",
                  file=sys.stderr)


def fetch_osm(tries=6):
    ll = [to_ll.transform(x, y) for x, y in ((X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1))]
    bb = f"{min(p[1] for p in ll)},{min(p[0] for p in ll)},{max(p[1] for p in ll)},{max(p[0] for p in ll)}"
    q = f'[out:json][timeout:180];(way["highway"]({bb});way["building"]({bb}););out geom tags;'
    for i in range(tries):   # Overpass asks for exponential backoff when at capacity
        try:
            els = json.loads(get(OVERPASS[i % len(OVERPASS)], data={"data": q}, tries=1))["elements"]
            break
        except Exception as e:
            print(f"  overpass try {i + 1}/{tries} failed: {e}; waiting {15 * 2 ** i} s", file=sys.stderr)
            time.sleep(15 * 2 ** i)
    else:
        raise RuntimeError("Overpass failed")
    feats = []
    for e in els:
        xy = [[n["lon"], n["lat"]] for n in e.get("geometry", [])]
        t = e.get("tags", {})
        poly = len(xy) > 3 and xy[0] == xy[-1] and "building" in t
        feats.append({"type": "Feature", "properties": {"osm_id": e["id"], **t},
                      "geometry": {"type": "Polygon", "coordinates": [xy]} if poly
                      else {"type": "LineString", "coordinates": xy}})
    (WIDE / "osm.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    (WIDE / "fetched_utc.txt").write_text(datetime.datetime.now(datetime.timezone.utc).isoformat() + "\n")


def slope_aspect(z, res):
    """Horn 3x3, as data/fetch_yarnell.py. Rows run north to south."""
    p = np.pad(z, 1, mode="edge")
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    d, f = p[1:-1, :-2], p[1:-1, 2:]
    g, h, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * res)
    dzds = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * res)
    grad = np.hypot(dzdx, dzds)
    return np.degrees(np.arctan(grad)), np.where(grad > 0, np.degrees(np.arctan2(-dzdx, dzds)) % 360, 0.0)


def write_png(path, bands):
    with rasterio.open(path, "w", driver="PNG", width=bands.shape[2], height=bands.shape[1],
                       count=bands.shape[0], dtype="uint8") as w:
        w.write(bands)


def ramp(values, stops):
    pos, cols = [p for p, _ in stops], np.array([c for _, c in stops], float)
    return np.stack([np.interp(values, pos, cols[:, k]) for k in range(3)])


def render():
    with rasterio.open(WIDE / "dem.tif") as r:
        dem, transform, shape = r.read(1).astype(float), r.transform, (r.height, r.width)
    with rasterio.open(WIDE / "fuel.tif") as r:
        fuel = r.read(1)
    slope, aspect = slope_aspect(dem, RES)
    zen, az = np.radians(45), np.radians(315)
    hs = np.clip(np.cos(zen) * np.cos(np.radians(slope))
                 + np.sin(zen) * np.sin(np.radians(slope)) * np.cos(az - np.radians(aspect)), 0, 1)

    write_png(WIDE / "hillshade.png", (hs * 255).astype(np.uint8)[None])
    z = (dem - dem.min()) / (dem.max() - dem.min())
    write_png(WIDE / "terrain.png", np.clip(ramp(z, RAMP) * (0.45 + 0.75 * hs ** 0.9), 0, 255).astype(np.uint8))
    write_png(WIDE / "slope.png", np.clip(ramp(slope, SLOPE_RAMP) * (0.6 + 0.5 * hs), 0, 255).astype(np.uint8))

    # fuel: 2013 replay override as in build_graph.py (SH5 inside the perimeter)
    perim = json.loads((YARNELL / "perimeter.geojson").read_text())["features"]
    inside = rasterize([transform_geom("EPSG:4326", f"EPSG:{CRS}", f["geometry"]) for f in perim], out_shape=shape,
                       transform=transform, dtype=np.uint8).astype(bool)
    fuel = np.where(inside, SH5, fuel)
    rgba = np.zeros((4, *shape), np.uint8)
    for (lo, hi), c in FUEL_RGBA:
        m = (fuel >= lo) & (fuel <= hi)
        for k in range(4):
            rgba[k][m] = c[k]
    write_png(WIDE / "fuel.png", rgba)

    # corners for the MapLibre image sources: tl, tr, br, bl
    corners = [list(to_ll.transform(x, y)) for x, y in ((X0, Y1), (X1, Y1), (X1, Y0), (X0, Y0))]
    lons, lats = [c[0] for c in corners], [c[1] for c in corners]
    inb = lambda pt: min(lons) <= pt[0] <= max(lons) and min(lats) <= pt[1] <= max(lats)
    rnd = lambda c: [round(c[0], 6), round(c[1], 6)] if isinstance(c[0], (int, float)) else [rnd(x) for x in c]
    pts = lambda c: [c] if isinstance(c[0], (int, float)) else [p for x in c for p in pts(x)]
    out = []
    osm_src = WIDE / "osm.geojson" if (WIDE / "osm.geojson").exists() else YARNELL / "osm.geojson"
    for f in json.loads(osm_src.read_text())["features"]:
        p, g = f["properties"], f["geometry"]
        if len(pts(g["coordinates"])) < 2:
            continue
        if p.get("highway") in ROADS and g["type"] == "LineString":
            kind = "road"
        elif p.get("highway") == "track" and g["type"] == "LineString":
            kind = "track"
        elif p.get("building") and g["type"] == "Polygon":
            kind = "building"
        else:
            continue  # footways (2016 Hotshot Journey Trail on the descent line), paths, steps
        if any(inb(pt) for pt in pts(g["coordinates"])):
            out.append({"type": "Feature", "properties": {"kind": kind},
                        "geometry": {"type": g["type"], "coordinates": rnd(g["coordinates"])}})
    (WIDE / "map_osm.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": out},
                                                     separators=(",", ":")))
    (WIDE / "meta.json").write_text(json.dumps({
        "crs": f"EPSG:{CRS}", "res_m": RES, "size_px": N, "bounds_utm": [X0, Y0, X1, Y1],
        "corners_lonlat_tl_tr_br_bl": corners, "elev_m": [round(dem.min(), 1), round(dem.max(), 1)],
        "sources": {"dem": DEM_SVC, "fuel": FUEL_SVC, "osm": "OpenStreetMap contributors, ODbL, via Overpass"},
    }, indent=1))

    kinds = {k: sum(f["properties"]["kind"] == k for f in out) for k in ("road", "track", "building")}
    size = lambda n: f"{(WIDE / n).stat().st_size // 1024} KB"
    print(f"wide/ {N}x{N} px, {2 * HALF // 1000} km, elevation {dem.min():.0f}-{dem.max():.0f} m, slope max {slope.max():.0f} deg")
    print(f"  terrain.png {size('terrain.png')}, slope.png {size('slope.png')}, hillshade.png {size('hillshade.png')}, "
          f"fuel.png {size('fuel.png')}")
    print(f"  map_osm.geojson {kinds}, {size('map_osm.geojson')}")


if __name__ == "__main__":
    if "--osm" in sys.argv:          # only retry the OpenStreetMap download
        fetch_osm()
    else:
        fetch(force="--fetch" in sys.argv)
    render()
