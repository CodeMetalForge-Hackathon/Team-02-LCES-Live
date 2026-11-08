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
LEAVE_BUFFER_MIN = 2.0       # leave-by = now + margin - buffer (§5 step 7)

# ---- Node health (§5.1) and worst case (§5.3) -------------------------------
HEARTBEAT_S = 10
AGING_S = 20                 # 2 missed heartbeats -> aging
STALE_S = 30                 # 3 missed heartbeats -> stale (grey)
WORST_WIND_MS = 10.0         # PLACEHOLDER (§5.3 open question)
WORST_TEMP_C = 38.0          # PLACEHOLDER
WORST_RH = 8.0               # PLACEHOLDER

# ---- Engine loop ------------------------------------------------------------
RECOMPUTE_S = 1.0            # recompute + publish engine/state this often
ALERT_HOLD_S = 30            # event alerts (e.g. "rerouted") stay in engine/state this long
STATE_TOPIC = "engine/state"
CMD_TOPIC = "engine/cmd"
