"""Build the data block for animation/index.html (Yarnell Hill story animation).

    uv run --with rasterio --with scipy --with pillow python animation/build_data.py

Needs the overview slides rendered at 200 dpi (pdftoppm -r 200 -png docs/YarnellOverview.pdf <dir>/s)
in the folder given by $SLIDES, and the satellite export (curl line below) at $SAT. Writes the DATA line between the markers in index.html.

Fire arrival time per cell: nested fire areas at known times (slides traced by colour + scenario.geojson
+ NIFC final perimeter); between two areas the time is interpolated by distance to each edge.
"""
import base64, json, os, pathlib, re
import numpy as np, rasterio
from PIL import Image, ImageDraw
from rasterio.windows import from_bounds
from scipy import ndimage as nd

ROOT = pathlib.Path(__file__).resolve().parent.parent
SLIDES = pathlib.Path(os.environ["SLIDES"])
LON0, LON1, LAT0, LAT1 = -112.845, -112.705, 34.186, 34.296
NX, NY = 700, 550  # ~18 m x 20 m cells
lon = LON0 + (np.arange(NX) + .5) * (LON1 - LON0) / NX
lat = LAT1 - (np.arange(NY) + .5) * (LAT1 - LAT0) / NY
LON, LAT = np.meshgrid(lon, lat)

# --- DEM ---
with rasterio.open(ROOT / "docs/USGS_13_n35w113.tif") as d:
    dem = d.read(1, window=from_bounds(LON0, LAT0, LON1, LAT1, d.transform), out_shape=(NY, NX),
                 resampling=rasterio.enums.Resampling.average).astype(np.float32)
print("elev", dem.min(), dem.max())

# --- slide masks -> lon/lat (affine fitted to scenario 16:00, checked on the lunch-spot marker ~25 m) ---
AX, BX, AY, BY = -112.84171, 6.119074e-05, 34.28169, 4.852497e-05


def slide_mask(p, it=32):
    a = np.asarray(Image.open(SLIDES / f"s{p}-{p:02d}.png")).astype(int)[..., :3]
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    m = (r > 140) & (g < 90) & (b < 90) & (r - g > 80)
    m[:340] = 0; m[1275:, :660] = 0
    yy, xx = np.ogrid[:m.shape[0], :m.shape[1]]
    m[(xx - 868) ** 2 + (yy - 1129) ** 2 < 121] = 0  # lunch-spot marker (red dot)
    m = nd.binary_opening(m)
    f = nd.binary_erosion(nd.binary_fill_holes(nd.binary_dilation(m, iterations=it)), iterations=it)
    lab, n = nd.label(f)
    f = lab == np.argmax(nd.sum(f, lab, range(1, n + 1))) + 1
    px = np.clip(((LON - AX) / BX).round().astype(int), 0, f.shape[1] - 1)
    py = np.clip(((AY - LAT) / BY).round().astype(int), 0, f.shape[0] - 1)
    return f[py, px]


def poly_mask(rings):
    im = Image.new("1", (NX, NY))
    for ring in rings:
        pts = [((x - LON0) / (LON1 - LON0) * NX, (LAT1 - y) / (LAT1 - LAT0) * NY) for x, y in ring]
        ImageDraw.Draw(im).polygon(pts, fill=1)
    return np.asarray(im)


# fix the affine offsets from the fitted bbox (done once in the scratch fit, values above)
sc = json.load(open(ROOT / "data/yarnell/scenario.geojson"))["features"]
scn = {f["properties"]["name"]: f for f in sc}
per = json.load(open(ROOT / "data/yarnell/perimeter.geojson"))["features"][0]["geometry"]
per_rings = per["coordinates"] if per["type"] == "Polygon" else [r for p in per["coordinates"] for r in p]

# minutes since ignition (Jun 28 17:00)
stages = [
    (900, slide_mask(9)),  # Jun 29 AM, 11 ac
    (1500, slide_mask(10)),  # Jun 29 PM, 113 ac
    (2460, slide_mask(13)),  # Jun 30 10:00, 418 ac
    (2640, slide_mask(15)),  # 13:00, 1812 ac
    (2760, slide_mask(16)),  # 15:00, 2413 ac
    (2820, poly_mask(scn["fire area 16:00"]["geometry"]["coordinates"])),
    (2835, poly_mask(scn["fire area 16:15"]["geometry"]["coordinates"])),
    (2850, poly_mask(scn["fire area 16:30"]["geometry"]["coordinates"])),
    (2860, poly_mask(scn["fire area 16:40"]["geometry"]["coordinates"])),
    (2870, poly_mask(scn["fire area 16:50"]["geometry"]["coordinates"])),
    (2880, slide_mask(24)),  # 17:00, 6206 ac
    (3120, poly_mask(per_rings)),  # final perimeter (Jul 6); shown as "late Jun 30 on"
]
o9 = stages[0][1]
oy, ox = (int(v) for v in np.argwhere(o9).mean(0).round())
ORIGIN = [float(lon[ox]), float(lat[oy])]

T = np.full((NY, NX), np.inf, np.float32)
prev = np.zeros((NY, NX), bool); prev[oy, ox] = True; tprev = 0.0
T[oy, ox] = 0
for t, m in stages:
    m = m | prev
    new = m & ~prev
    a = nd.distance_transform_edt(~prev)  # distance to the previous area
    b = nd.distance_transform_edt(m)  # distance to the outside of this area
    T[new] = tprev + (t - tprev) * a[new] / (a[new] + b[new])
    prev, tprev = m, t
    print(t, "acres", round(m.sum() * 18.4 * 20 / 4047))

# --- crew path: lunch spot -> two-track road -> descent point (16:20) -> deployment site (16:39) ---
rt = {f["properties"]["id"]: f for f in json.load(open(ROOT / "data/yarnell/routes.geojson"))["features"]}
road = rt["black"]["geometry"]["coordinates"][::-1]  # ends at the descent point
lunch = scn["Lunch spot"]["geometry"]["coordinates"]
descent = scn["2013 descent line"]["geometry"]["coordinates"]
pts = lambda c: [[round(x, 6), round(y, 6)] for x, y in c]

dep = scn["Deployment site"]["geometry"]["coordinates"]
di, dj = int((LAT1 - dep[1]) / (LAT1 - LAT0) * NY), int((dep[0] - LON0) / (LON1 - LON0) * NX)
print("T at deployment site", T[di, dj], "(16:42 = 2862)")

# satellite: USGS ImageryOnly export over the same bounds (EPSG:4326), muted so the fire reads on top
#   curl -o sat.jpg "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export?bbox=-112.845,34.186,-112.705,34.296&bboxSR=4326&imageSR=4326&size=4000,3140&format=jpg&f=image"
from PIL import ImageEnhance
import io
sat = Image.open(os.environ["SAT"]).convert("RGB").resize((3600, 2826), Image.LANCZOS)
sat = ImageEnhance.Contrast(ImageEnhance.Brightness(ImageEnhance.Color(sat).enhance(0.55)).enhance(0.88)).enhance(1.08)
jb = io.BytesIO(); sat.save(jb, "JPEG", quality=74, optimize=True, progressive=True)

Tq = np.where(np.isfinite(T), np.minimum(T, 65000), 65535).astype("<u2")
D = {
    "grid": [LON0, LON1, LAT0, LAT1, NX, NY],
    "dem": base64.b64encode(((dem - 900) * 16).clip(0, 65535).astype("<u2").tobytes()).decode(),
    "T": base64.b64encode(Tq.tobytes()).decode(),
    "origin": ORIGIN,
    "crew": {"lunch": pts([lunch]), "road": pts(road), "descent": pts(descent)},
    "sites": {k: scn[k]["geometry"]["coordinates"] for k in ("Deployment site", "Boulder Springs Ranch")},
    "yarnell": [-112.74709, 34.22219],
    "sat": base64.b64encode(jb.getvalue()).decode(),
    "perim": [pts(r) for r in per_rings],
}
html = ROOT / "animation/index.html"
s = html.read_text()
s = re.sub(r"(// DATA-BEGIN\n).*?(// DATA-END)", lambda m: m[1] + "const DATA=" + json.dumps(D, separators=(",", ":")) + ";\n" + m[2], s, flags=re.S)
html.write_text(s)
print("wrote", html, len(s) // 1024, "KB; origin", ORIGIN)
