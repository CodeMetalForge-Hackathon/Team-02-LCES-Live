"""Margin engine checks (docs/LCES_Design.md §7). Asserts only.

Run:  uv run --group engine python test_margin.py      (or: uv run --group engine --with pytest pytest test_margin.py)

Uses the real Yarnell rasters and the placeholder nodes/routes in data/yarnell/placeholder/.
"""
import json
import math
import tempfile
from pathlib import Path

import config
import fire_model
from engine import MarginEngine, load_nodes
from fire_model import Spread, Weather, arrival_min, one_hour_moisture
from margin import FirePoint, Terrain, evaluate, load_routes, spreads_for, tobler_kmh

TERRAIN = Terrain()
NODES_FILE = config.PLACEHOLDER_DIR / "nodes.json"
ROUTES_FILE = config.PLACEHOLDER_DIR / "routes.geojson"
CALM = dict(wind_ms=2.5, wind_dir=270)      # simulator before the shift
SHIFT = dict(wind_ms=7.5, wind_dir=180)     # simulator after the shift


def routes():
    return load_routes(ROUTES_FILE, TERRAIN)


def engine():
    return MarginEngine(TERRAIN, routes(), load_nodes(NODES_FILE), placeholder=True)


def feed(eng, now, wind_ms, wind_dir, skip=()):
    readings = {"N1": dict(wind_ms=wind_ms, wind_dir=wind_dir, temp_c=32, rh=15),
                "N2": dict(wind_ms=-1, wind_dir=-1, temp_c=31, rh=16),
                "N3": dict(wind_ms=-1, wind_dir=-1, temp_c=30, rh=18)}
    for node, values in readings.items():
        if node not in skip:
            eng.on_wx({"type": "wx", "node": node, **values}, now)


def route_by_id(state, rid):
    return next(r for r in state["routes"] if r["id"] == rid)


# ---- fire model -------------------------------------------------------------------------
def test_moisture_table():
    assert one_hour_moisture(32, 15) < one_hour_moisture(32, 50) < one_hour_moisture(32, 95)
    assert one_hour_moisture(40, 10) <= one_hour_moisture(5, 10)
    assert one_hour_moisture(32, 0) == 0.01 and one_hour_moisture(32, 100) == 0.13


def test_downwind_fastest_and_omni_is_a_circle():
    sp = Spread(145, 0.0, 0.0, Weather(5.0, 225, 32, 15))      # wind FROM 225 -> head toward 45
    assert abs(sp.head_dir - 45) < 2
    assert sp.ros_m_min(45) > sp.ros_m_min(135) > sp.ros_m_min(225)
    omni = Spread(145, 0.0, 0.0, Weather(5.0, None, 32, 15))
    assert omni.ros_m_min(0) == omni.ros_m_min(123) == omni.ros_m_min(270) == omni.head_m_min


def test_non_burnable_never_arrives():
    sp = Spread(99, 0.0, 0.0, Weather(5.0, 225, 32, 15))
    assert not sp.burnable and sp.ros_m_min(45) == 0
    assert arrival_min(sp, (0, 0), (500, 500)) == math.inf


def test_arrival_shrinks_as_the_fire_ages_and_flame_buffer_applies():
    sp = Spread(145, 0.0, 0.0, Weather(5.0, 225, 32, 15))
    t_new, t_old = arrival_min(sp, (0, 0), (700, 700), 0), arrival_min(sp, (0, 0), (700, 700), 3)
    assert abs((t_new - t_old) - 3) < 1e-9
    assert arrival_min(sp, (0, 0), (0, config.SEPARATION_FLAME_MULT * sp.flame_m * 0.9)) == 0.0


def test_fallback_model_shape():
    saved = fire_model.HAVE_PYROTHERMEL
    fire_model.HAVE_PYROTHERMEL = False
    try:
        sp = Spread(145, 0.0, 0.0, Weather(5.0, 225, 32, 15))
        assert abs(sp.head_dir - 45) < 1e-9
        assert sp.ros_m_min(45) > sp.ros_m_min(135) > sp.ros_m_min(225) > 0
    finally:
        fire_model.HAVE_PYROTHERMEL = saved


# ---- crew model -------------------------------------------------------------------------
def test_tobler_uphill_slower_than_downhill():
    up, down = 100 / (tobler_kmh(+0.1) / 3.6), 100 / (tobler_kmh(-0.1) / 3.6)
    assert round(up) == 101 and round(down) == 71        # doc §4.2 sanity check


def test_route_reversed_costs_differ_with_slope():
    feature = json.loads(ROUTES_FILE.read_text())["features"][0]       # R1 drops ~100 m into the bowl
    rev = json.loads(json.dumps(feature))
    rev["geometry"]["coordinates"].reverse()
    rev["properties"]["id"] = "R1rev"
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "r.geojson"
        p.write_text(json.dumps({"type": "FeatureCollection", "features": [feature, rev]}))
        fwd, back = load_routes(p, TERRAIN)
    assert abs(fwd.seg_s.sum() - back.seg_s.sum()) > 60       # down != up, by more than a minute


# ---- margin (§7) ------------------------------------------------------------------------
def test_no_fire_all_green_shortest_route_active():
    eng = engine()
    feed(eng, 0, **CALM)
    s = eng.compute(0)
    assert all(r["status"] == "green" and r["margin_min"] is None for r in s["routes"])
    eta = {r["id"]: r["eta_min"] for r in s["routes"]}
    assert s["active_route"] == min(eta, key=eta.get) == "R1"
    assert s["leave_by_ts"] is None


def test_route_cut_midway_is_red_even_if_safety_zone_reachable():
    r1 = routes()[0]
    mid = len(r1.xy) // 2
    # Fire 150 m west of R1's middle, wind from the west: the head runs across the middle.
    fx, fy = r1.xy[mid][0] - 150, r1.xy[mid][1]
    sp = spreads_for([FirePoint("F", fx, fy, 0.0, "test")], Weather(5.0, 270, 32, 15), TERRAIN)
    [res] = evaluate([r1], sp, r1.xy[0], 0.0)
    assert res.margin_min < config.RED_MIN                    # red
    assert 0 < res.worst < len(r1.xy) - 1                     # the worst point is mid-route, not the SZ
    _, t_crew_s = r1.crew_seconds(r1.xy[0])
    sz_margin = arrival_min(sp[0][1], (fx, fy), r1.xy[-1]) - t_crew_s[-1] / 60
    assert sz_margin > res.margin_min                         # judged by the SZ alone it would look better


def test_wind_shift_turns_r1_red_and_reroutes_to_r2():
    eng = engine()
    feed(eng, 0, **CALM)
    eng.on_cmd({"cmd": "fire", "node": "N2"}, 0)
    s = eng.compute(0)
    assert route_by_id(s, "R1")["status"] == "green" and s["active_route"] == "R1"
    feed(eng, 10, **SHIFT)
    s = eng.compute(10)
    assert route_by_id(s, "R1")["status"] == "red"
    assert s["active_route"] == "R2" and route_by_id(s, "R2")["status"] != "red"
    assert any(a.startswith("REROUTED: R1 -> R2") for a in s["alerts"])
    assert s["leave_by_ts"] is not None and s["leave_by_ts"] > 10


def test_stale_node_goes_grey_and_shrinks_the_margin():
    eng = engine()
    feed(eng, 0, **CALM)
    eng.on_cmd({"cmd": "fire", "node": "N2"}, 0)
    fresh = route_by_id(eng.compute(0), "R2")["margin_min"]
    t = config.STALE_S + 1
    feed(eng, t, **CALM, skip=("N3",))                        # N3 goes silent
    s = eng.compute(t)
    n3 = next(n for n in s["nodes"] if n["id"] == "N3")
    assert n3["status"] == "stale"
    assert s["weather"]["wind_ms"] >= config.WORST_WIND_MS and s["weather"]["rh"] <= config.WORST_RH
    stale = route_by_id(s, "R2")["margin_min"]
    assert stale < fresh - (t / 60)                           # more than just the elapsed time
    assert any("Node N3 stale" in a for a in s["alerts"])
    # With the current worst-case numbers every route goes red: no reroute onto a red route.
    if all(r["status"] == "red" for r in s["routes"]):
        assert s["active_route"] == "R1"
        assert not any(a.startswith("REROUTED") for a in s["alerts"])
        assert any(a.startswith("NO SAFE ROUTE") for a in s["alerts"])


def test_reset_clears_fires():
    eng = engine()
    feed(eng, 0, **SHIFT)
    eng.on_cmd({"cmd": "fire", "node": "N2"}, 0)
    eng.compute(0)
    eng.on_cmd({"cmd": "reset"}, 1)
    s = eng.compute(1)
    assert not s["fires"] and all(r["status"] == "green" for r in s["routes"])


def test_flame_sensor_reading_creates_one_fire_at_the_node():
    eng = engine()
    for t in (0, 10, 20):
        eng.on_wx({"type": "wx", "node": "N2", "temp_c": 40, "rh": 10, "wind_ms": -1, "wind_dir": -1,
                   "flame": 1}, t)
    s = eng.compute(20)
    assert len(s["fires"]) == 1
    n2 = load_nodes(NODES_FILE)["N2"]
    assert abs(s["fires"][0]["lat"] - n2["lat"]) < 1e-5 and abs(s["fires"][0]["lon"] - n2["lon"]) < 1e-5


def test_state_is_json_serialisable():
    eng = engine()
    feed(eng, 0, **SHIFT)
    eng.on_fire({"type": "fire", "src": "THERM1", "lat": 34.2100, "lon": -112.7710, "conf": 0.9}, 0)
    json.dumps(eng.compute(5), allow_nan=False)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} checks passed (model: {fire_model.MODEL_NAME})")
