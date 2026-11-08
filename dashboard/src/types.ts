
export type SensorReading = {
  type: "wx";
  node: string;
  seq: number;
  temp_c: number;
  rh: number;
  wind_ms: number;
  wind_dir: number;
  flame: number;
  batt: number;
};

export type RouteStatus = "green" | "amber" | "red" | "grey";

// engine/state, docs/LCES_Design.md §5.5 / §5.6
export type EngineState = {
  type: "state";
  ts?: number;
  phase?: "ready" | "running";      // ready until Start (POST /api/cmd {"cmd":"start"})
  started_ts?: number | null;      // unix s of Start = scenario_start
  scenario_start?: string;         // "16:20"
  replan_ts?: number | null;       // unix s of the last reroute (or of Start)
  active_route?: string | null;
  start_route?: string | null;     // route the crew is on at Start (null = fastest safe = the black); {"cmd":"plan"}
  routes?: {
    id: string; name?: string; to?: string; margin_min: number | null; status: RouteStatus; active: boolean;
    eta_min?: number; path?: [number, number][];
    replanned_ts?: number | null;  // unix s the fire last bent this route somewhere new (engine announce_detours)
    // minutes from ts per path point; null = before the crew joins / fire never gets there
    t_crew_min?: (number | null)[]; t_fire_min?: (number | null)[];
  }[];
  nodes?: { id: string; label?: string; lat: number | null; lon: number | null; status: string; age_s: number | null }[];
  fires?: { id?: string; lat: number; lon: number }[];
  crew?: { lat: number; lon: number; walking?: boolean; arrived?: boolean };
  fire_grid?: FireGrid | null;
  alerts?: string[];
  leave_by_ts?: number | null;
  weather?: { wind_ms?: number | null; wind_from?: number | null; temp_c?: number | null; rh?: number | null; m1h_pct?: number; notes?: string[] };
  placeholder_data?: boolean;
};

export type Envelope = { topic: string; rx_ts: number; data: SensorReading | EngineState };

// engine/state fire_grid (margin.fire_grid_msg): base64(zlib(uint16 LE)), row 0 = north;
// 0 = the black at Start, k >= 1 = arrival at t0 + (k - 1) * unit_s, 65535 = not within the horizon
export type FireGrid = {
  cols: number; rows: number; t0: number; unit_s: number; burnout_s: number;
  corners: [[number, number], [number, number], [number, number], [number, number]]; data: string;
};
