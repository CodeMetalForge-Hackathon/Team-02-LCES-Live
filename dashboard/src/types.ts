import type { MultiPolygon } from "geojson";

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
  routes?: {
    id: string; name?: string; to?: string; margin_min: number | null; status: RouteStatus; active: boolean;
    eta_min?: number; path?: [number, number][];
  }[];
  nodes?: { id: string; label?: string; lat: number | null; lon: number | null; status: string; age_s: number | null }[];
  fires?: { id?: string; lat: number; lon: number }[];
  crew?: { lat: number; lon: number };
  areas?: Record<"black" | "burning" | "front_5min" | "front_10min", MultiPolygon> | null;
  alerts?: string[];
  leave_by_ts?: number | null;
  weather?: { wind_ms?: number | null; wind_from?: number | null; temp_c?: number | null; rh?: number | null; notes?: string[] };
  placeholder_data?: boolean;
};

export type Envelope = { topic: string; rx_ts: number; data: SensorReading | EngineState };
