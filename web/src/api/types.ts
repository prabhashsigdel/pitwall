// Response shapes of the FastAPI backend (mirrors api/schemas.py).
// Times are seconds, deg values seconds per lap. null = no value in the pipeline.

export type Compound = "SOFT" | "MEDIUM" | "HARD" | "INTERMEDIATE" | "WET" | "UNKNOWN";
export type Tone = "good" | "bad" | "neutral";

export interface Team {
  name: string;
  colour: string; // readable (>= 3:1) on the surface colour
}

export interface Meta {
  season: number;
  races_loaded: number;
  last_race: { round: number; event_name: string; date: string } | null;
}

export interface RaceSummary {
  round: number;
  event_name: string;
  circuit: string;
  date: string; // YYYY-MM-DD
  is_reliable: boolean | null;
  fuel_source: "estimated" | "fixed" | null;
  red_flag_laps: number[];
}

export interface RaceHeader {
  round: number;
  event_name: string;
  circuit: string;
  country: string | null;
  date: string;
  total_laps: number | null;
  winner: {
    code: string;
    team: Team;
    strategy: string;
    compound_sequence: string;
    stint_lengths: string;
    stops: number;
  } | null;
  fuel: {
    value: number;
    source: "estimated" | "fixed";
    ci_low: number | null;
    ci_high: number | null;
    own_estimate: number | null;
  } | null;
  r_squared: number | null;
  clean_laps: number | null;
  drivers_in_model: number;
  track_temp: { first_10: number | null; last_10: number | null };
  red_flag_laps: number[];
  is_reliable: boolean | null;
  unreliable_reason: string | null;
}

export interface Stint {
  compound: Compound;
  start_lap: number;
  end_lap: number;
  red_flag_change: boolean;
}

export interface DriverStrategy {
  position: number | null;
  code: string;
  team: Team;
  status: "finished" | "DNF";
  laps: number;
  stops: number;
  stints: Stint[];
}

export interface Strategies {
  round: number;
  total_laps: number;
  red_flag_laps: number[];
  drivers: DriverStrategy[];
}

export interface Line {
  x_start: number;
  y_start: number;
  x_end: number;
  y_end: number;
}

export interface CompoundDeg {
  compound: "SOFT" | "MEDIUM" | "HARD";
  ran: boolean;
  deg_per_lap: number | null;
  se: number | null;
  ci_low: number | null;
  ci_high: number | null;
  n_laps: number | null;
  n_drivers: number | null;
  low_confidence: boolean | null;
  line: Line | null;
  band_low: Line | null;
  band_high: Line | null;
}

export interface DegPoint {
  driver: string;
  compound: "SOFT" | "MEDIUM" | "HARD";
  tyre_age: number;
  value: number;
}

export interface Deg {
  round: number;
  compounds: CompoundDeg[];
  points: DegPoint[];
}

export interface Breakdown {
  pit_loss: number | null;
  tyre_gain: number | null;
  car_pace: number | null;
  unexplained: number | null;
  total: number | null;
}

export interface Battle {
  attacker: string;
  attacker_team: Team;
  defender: string;
  defender_team: Team;
  attacker_pit_lap: number;
  defender_pit_lap: number | null;
  type: string;
  type_code: string;
  result: string;
  result_code: string | null;
  tone: Tone;
  gap_before: number | null;
  gap_after: number | null;
  gap_before_text: string;
  gap_after_text: string;
  gained: number | null;
  stuck: boolean | null;
  gap_trend: string | null;
  sc_window: boolean;
  teammates: boolean;
  early_stop: boolean;
  outlier: boolean;
  outlier_reason: string | null;
  breakdown: Breakdown | null;
  notes: string[];
}

export interface Battles {
  round: number;
  total: number;
  battles: Battle[];
}

export interface TrafficLap {
  lap: number;
  ahead: string | null;
  gap_ahead_s: number | null;
  compound: string;
  tyre_age: number;
  actual: number;
  clean_air: number;
  lost: number;
}

export interface Traffic {
  round: number;
  driver: string;
  blocker: string | null;
  reliable: boolean;
  total_lost: number;
  uncertainty: number;
  per_lap: TrafficLap[];
  skipped: { lap: number; reason: string }[];
  actual_pos: number;
  new_pos: number;
  car_ahead: string | null;
  gap_to_car_ahead: number | null;
  traffic_gap_s: number;
  caveat: string;
}
