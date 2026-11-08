"""Margin engine checks (docs/LCES_Design.md §7). Asserts only.

Run:  uv run --group engine python test_margin.py      (or: uv run --group engine --with pytest pytest test_margin.py)

Uses the real Yarnell rasters and the placeholder nodes/routes in data/yarnell/placeholder/;
the graph checks use data/yarnell/graph.npz (build_graph.py) and the real nodes.json.
"""
import json
import math
import tempfile
from pathlib import Path

import config
import fire_model
from engine import MarginEngine, load_nodes
from fire_model import Spread, Weather, arrival_min, one_hour_moisture
import build_graph
import numpy as np
from margin import (FireGrid, FirePoint, GraphTerrain, Terrain, crew_start_xy, evaluate, graph_routes, load_graph,
                    load_routes, spreads_for, tobler_kmh, to_lonlat, v1_fire_min)

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
    [res] = evaluate([r1], v1_fire_min(sp, 0.0), r1.xy[0])
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


# ---- engine over the routing graph (§9 track 8b) -----------------------------------------
GRAPH = load_graph()


def test_graph_routes_use_graph_times_and_send_path():
    start = crew_start_xy()
    eng = MarginEngine(GraphTerrain(GRAPH), graph_routes(GRAPH, start), load_nodes(config.NODES_FILE),
                       graph=GRAPH)
    s = eng.compute(0)
    c = build_graph.cell(GRAPH, *start)
    for r, key in (("ranch", "ranch"), ("black", "black0")):
        t, path = build_graph.route(GRAPH, c, GRAPH[key])
        route = next(x for x in eng.routes if x.id == r)
        cum = np.concatenate([[0], np.cumsum(route.seg_s)])
        assert abs(cum[-1] - t) < 1e-6, (r, cum[-1], t)           # the engine walks the graph's times
        assert route.xy.shape[0] == len(path)
        sr = route_by_id(s, r)
        assert abs(sr["eta_min"] - t / 60) < 0.2 and len(sr["path"]) == len(path)
    assert route_by_id(s, "black")["eta_min"] < route_by_id(s, "ranch")["eta_min"]   # 18.8 vs 28.1 min
    lon, lat = to_lonlat(*start)
    assert abs(route_by_id(s, "ranch")["path"][0][0] - lon) < 1e-4
    # crew walks 300 m along the ranch route: routes are re-planned from there, ETA drops
    ranch = next(x for x in eng.routes if x.id == "ranch")
    i = int(np.searchsorted(np.cumsum(np.hypot(*np.diff(ranch.xy, axis=0).T)), 300))
    eng.on_crew(dict(zip(("lon", "lat"), to_lonlat(*ranch.xy[i]))), 1)
    s2 = eng.compute(1)
    assert route_by_id(s2, "ranch")["eta_min"] < route_by_id(s, "ranch")["eta_min"] - 3


def test_graph_terrain_keeps_lf2025_fuel_outside_the_perimeter():
    t = GraphTerrain(GRAPH)
    fuel = GRAPH["fuel"]
    burnable_other = (fuel != config.REPLAY_FUEL) & ~np.isin(fuel, list(config.NON_BURNABLE))
    r, c = np.argwhere(burnable_other)[0]
    x, y = GRAPH["x"][r * fuel.shape[1] + c], GRAPH["y"][r * fuel.shape[1] + c]
    assert t.fuel(x, y) == fuel[r, c] != config.REPLAY_FUEL    # no "SH5 everywhere" override


# ---- fire spread over the grid (§2.4, §7 "Fire spread", ADR 0002) ---------------------------
def flat_fire(wall_col=None, n=41):
    """Flat SH5 grid, 10 m cells, the black = the centre cell, scenario start t = 0."""
    fuel = np.full((n, n), config.REPLAY_FUEL)
    if wall_col is not None:
        fuel[:, wall_col] = 99
    g = dict(shape=np.array([n, n]), res=10.0, x0=0.0, ytop=n * 10.0, fuel=fuel,
             slope=np.zeros((n, n)), aspect=np.zeros((n, n)), black0=np.array([n // 2 * n + n // 2]))
    return FireGrid(g, 0.0), n


def test_fire_downwind_cells_reached_before_upwind():
    f, n = flat_fire()
    f.advance(0, Weather(5.0, 270, 32, 15))                   # wind from the west
    a = f.arrival.reshape(n, n)
    c = n // 2
    assert a[c, c + 15] < a[c + 15, c] < a[c, c - 15]          # downwind < flank < upwind
    assert np.isfinite(a).all()


def test_fire_wind_shift_turns_from_where_it_reached():
    f, n = flat_fire()
    f.advance(0, Weather(5.0, 270, 32, 15))
    t_shift = float(np.sort(f.arrival)[int(n * n * 0.05)])     # ~5 % of the grid burned under the west wind
    before = f.arrival.copy()
    f.advance(t_shift, Weather(5.0, 0, 32, 15))               # wind now from the north
    reached = before <= t_shift
    assert (f.arrival[reached] == before[reached]).all()      # history kept
    fresh, _ = flat_fire()
    fresh.advance(0, Weather(5.0, 0, 32, 15))                 # as if the north wind had always blown
    a, b, c = f.arrival.reshape(n, n), fresh.arrival.reshape(n, n), n // 2
    # south of the east tongue: reached from the tongue, earlier than the fresh fire gets there
    east = int(np.flatnonzero(reached.reshape(n, n)[c]).max())
    assert east > c + 2 and a[n - 1, east] < b[n - 1, east]


def test_fire_never_crosses_non_burnable_cells():
    f, n = flat_fire(wall_col=30)
    f.advance(0, Weather(13.0, 270, 40, 8))
    a = f.arrival.reshape(n, n)
    assert np.isinf(a[:, 30:]).all() and np.isfinite(a[:, :30]).all()


def test_fire_cell_burns_for_5_min_then_black_and_flame_buffer_is_unsafe_first():
    f, n = flat_fire()
    f.advance(0, Weather(5.0, 270, 32, 15))
    c = n // 2
    cell = c * n + c + 5
    t = f.arrival[cell]
    assert f.burning(t + 1)[cell] and not f.black(t + 1)[cell]
    assert f.black(t + config.BURNOUT_S + 1)[cell] and not f.burning(t + config.BURNOUT_S + 1)[cell]
    unsafe = f.unsafe_at(0)
    assert unsafe[cell] < t and np.isinf(unsafe[c * n + c])   # 4 x flame ahead of the front; the black is safe


def test_graph_engine_spreads_from_the_black_and_ignites_spot_fires():
    eng = MarginEngine(GraphTerrain(GRAPH), graph_routes(GRAPH, crew_start_xy()), load_nodes(config.NODES_FILE),
                       graph=GRAPH)
    feed(eng, 0, **CALM)
    eng.compute(0)
    reached0 = np.isfinite(eng.fire.arrival).sum()
    assert reached0 > len(GRAPH["black0"])                     # predicted spread beyond the black
    eng.on_cmd({"cmd": "fire", "node": "N2"}, 60)
    s = eng.compute(60)
    n2 = load_nodes(config.NODES_FILE)["N2"]
    assert eng.fire.arrival[build_graph.cell(GRAPH, n2["x"], n2["y"])] == 60
    assert eng.fire.burnable[np.isfinite(eng.fire.arrival)].all()          # non-burnable never burns
    json.dumps(s, allow_nan=False)
    assert s["areas"]["black"]["coordinates"] and s["areas"]["burning"]["coordinates"]   # map layers (§5.5)
    eng.on_cmd({"cmd": "reset"}, 61)
    eng.compute(61)
    assert eng.fire.arrival[build_graph.cell(GRAPH, n2["x"], n2["y"])] > 61   # reset: the black at start again


# ---- closures + reroute loop (§5 steps 3-7) ------------------------------------------------
def flat_graph(n=41):
    """Flat open SH5 grid, 10 m cells. Crew at the west edge, ranch at the east edge, black0 = NW corner (cold)."""
    elev, slope, veg, blk = build_graph._flat(n, n)
    g = build_graph.build(elev, slope, veg, blk, x0=0.0, ytop=n * 10.0)
    c = n // 2
    g.update(elev=elev, slope=slope, aspect=np.zeros((n, n)), veg=veg, fuel=np.full((n, n), config.REPLAY_FUEL),
             ranch=np.array([c * n + n - 1]), black0=np.array([0]), front0=np.array([], int))
    g["_m"] = build_graph.matrix(g)
    g["_ok"] = np.bincount(g["src"], minlength=len(g["x"])) > 0
    g["_cell"] = np.arange(n * n)
    crew = (25.0, n * 10.0 - c * 10 - 5)                     # centre of cell (c, 2)
    return g, n, crew


def test_closures_route_around_the_fire_with_a_safe_margin():
    g, n, crew = flat_graph()
    f = FireGrid(g, 0.0)
    f.advance(0, Weather(1.0, 0, 32, 15), fires=[(205.0, 205.0, 0.0)])   # spot fire mid-way, light north wind
    blind = next(r for r in graph_routes(g, crew) if r.id == "ranch")
    routed = next(r for r in graph_routes(g, crew, f, 0) if r.id == "ranch")
    fire_min = f.fire_min(0)
    m_blind, m_routed = evaluate([blind, routed], fire_min, crew)
    assert m_blind.margin_min < 0                             # straight through the fire
    assert m_routed.margin_min >= 0 and routed.seg_s.sum() > blind.seg_s.sum()   # detour, never into closed cells


def test_no_path_left_keeps_the_route_red_and_the_black_grows():
    g, n, crew = flat_graph()
    f = FireGrid(g, 0.0)
    f.advance(0, Weather(1.0, 0, 32, 15), fires=[(*crew, 0.0)])          # fire on the crew
    rs = graph_routes(g, crew, f, 0)
    assert {r.id for r in rs} == {"ranch", "black"}                       # both still shown ...
    assert all(r.margin_min < config.RED_MIN for r in evaluate(rs, f.fire_min(0), crew))   # ... and red
    late = float(f.arrival[np.isfinite(f.arrival)].max()) + config.BURNOUT_S + 60       # all burned out
    black = next(r for r in graph_routes(g, crew, f, late) if r.id == "black")
    assert black.seg_s.sum() < 60                             # the black is now right here, not the NW corner


def test_graph_2013_story_ssw_green_then_nne_outflow_no_safe_route():
    """Demo on the 2013 data (spec story): wind from the SSW -> both routes green; the outflow from the
    NNE drives the front north of the crew south onto them -> both red, "no safe route"."""
    eng = MarginEngine(GraphTerrain(GRAPH), graph_routes(GRAPH, crew_start_xy()), load_nodes(config.NODES_FILE),
                       graph=GRAPH)
    feed(eng, 0, wind_ms=2.5, wind_dir=200)
    s = eng.compute(0)
    assert all(r["status"] == "green" for r in s["routes"]), s["routes"]
    feed(eng, 60, wind_ms=7.5, wind_dir=20)
    s = eng.compute(60)
    assert all(r["status"] == "red" for r in s["routes"]), s["routes"]
    assert any(a.startswith("NO SAFE ROUTE") for a in s["alerts"])


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} checks passed (model: {fire_model.MODEL_NAME})")
