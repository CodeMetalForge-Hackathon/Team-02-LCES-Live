"""Fetch the Yarnell Hill demo data into data/yarnell/ (rerunnable, needs internet).

    uv run --with rasterio --with pyproj python data/fetch_yarnell.py

Outputs, all on one 10 m grid in EPSG:32612 (UTM 12N):
    dem.tif        USGS 3DEP elevation (m), float32
    fuel.tif       LANDFIRE LF2025 FBFM40 codes, uint16 (nearest neighbour from 30 m)
    slope.tif      degrees, Horn method
    aspect.tif     degrees from north the slope faces (downhill), 0 where flat (= gdaldem -zero_for_flat)
    hillshade.png  for the map; corners in meta.json (MapLibre image source)
Vectors (GeoJSON, EPSG:4326):
    perimeter.geojson  NIFC final perimeter, Yarnell Hill 2013
    osm.geojson        roads/tracks/trails, buildings, water, fences from OpenStreetMap
    sites.geojson      key points (OSM node ids in properties)
    meta.json          bounds, sources, fetch date, value summaries
"""
import datetime, json, pathlib, sys, time, urllib.parse, urllib.request

import numpy as np
import rasterio
from pyproj import Transformer

OUT = pathlib.Path(__file__).parent / "yarnell"
CRS = 32612
CENTER = (-112.7680, 34.2205)  # lon, lat: between the deployment site and Yarnell town
HALF = 2500                    # m, so the patch is 5 km x 5 km (crop to the table's 2 km later)
RES = 10                       # m
UA = {"User-Agent": "lces-hackathon-data/0.1"}

DEM_SVC = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
FUEL_SVC = "https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2025/LF2025_FBFM40_CONUS/ImageServer"
PERIM_SVC = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
             "InterAgencyFirePerimeterHistory_All_Years_View/FeatureServer/0/query")
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]

# Copied from OSM on 2026-11-07; ids let anyone re-check them.
SITES = [
    ("Fatality / deployment site", 4614226668, -112.77750, 34.22046),
    ("Observation deck (ridge)", 4614226669, -112.78221, 34.22146),
    ("Hotshot Journey Trailhead", 11757122998, -112.77446, 34.20349),
    ("Yarnell (town centre)", None, -112.74709, 34.22219),
]

to_utm = Transformer.from_crs(4326, CRS, always_xy=True)
to_ll = Transformer.from_crs(CRS, 4326, always_xy=True)
cx, cy = (round(v / RES) * RES for v in to_utm.transform(*CENTER))
X0, Y0, X1, Y1 = cx - HALF, cy - HALF, cx + HALF, cy + HALF
N = 2 * HALF // RES
LL = [to_ll.transform(x, y) for x, y in ((X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1))]
S, W = min(p[1] for p in LL), min(p[0] for p in LL)
NN, E = max(p[1] for p in LL), max(p[0] for p in LL)


def get(url, params=None, data=None, tries=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = urllib.parse.urlencode(data).encode() if data else None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, body, UA), timeout=120) as r:
                return r.read()
        except Exception as e:
            print(f"  retry {i + 1}/{tries}: {e}", file=sys.stderr)
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"failed: {url[:120]}")


def export_image(svc, path, pixel, interp):
    """Ask the ImageServer for exactly our grid, so no local reprojection is needed."""
    path.write_bytes(get(svc + "/exportImage", dict(
        bbox=f"{X0},{Y0},{X1},{Y1}", bboxSR=CRS, imageSR=CRS, size=f"{N},{N}",
        format="tiff", pixelType=pixel, interpolation=interp, f="image")))
    with rasterio.open(path) as r:
        a = r.read(1)
        assert r.crs.to_epsg() == CRS, r.crs
        assert (r.width, r.height) == (N, N), (r.width, r.height)
        assert np.allclose(tuple(r.bounds), (X0, Y0, X1, Y1), atol=0.5), r.bounds
        return a, r.profile


def slope_aspect(z, res):
    """Horn 3x3. Rows run north to south. Aspect = compass direction the slope faces."""
    p = np.pad(z, 1, mode="edge")
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    d, f = p[1:-1, :-2], p[1:-1, 2:]
    g, h, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * res)  # + = rises to the east
    dzds = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * res)  # + = rises to the south
    grad = np.hypot(dzdx, dzds)
    slope = np.degrees(np.arctan(grad))
    aspect = np.where(grad > 0, np.degrees(np.arctan2(-dzdx, dzds)) % 360, 0.0)
    return slope.astype("float32"), aspect.astype("float32")


def self_check():
    yy, xx = np.mgrid[0:5, 0:5].astype(float)
    s, a = slope_aspect(xx * 10, 10)          # rises 1 m per m to the east
    assert abs(s[2, 2] - 45) < 1e-6 and abs(a[2, 2] - 270) < 1e-6  # faces west
    s, a = slope_aspect(-yy * 10, 10)         # rises to the north (row 0 = north)
    assert abs(a[2, 2] - 180) < 1e-6          # faces south
    s, a = slope_aspect(np.zeros((5, 5)), 10)
    assert s.max() == 0 and a.max() == 0      # flat


def write_tif(name, arr, profile, dtype):
    p = dict(profile, driver="GTiff", dtype=dtype, count=1, compress="deflate", nodata=None)
    with rasterio.open(OUT / name, "w", **p) as w:
        w.write(arr.astype(dtype), 1)


def osm_geojson():
    bb = f"{S},{W},{NN},{E}"
    q = (f'[out:json][timeout:120];(way["highway"]({bb});way["building"]({bb});'
         f'way["natural"="water"]({bb});way["waterway"]({bb});way["barrier"]({bb}););out geom tags;')
    for url in OVERPASS:
        try:
            els = json.loads(get(url, data={"data": q}, tries=2))["elements"]
            break
        except Exception as e:
            print(f"  overpass {url} failed: {e}", file=sys.stderr)
    else:
        raise RuntimeError("all Overpass mirrors failed")
    feats = []
    for e in els:
        xy = [[n["lon"], n["lat"]] for n in e.get("geometry", [])]
        t = e.get("tags", {})
        area = len(xy) > 3 and xy[0] == xy[-1] and ("building" in t or t.get("natural") == "water")
        geom = {"type": "Polygon", "coordinates": [xy]} if area else {"type": "LineString", "coordinates": xy}
        kind = next((k for k in ("highway", "building", "waterway", "natural", "barrier") if k in t), "other")
        feats.append({"type": "Feature", "geometry": geom,
                      "properties": {"osm_id": e["id"], "kind": kind, **t}})
    return {"type": "FeatureCollection", "features": feats}


def main():
    self_check()
    OUT.mkdir(exist_ok=True)
    print(f"grid {N}x{N} @ {RES} m, EPSG:{CRS} [{X0},{Y0},{X1},{Y1}]")

    print("DEM (3DEP)...")
    dem, prof = export_image(DEM_SVC, OUT / "dem.tif", "F32", "RSP_BilinearInterpolation")
    assert np.isfinite(dem).all() and 500 < dem.min() and dem.max() < 2500, (dem.min(), dem.max())

    print("Fuel (LANDFIRE LF2025 FBFM40)...")
    fuel, _ = export_image(FUEL_SVC, OUT / "fuel.tif", "U16", "RSP_NearestNeighbor")
    codes, counts = np.unique(fuel, return_counts=True)
    valid = set(range(91, 100)) | set(range(101, 110)) | set(range(121, 125)) | set(range(141, 150)) \
        | set(range(161, 166)) | set(range(181, 190)) | set(range(201, 205))
    assert set(codes.tolist()) <= valid, f"unexpected fuel codes {set(codes.tolist()) - valid}"

    slope, aspect = slope_aspect(dem, RES)
    write_tif("slope.tif", slope, prof, "float32")
    write_tif("aspect.tif", aspect, prof, "float32")

    zen, az = np.radians(45), np.radians(315)
    s, a = np.radians(slope), np.radians(aspect)
    hs = np.clip(np.cos(zen) * np.cos(s) + np.sin(zen) * np.sin(s) * np.cos(az - a), 0, 1)
    with rasterio.open(OUT / "hillshade.png", "w", driver="PNG", width=N, height=N, count=1,
                       dtype="uint8") as w:
        w.write((hs * 255).astype("uint8"), 1)

    print("Perimeter (NIFC)...")
    perim = json.loads(get(PERIM_SVC, data=dict(
        where="INCIDENT='Yarnell Hill' AND FIRE_YEAR='2013'", outFields="*", outSR=4326, f="geojson")))
    assert len(perim["features"]) >= 1, "Yarnell Hill perimeter not found"
    (OUT / "perimeter.geojson").write_text(json.dumps(perim))

    print("OSM...")
    osm = osm_geojson()
    (OUT / "osm.geojson").write_text(json.dumps(osm))

    sites = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
         "properties": {"name": n, "osm_node": i, "utm": [round(v, 1) for v in to_utm.transform(lon, lat)]}}
        for n, i, lon, lat in SITES]}
    for f in sites["features"]:
        x, y = f["properties"]["utm"]
        assert X0 < x < X1 and Y0 < y < Y1, f"{f['properties']['name']} outside patch"
    (OUT / "sites.geojson").write_text(json.dumps(sites, indent=1))

    kinds = {}
    for f in osm["features"]:
        kinds[f["properties"]["kind"]] = kinds.get(f["properties"]["kind"], 0) + 1
    meta = {
        "fetched_utc": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "crs": f"EPSG:{CRS}", "res_m": RES, "size_px": N,
        "bounds_utm": [X0, Y0, X1, Y1],
        "corners_lonlat_tl_tr_br_bl": [list(LL[3]), list(LL[2]), list(LL[1]), list(LL[0])],
        "elev_m": [round(float(dem.min()), 1), round(float(dem.max()), 1)],
        "slope_deg_max": round(float(slope.max()), 1),
        "fuel_share": {int(c): round(n / fuel.size, 3) for c, n in zip(codes, counts)},
        "osm_counts": kinds,
        "perimeter_acres": [f["properties"].get("GIS_ACRES") for f in perim["features"]],
        "sources": {
            "dem": DEM_SVC + " (USGS 3DEP seamless, best available, resampled to 10 m)",
            "fuel": FUEL_SVC + " (LANDFIRE LF2025, 30 m native)",
            "perimeter": PERIM_SVC.rsplit("/", 1)[0] + " (NIFC InterAgency Fire Perimeter History)",
            "osm": "OpenStreetMap contributors, ODbL, via Overpass",
        },
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps({k: meta[k] for k in ("elev_m", "slope_deg_max", "fuel_share", "osm_counts",
                                            "perimeter_acres")}, indent=1))


if __name__ == "__main__":
    main()
