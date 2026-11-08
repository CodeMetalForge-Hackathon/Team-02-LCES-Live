"""Margin engine settings (docs/LCES_Design.md §5). Every tunable number lives here.

Values marked PLACEHOLDER are not decided yet (§9 open questions); change them here only.
"""
from pathlib import Path

DATA = Path(__file__).parent / "data" / "yarnell"

# ---- Input files ------------------------------------------------------------
# Person A owns the real nodes.json / routes.geojson. Until they exist, the
# engine uses the clearly-marked placeholders in data/yarnell/placeholder/.
NODES_FILE = DATA / "nodes.json"
ROUTES_FILE = DATA / "routes.geojson"
PLACEHOLDER_DIR = DATA / "placeholder"
GRAPH_FILE = DATA / "graph.npz"          # build_graph.py; when present, routes come from it
SCENARIO_FILE = DATA / "scenario.geojson"
RASTERS = {name: DATA / f"{name}.tif" for name in ("dem", "slope", "aspect", "fuel")}

# ---- Scenario ---------------------------------------------------------------
# "2013": replay with SH5 (old chaparral) everywhere (§3.1 fuel decision, §1.4 fallback).
# "today": LANDFIRE LF2025 fuel.tif as is.
SCENARIO = "2013"
SCENARIO_START = "16:20"     # scenario clock at Start ({"cmd": "start"}); shown as 16:20 + elapsed
REPLAY_FUEL = 145            # SH5
NON_BURNABLE = range(91, 100)  # FBFM40 91-99: no spread

# ---- Fire model (§2) --------------------------------------------------------
SAFETY_FACTOR = 1.5          # fire assumed this many times faster than modelled (§2.4)
SEPARATION_FLAME_MULT = 4.0  # unsafe within 4 x flame length of the front (Butler & Cohen 1998)
LIVE_HERB_MOISTURE = 0.30    # fraction. PLACEHOLDER: cured grass, June drought
LIVE_WOODY_MOISTURE = 0.60   # fraction. PLACEHOLDER: stressed chaparral
RFM_CORRECTION_PCT = 0       # Fosberg Table B correction; 0 = exposed, midday (most conservative)
BURNOUT_S = 300             # a cell burns this long after the fire reaches it, then it is the black (§2.4)
SLOPE_BAND_DEG = 5           # spread rates cached per (fuel, slope band, aspect band, direction) (§2.4)
ASPECT_BAND_DEG = 22.5
FIRE_MERGE_M = 30            # a new fire point within this distance of an existing one is the same fire
# Hot sensor node rule (§1.4): a reading >= HOT_TEMP_C, or a rise of more than HOT_RISE_C within HOT_RISE_S,
# puts a fire point at the node (hair dryer). DHT11 tops out near 50 C: tune on the day.
HOT_TEMP_C = 45.0
HOT_RISE_C = 8.0
HOT_RISE_S = 30.0
# A route the fire bends off its fire-blind path is announced ("re-planned around the fire") when it moves more
# than ROUTE_CHANGE_M from the last announced path, at most once per ROUTE_CHANGE_HOLD_S (it re-plans every step).
ROUTE_CHANGE_M = 50.0
ROUTE_CHANGE_HOLD_S = 20.0

# ---- Crew model (§4) --------------------------------------------------------
ROUTE_STEP_M = 25            # routes are densified to this spacing (§4.2: segmentize ~25 m)
TRAIL_FACTOR = 1.0
# Off-trail travel factor by FBFM40 fuel group (§4.3 fallback until EVC/EVH are on the grid)
OFFTRAIL_FACTOR_BY_FUEL = [  # (first code, last code, factor)
    (91, 99, 0.8),     # non-burnable / bare
    (101, 109, 0.8),   # grass
    (121, 124, 0.6),   # grass-shrub
    (141, 143, 0.4),   # SH1-SH3
    (144, 149, 0.15),  # SH4-SH9 (SH5 = old dense chaparral)
    (161, 165, 0.3),   # timber understory
    (181, 189, 0.6),   # timber litter
]
OFFTRAIL_FACTOR_DEFAULT = 0.5

# ---- Margin thresholds (§5.1) -----------------------------------------------
GREEN_MIN = 5.0              # margin >= 5 min -> green
RED_MIN = 2.0                # margin < 2 min -> red (+ reroute + alert)
DROP_AMBER_FRACTION = 0.30   # margin dropped > 30 % within DROP_WINDOW_S -> amber
DROP_WINDOW_S = 30
DROP_AMBER_BELOW_MIN = 10.0  # ... only while the margin is under this (140 -> 40 min is no warning)
LEAVE_BUFFER_MIN = 2.0       # leave-by = now + margin - buffer (§5 step 7)

# ---- Node health (§5.1) and worst case (§5.3) -------------------------------
HEARTBEAT_S = 10
AGING_S = 20                 # 2 missed heartbeats -> aging
STALE_S = 30                 # 3 missed heartbeats -> stale (grey)
# Per-node overrides (aging_s, stale_s). N1 (La Crosse wind via SDR) transmits only every ~30 s, so it gets
# about 2 intervals + jitter before it goes stale; one lost packet makes it aging, not stale.
NODE_HEALTH_S = {"N1": (40, 65)}


def health_s(node: str) -> tuple[float, float]:
    """(aging_s, stale_s) for a node."""
    return NODE_HEALTH_S.get(node, (AGING_S, STALE_S))
WORST_WIND_MS = 10.0         # PLACEHOLDER (§5.3 open question)
WORST_TEMP_C = 38.0          # PLACEHOLDER
WORST_RH = 8.0               # PLACEHOLDER

# ---- Wind smoothing (N1 readings jump around) ---------------------------------
WIND_SMOOTH_S = 20.0         # time constant of the moving average (wind direction averaged as a vector)
WIND_DIR_STEP_DEG = 5        # smoothed direction rounded to this, so tiny wobbles don't re-run the fire grid
WIND_SPEED_STEP_MS = 0.5     # smoothed speed rounded UP to this (conservative)

# ---- Simulated crew (escape-margin spec story 35) -----------------------------
CREW_WALK = True             # after Start the crew walks the active route at the modelled speed (graph mode),
                             # until a real crew/<id>/pos message arrives

# ---- Engine loop ------------------------------------------------------------
FIRE_GRID_UNIT_S = 0.25     # engine/state fire_grid: arrival times in steps of this (uint16: ~4.5 h of scenario)
FIRE_GRID_HORIZON_S = 900   # ... sent for cells the fire reaches within this many seconds (dashboard draws 0-10 min)
RECOMPUTE_S = 1.0            # recompute + publish engine/state this often
ALERT_HOLD_S = 30            # event alerts (e.g. "rerouted") stay in engine/state this long
STATE_TOPIC = "engine/state"
CMD_TOPIC = "engine/cmd"
