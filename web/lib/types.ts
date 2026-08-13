/** API payload types — mirror api/queries_live.py + queries_history.py. */

export interface Position {
  icao24: string;
  callsign: string | null;
  lat: number;
  lon: number;
  alt_m: number | null;
  on_ground: boolean;
  vel_ms: number | null;
  track_deg: number | null;
  vrate_ms: number | null;
  category: number | null;
  age_s: number;
}

export interface Condition {
  station: string;
  obs_ts: string;
  temp_c: number | null;
  wind_speed_kmh: number | null;
  wind_gust_kmh: number | null;
  wind_dir_deg: number | null;
  visibility_m: number | null;
  ceiling_ft: number | null;
  flight_category: string | null;
  text_desc: string | null;
}

export interface AlertRow {
  alert_id: string;
  airport: string;
  event: string;
  severity: string | null;
  effective_ts: string;
  ends_ts: string | null;
}

export interface Snapshot {
  generated_at: string;
  positions: Position[];
  conditions: Condition[];
  alerts: AlertRow[];
  freshness_s: number | null;
}

export interface ArrivalPoint { airport: string; bucket_ts: string; arrivals: number }
export interface AirbornePoint { airport: string; bucket_ts: string; holding_min: number; go_arounds: number }
export interface EventRow {
  event_id: string; icao24: string; callsign: string | null;
  event_type: "arrival" | "holding" | "go_around"; airport: string;
  event_ts: string; duration_s: number | null;
  details: Record<string, unknown> | null;
}
export interface WindPoint { station: string; obs_ts: string; wind_speed_kmh: number | null; wind_gust_kmh: number | null }
export interface ScatterPoint { airport: string; hour_ts: string; arrivals_detected: number; effective_wind_kmh: number; flight_category: string | null }
export interface QualityDay {
  quality_date: string; airport: string; arrivals_detected: number; arrivals_gt: number;
  observed_hours: number; precision: number | null; recall: number | null;
}

export interface StaircaseCell { airport: string; worst_category: string; days: number; avg_delay_min: number }
export interface CauseYear { year: number; weather_khr: number | null; nas_khr: number | null }
export interface SeasonMonth { airport: string; month: number; wx_cancelled: number }
export interface WorstDay {
  flight_date: string; airport: string; worst_category: string | null;
  arrivals_scheduled: number | null; cancelled: number | null; cancelled_weather: number | null;
  wx_delay_hours: number | null; min_visibility_sm: number | null; min_ceiling_ft: number | null;
}
export interface MonthlyPoint { month: string; worst_category: string; avg_delay_min: number }

export interface Meta { generated_at: string; status: "fresh" | "stale" | "offline"; freshness_s: number; aircraft: number }

/** Where a payload came from — drives the honesty banner. */
export type DataSource = "api" | "blob";
export type LiveMode = "live" | "polling" | "frozen";
