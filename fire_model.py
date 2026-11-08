"""Fire spread for the margin engine (docs/LCES_Design.md §2).

Rate of spread comes from the Fire Lab's Behave engine via pyrothermel. If pyrothermel
isn't installed (it needs a source build on the Pi), a simple fallback (§2.7) is used,
and engine/state says so ("model": "fallback").
"""
import bisect
import math
from dataclasses import dataclass

import config

try:
    from pyrothermel import FuelModel, MoistureScenario, PyrothermelRun
    HAVE_PYROTHERMEL = True
except ImportError:  # pragma: no cover - depends on the machine
    HAVE_PYROTHERMEL = False

MODEL_NAME = "pyrothermel" if HAVE_PYROTHERMEL else "fallback"

# ---- 1-h dead fuel moisture: Fosberg / Rothermel 1983 Table A (daytime, 08:00-19:59) ----
# Rows: dry-bulb temp (F) 10-29, 30-49, 50-69, 70-89, 90-109, >109.
# Columns: RH 0-4, 5-9, ..., 95-99, 100 (%). Values = reference fuel moisture (%).
# Table B corrections (shading, aspect, time of day) -> config.RFM_CORRECTION_PCT.
# TO VERIFY against INT-GTR-143 / NWCG PMS 437 before quoting numbers in the pitch.
_RFM_TEMP_EDGES_F = [30, 50, 70, 90, 110]
_RFM_TABLE = [
    [1, 2, 2, 3, 4, 5, 5, 6, 7, 8, 8, 8, 9, 9, 10, 11, 12, 12, 13, 13, 14],
    [1, 2, 2, 3, 4, 5, 5, 6, 7, 7, 7, 8, 9, 9, 10, 10, 11, 12, 13, 13, 13],
    [1, 2, 2, 3, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 11, 12, 12, 12, 13],
    [1, 1, 2, 2, 3, 4, 5, 5, 6, 7, 7, 8, 8, 8, 9, 10, 10, 11, 12, 12, 13],
    [1, 1, 2, 2, 3, 4, 4, 5, 6, 7, 7, 8, 8, 8, 9, 10, 10, 11, 12, 12, 13],
    [1, 1, 2, 2, 3, 4, 4, 5, 6, 7, 7, 8, 8, 8, 9, 10, 10, 11, 12, 12, 12],
]


def one_hour_moisture(temp_c: float, rh: float) -> float:
    """1-h dead fuel moisture as a fraction, from air temperature and relative humidity."""
    temp_f = temp_c * 9 / 5 + 32
    row = bisect.bisect_right(_RFM_TEMP_EDGES_F, temp_f)
    col = min(max(int(rh // 5), 0), 20)
    return max(_RFM_TABLE[row][col] + config.RFM_CORRECTION_PCT, 1) / 100


@dataclass
class Weather:
    wind_ms: float              # midflame (sensors sit ~1-2 m up), m/s
    wind_from: float | None     # degrees, direction the wind blows FROM; None = unknown
    temp_c: float
    rh: float

    @property
    def m1h(self) -> float:
        return one_hour_moisture(self.temp_c, self.rh)


class Spread:
    """Spread from one fire point under one weather. ros_m_min(bearing) works in any direction."""

    def __init__(self, fuel_code: int, slope_deg: float, aspect_deg: float, wx: Weather):
        self.burnable = int(fuel_code) not in config.NON_BURNABLE
        self.omni = wx.wind_from is None
        wind_kmh = max(wx.wind_ms, 0.0) * 3.6
        # Unknown wind direction (§5.3): assume the worst, i.e. the wind pushes the fire
        # upslope, and use that head rate in every direction (a circle).
        wind_from = aspect_deg if self.omni else wx.wind_from
        self.head_m_min = 0.0
        self.head_dir = None
        self.flame_m = 0.0
        self._cache: dict[int, float] = {}
        if not self.burnable:
            return
        if HAVE_PYROTHERMEL:
            m1 = wx.m1h
            moisture = MoistureScenario(m1, m1 + 0.01, m1 + 0.02,
                                        config.LIVE_HERB_MOISTURE, config.LIVE_WOODY_MOISTURE)
            self._run = PyrothermelRun(FuelModel.from_existing(int(fuel_code)), moisture, wind_kmh,
                                       wind_input_mode="direct_midflame", wind_direction=wind_from,
                                       slope=max(float(slope_deg), 0.0), aspect=float(aspect_deg))
            head = self._run.run_surface_fire_in_direction_of_max_spread()
            self.head_m_min = head["spread_rate"] * 1000 / 60
            self.head_dir = head["direction"]
            self.flame_m = head["flame_length"]
        else:
            # §2.7 fallback: head ROS ~ 10 % of the 10-m open wind (Cruz & Alexander 2019).
            # Sensors give midflame wind; 10-m wind ~ midflame / 0.4 in shrubs.
            wind10_kmh = wind_kmh / 0.4
            self.head_m_min = max(0.10 * wind10_kmh, 0.1) * 1000 / 60
            self.head_dir = (wind_from + 180) % 360
            u_mph = wind_kmh / 1.609
            lb = 0.936 * math.exp(0.2566 * u_mph) + 0.461 * math.exp(-0.1548 * u_mph) - 0.397
            lb = min(max(lb, 1.0), 8.0)                       # Anderson 1983, capped at 8
            self._ecc = math.sqrt(1 - 1 / lb ** 2)
            self.flame_m = float("nan")                       # not modelled by the fallback

    def ros_m_min(self, bearing_deg: float) -> float:
        if not self.burnable:
            return 0.0
        if self.omni:
            return self.head_m_min
        if HAVE_PYROTHERMEL:
            key = round(bearing_deg) % 360          # 1-degree cache: Behave re-runs on every call
            if key not in self._cache:
                r = self._run.run_surface_fire_in_direction_of_interest(float(key))
                self._cache[key] = r["spread_rate"] * 1000 / 60
            return self._cache[key]
        theta = math.radians(bearing_deg - self.head_dir)
        return self.head_m_min * (1 - self._ecc) / (1 - self._ecc * math.cos(theta))


def arrival_min(spread: Spread, fire_xy, pt_xy, elapsed_min: float = 0.0) -> float:
    """Minutes from NOW until the fire, burning since elapsed_min ago, makes pt unsafe.

    Unsafe = within SEPARATION_FLAME_MULT x flame length of the front. Uses the safety
    factor (fire assumed SAFETY_FACTOR times faster). 0 = already unsafe; inf = never.
    Coordinates in metres (EPSG:32612), x = east, y = north.
    """
    dx, dy = pt_xy[0] - fire_xy[0], pt_xy[1] - fire_xy[1]
    flame = spread.flame_m if math.isfinite(spread.flame_m) else 0.0
    dist = max(math.hypot(dx, dy) - config.SEPARATION_FLAME_MULT * flame, 0.0)
    if dist == 0.0:
        return 0.0
    ros = spread.ros_m_min(math.degrees(math.atan2(dx, dy)) % 360)
    if ros <= 0:
        return math.inf
    return max(dist / (ros * config.SAFETY_FACTOR) - elapsed_min, 0.0)
