"""Margin engine checks (docs/LCES_Design.md §7). Asserts only.

Run:  uv run --group engine python test_margin.py      (or: uv run --group engine --with pytest pytest test_margin.py)

Uses the real Yarnell rasters and the placeholder nodes/routes in data/yarnell/placeholder/;
the graph checks use data/yarnell/graph.npz (build_graph.py) and the real nodes.json.
"""
import base64
import json
import zlib
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
config.START_ROUTE = None                   # checks assume the fastest-route start; the ranch start has its own
CALM = dict(wind_ms=2.5, wind_dir=270)      # simulator before the shift
SHIFT = dict(wind_ms=7.5, wind_dir=180)     # simulator after the shift


def routes():
    return load_routes(ROUTES_FILE, TERRAIN)


def engine():
    eng = MarginEngine(TERRAIN, routes(), load_nodes(NODES_FILE), placeholder=True)
    eng.on_cmd({"cmd": "start"}, 0)
    return eng


def graph_engine(start_at=0.0):
    eng = MarginEngine(GraphTerrain(GRAPH), graph_routes(GRAPH, crew_start_xy()), load_nodes(config.NODES_FILE),
                       graph=GRAPH)
    if start_at is not None:
        eng.on_cmd({"cmd": "start"}, start_at)
    return eng


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
    for t in (10, 30, 50, 70):                                # wind is smoothed (WIND_SMOOTH_S): hold the shift
        feed(eng, t, **SHIFT)
    s = eng.compute(70)
    assert route_by_id(s, "R1")["status"] == "red"
    assert s["active_route"] == "R2" and route_by_id(s, "R2")["status"] != "red"
    assert any(a.startswith("REROUTED: R1 -> R2") for a in s["alerts"])
    assert s["leave_by_ts"] is not None and s["leave_by_ts"] > 70


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


def test_n1_gets_a_longer_stale_threshold():
    # N1 (SDR wind) sends every ~30 s: one missed packet = aging, not stale; other nodes keep STALE_S.
    eng = engine()
    feed(eng, 0, **CALM)
    h = eng.node_health(45)
    assert h["N1"][0] == "aging" and h["N2"][0] == "stale"
    assert eng.node_health(config.health_s("N1")[1])["N1"][0] == "stale"


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


def test_hot_sensor_creates_one_fire_at_the_node_and_is_left_out_of_the_weather():
    def temp(eng, node, t, c):
        eng.on_wx({"type": "wx", "node": node, "temp_c": c, "rh": 10, "wind_ms": -1, "wind_dir": -1, "flame": -1}, t)

    ready = graph_engine(start_at=None)
    temp(ready, "N2", 0, 60)
    assert not ready.fires                                      # nothing burns before Start
    eng = graph_engine()
    feed(eng, 0, 7.5, 200)                                      # N2 31 C, N3 30 C
    temp(eng, "N3", 20, 37)
    assert not eng.fires                                        # +7 C in 20 s: not hot
    temp(eng, "N2", 10, 40)                                     # +9 C in 10 s: hair dryer
    assert [f.source for f in eng.fires] == ["N2 hot sensor (40 C)"]
    temp(eng, "N3", 100, 44)
    temp(eng, "N3", 140, 46)                                    # >= 45 C (no rise in the last 30 s)
    temp(eng, "N2", 150, 55)                                    # N2 still hot: same fire, first time kept
    assert [(f.source, f.t0) for f in eng.fires] == [("N2 hot sensor (40 C)", 10), ("N3 hot sensor (46 C)", 140)]
    feed(eng, 150, 7.5, 200, skip=("N2", "N3"))
    assert eng.compute(150)["weather"]["temp_c"] == 32         # N1's reading; the hot nodes are left out
    eng.on_cmd({"cmd": "reset"}, 160)
    assert not eng.hot


def test_spot_fire_detour_announces_a_route_replan_once():
    eng = graph_engine()
    states = []
    for t in range(0, 121, 5):
        feed(eng, t, 7.5, 200)
        if t == 60:
            eng.on_cmd({"cmd": "fire", "node": "N2"}, t)
        states.append(eng.compute(t))
    assert all(r["replanned_ts"] is None for s in states[:12] for r in s["routes"])   # walking alone: no replan
    ranch = [route_by_id(s, "ranch")["replanned_ts"] for s in states[12:]]
    assert ranch[0] == 60, ranch                                # the fire at N2 bends the ranch route at once
    assert sum(1 for s in states for a in s["alerts"] if a.startswith("Route ranch re-planned")) >= 1
    times = sorted(set(ranch))
    assert all(b - a >= config.ROUTE_CHANGE_HOLD_S for a, b in zip(times, times[1:])), times   # no flicker


def test_fast_drop_is_amber_only_when_the_margin_is_small():
    eng = engine()
    assert eng._route_status("R1", 140, 0) == "green"
    assert eng._route_status("R1", 40, 5) == "green"            # -70 %, but 40 min left: no warning
    assert eng._route_status("R2", 9, 0) == "green"
    assert eng._route_status("R2", 6, 5) == "amber"             # -33 % with under 10 min left: closing fast


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
    eng = graph_engine()
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
    fg = s["fire_grid"]                                                    # map layer: arrival per cell
    q = np.frombuffer(zlib.decompress(base64.b64decode(fg["data"])), "<u2").reshape(fg["rows"], fg["cols"])
    assert (q == 0).sum() == eng.fire.black0.sum() and ((q > 0) & (q < 65535)).any() and len(fg["corners"]) == 4
    eng.on_cmd({"cmd": "reset"}, 61)
    eng.compute(61)
    assert eng.fire is None and not eng.fires                                # reset: back to ready
    eng.on_cmd({"cmd": "start"}, 62)
    eng.compute(62)
    assert eng.fire.arrival[build_graph.cell(GRAPH, n2["x"], n2["y"])] > 62   # the black at start again


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
    eng = graph_engine()
    feed(eng, 0, wind_ms=2.5, wind_dir=200)
    s = eng.compute(0)
    assert all(r["status"] == "green" for r in s["routes"]), s["routes"]
    feed(eng, 60, wind_ms=7.5, wind_dir=20)
    s = eng.compute(60)
    assert all(r["status"] == "red" for r in s["routes"]), s["routes"]
    assert any(a.startswith("NO SAFE ROUTE") for a in s["alerts"])



# ---- ready phase, Start, initial plan + reroute (docs/specs/demo-dashboard, tickets 01-03) -------
def test_ready_phase_no_fire_until_start_then_reset_back_to_ready():
    eng = graph_engine(start_at=None)
    feed(eng, 0, **CALM)
    s = eng.compute(0)
    assert s["phase"] == "ready" and s["started_ts"] is None and s["scenario_start"] == "16:20"
    assert s["fire_grid"] is None and eng.fire is None
    assert all(r["margin_min"] is None and r["status"] == "green" for r in s["routes"])
    assert all(all(v is None for v in r["t_fire_min"]) for r in s["routes"])
    assert s["active_route"] == "black" and s["replan_ts"] is None
    eng.on_cmd({"cmd": "fire", "node": "N2"}, 5)                # ignored before Start
    assert not eng.fires
    feed(eng, 100, **CALM)
    s = eng.compute(100)                                        # time passing in ready moves nothing
    assert s["fire_grid"] is None and s["phase"] == "ready"
    eng.on_cmd({"cmd": "start"}, 120)
    s = eng.compute(120)
    assert s["phase"] == "running" and s["started_ts"] == 120 and s["replan_ts"] == 120
    assert s["fire_grid"]["t0"] == 120
    assert all(isinstance(r["margin_min"], float) for r in s["routes"]), s["routes"]
    grid = eng.fire
    eng.on_cmd({"cmd": "start"}, 130)                           # second start ignored
    assert eng.started_ts == 120 and eng.fire is grid
    eng.on_cmd({"cmd": "reset"}, 140)
    s = eng.compute(140)
    assert s["phase"] == "ready" and s["fire_grid"] is None and s["started_ts"] is None
    assert all(r["margin_min"] is None for r in s["routes"]) and s["active_route"] == "black"
    json.dumps(s, allow_nan=False)


def test_graph_2013_story_starts_on_the_black_and_keeps_it():
    """At Start the crew takes the fastest route that isn't red (the black, 18.8 vs 28.1 min) and keeps it:
    the outflow from the WNW-NW (300 deg) cuts the ranch route, but the black stays green -> no reroute."""
    eng = graph_engine()
    feed(eng, 0, wind_ms=2.5, wind_dir=200)
    s = eng.compute(0)
    assert all(r["status"] == "green" for r in s["routes"]), s["routes"]
    assert s["active_route"] == "black" and s["replan_ts"] == 0
    for t in (60, 120, 180):
        feed(eng, t, wind_ms=7.5, wind_dir=300)
        s = eng.compute(t)
        assert s["active_route"] == "black" and route_by_id(s, "black")["status"] != "red", s["routes"]
    assert route_by_id(s, "ranch")["status"] == "red"
    assert not any(a.startswith("REROUTED") for a in s["alerts"]) and s["replan_ts"] == 0


def test_start_on_the_ranch_then_hot_n2_reroutes_to_the_black():
    """START_ROUTE = "ranch" (where the 2013 crew went): a hot N2 cuts it -> REROUTED ranch -> black."""
    config.START_ROUTE = "ranch"
    try:
        eng = graph_engine(start_at=None)
        feed(eng, 0, wind_ms=3, wind_dir=200)
        assert eng.compute(0)["active_route"] == "ranch"         # ready screen shows the ranch plan
        eng.on_cmd({"cmd": "start"}, 1)
        feed(eng, 1, wind_ms=3, wind_dir=200)
        s = eng.compute(1)
        assert s["active_route"] == "ranch" and s["replan_ts"] == 1
        eng.on_wx({"type": "wx", "node": "N2", "temp_c": 55, "rh": 10, "wind_ms": -1, "wind_dir": -1}, 30)
        feed(eng, 31, wind_ms=3, wind_dir=200)
        s = eng.compute(31)
        assert s["active_route"] == "black" and s["replan_ts"] == 31, s["routes"]
        assert any(a.startswith("REROUTED: ranch -> black") for a in s["alerts"]), s["alerts"]
        eng.on_cmd({"cmd": "reset"}, 40)
        assert eng.compute(40)["active_route"] == "ranch"
    finally:
        config.START_ROUTE = None


def test_plan_cmd_picks_the_start_route_only_before_start():
    eng = graph_engine(start_at=None)
    feed(eng, 0, wind_ms=3, wind_dir=200)
    assert eng.compute(0)["start_route"] is None and eng.compute(0)["active_route"] == "black"
    eng.on_cmd({"cmd": "plan", "route": "ranch"}, 1)
    s = eng.compute(1)
    assert s["start_route"] == "ranch" and s["active_route"] == "ranch"
    eng.on_cmd({"cmd": "plan", "route": "nowhere"}, 2)            # unknown: ignored
    eng.on_cmd({"cmd": "start"}, 3)
    assert eng.compute(3)["active_route"] == "ranch"
    eng.on_cmd({"cmd": "plan", "route": "black"}, 4)              # running: ignored
    assert eng.compute(4)["active_route"] == "ranch"
    eng.on_cmd({"cmd": "reset"}, 5)
    assert eng.compute(5)["start_route"] == "ranch"               # the operator's choice survives Reset


def test_graph_black_cut_reroutes_to_the_ranch():
    """Wind from the east (90 deg, 4 m/s) drives the fire onto the route back to the black -> REROUTED to the ranch."""
    eng = graph_engine()
    feed(eng, 0, wind_ms=2.5, wind_dir=200)
    assert eng.compute(0)["active_route"] == "black"
    feed(eng, 60, wind_ms=4.0, wind_dir=90)
    s = eng.compute(60)
    assert route_by_id(s, "black")["status"] == "red" and route_by_id(s, "ranch")["status"] == "green", s["routes"]
    assert any(a.startswith("REROUTED: black -> ranch") for a in s["alerts"])
    assert s["active_route"] == "ranch" and s["replan_ts"] == 60
    feed(eng, 120, wind_ms=2.5, wind_dir=200)                    # wind back: no flip back to the faster black
    assert eng.compute(120)["active_route"] == "ranch"


def test_per_point_times_align_with_path_and_give_the_margin():
    eng = graph_engine()
    feed(eng, 0, wind_ms=2.5, wind_dir=200)
    eng.compute(0)
    feed(eng, 60, wind_ms=7.5, wind_dir=300)
    s = eng.compute(60)
    for r in s["routes"]:
        tc, tf = r["t_crew_min"], r["t_fire_min"]
        assert len(tc) == len(tf) == len(r["path"]), r["id"]
        diffs = [f - c for c, f in zip(tc, tf) if c is not None and f is not None]
        if r["margin_min"] is None:
            assert not diffs
        else:
            assert abs(min(diffs) - r["margin_min"]) <= 0.15, (r["id"], min(diffs), r["margin_min"])
        assert tc[-1] is not None and abs(tc[-1] - r["eta_min"]) <= 0.1



# ---- wind smoothing + crew walk (Nov 8 dashboard round 2) ---------------------------------
def test_wind_direction_is_smoothed_as_a_vector():
    eng = graph_engine()
    for t, d in ((0, 350), (2, 10), (4, 350), (6, 10)):          # jumps across north
        eng.on_wx({"type": "wx", "node": "N1", "wind_ms": 4.0, "wind_dir": d, "temp_c": 30, "rh": 15}, t)
    w, d = eng.smoothed_wind("N1")
    assert d in (0, 355, 5) and w == 4.0, (w, d)                 # not 180
    for t in range(8, 200, 2):                                    # a steady new direction wins within a minute or so
        eng.on_wx({"type": "wx", "node": "N1", "wind_ms": 7.3, "wind_dir": 300, "temp_c": 30, "rh": 15}, t)
    assert eng.smoothed_wind("N1") == (7.5, 300)                  # speed rounded up to 0.5 m/s
    eng2 = graph_engine()
    eng2.on_wx({"type": "wx", "node": "N1", "wind_ms": 4.0, "wind_dir": 200, "temp_c": 30, "rh": 15}, 0)
    eng2.on_wx({"type": "wx", "node": "N1", "wind_ms": 4.0, "wind_dir": 300, "temp_c": 30, "rh": 15}, 2)
    assert 200 < eng2.smoothed_wind("N1")[1] < 230                # one jumpy reading only moves it a little


def test_crew_walks_the_active_route_after_start_and_stops_on_real_positions():
    eng = graph_engine(start_at=None)
    feed(eng, 0, wind_ms=2.5, wind_dir=200)
    s0 = eng.compute(0)
    eng.compute(30)
    assert eng.crew_xy == eng.start_xy                            # ready: nobody moves
    eng.on_cmd({"cmd": "start"}, 40)
    feed(eng, 40, wind_ms=2.5, wind_dir=200)
    s1 = eng.compute(40)
    feed(eng, 100, wind_ms=2.5, wind_dir=200)
    s2 = eng.compute(100)
    moved = math.hypot(eng.crew_xy[0] - eng.start_xy[0], eng.crew_xy[1] - eng.start_xy[1])
    assert 10 < moved < 150, moved
    eta1, eta2 = route_by_id(s1, "black")["eta_min"], route_by_id(s2, "black")["eta_min"]
    assert 0.5 < eta1 - eta2 < 1.6, (eta1, eta2)                  # ~1 min of walking done in 1 min
    assert s2["crew"]["walking"] and s0["crew"]["walking"] is False
    eng.on_crew(dict(zip(("lon", "lat"), to_lonlat(*eng.start_xy))), 101)   # real GPS: walk stops
    eng.compute(160)
    assert math.hypot(eng.crew_xy[0] - eng.start_xy[0], eng.crew_xy[1] - eng.start_xy[1]) < 0.01
    eng.on_cmd({"cmd": "reset"}, 170)
    assert not eng.crew_external


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} checks passed (model: {fire_model.MODEL_NAME})")
