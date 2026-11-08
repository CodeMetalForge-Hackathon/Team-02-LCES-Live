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

export type EngineState = {
  type: "state";
  routes?: { id: string; name?: string; margin_min: number | null; status: "green" | "amber" | "red" | "grey"; active: boolean; eta_min?: number }[];
  nodes?: { id: string; status: string; age_s: number | null }[];
  fires?: { lat: number; lon: number }[];
  alerts?: string[];
  leave_by_ts?: number | null;
  weather?: { notes?: string[] };
  placeholder_data?: boolean;
};

export type Envelope = { topic: string; rx_ts: number; data: SensorReading | EngineState };
