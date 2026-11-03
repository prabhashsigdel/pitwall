"""Pydantic response (and request) models for every endpoint.

Times are seconds, deg values are seconds per lap. Optional fields are null when
the pipeline has no value for them (e.g. no breakdown for an offset battle).
"""

from pydantic import BaseModel, Field


class Team(BaseModel):
    name: str
    colour: str = Field(description="hex, readable (>= 3:1) on the #15151E card surface")


# --- /api/meta and /api/races -------------------------------------------------------
class LastRace(BaseModel):
    round: int
    event_name: str
    date: str


class Meta(BaseModel):
    season: int
    races_loaded: int
    last_race: LastRace | None


class RaceSummary(BaseModel):
    round: int
    event_name: str
    circuit: str
    date: str
    is_reliable: bool | None
    fuel_source: str | None = Field(description="estimated / fixed")
    red_flag_laps: list[int]


# --- /api/races/{round} ---------------------------------------------------------------
class Winner(BaseModel):
    code: str
    team: Team
    strategy: str = Field(description='e.g. "M27-S18-S26 · 1 stop"')
    compound_sequence: str
    stint_lengths: str
    stops: int


class Fuel(BaseModel):
    value: float = Field(description="fuel + track effect used by the model, s/lap")
    source: str = Field(description="estimated (own fit) / fixed (season fallback)")
    ci_low: float | None = Field(description="95% CI of the race's own estimate")
    ci_high: float | None
    own_estimate: float | None = Field(description="the race's own estimate when it fell back")


class TrackTemp(BaseModel):
    first_10: float | None
    last_10: float | None


class RaceHeader(BaseModel):
    round: int
    event_name: str
    circuit: str
    country: str | None
    date: str
    total_laps: int | None
    winner: Winner | None
    fuel: Fuel | None
    r_squared: float | None
    clean_laps: int | None
    drivers_in_model: int
    track_temp: TrackTemp
    red_flag_laps: list[int]
    is_reliable: bool | None
    unreliable_reason: str | None


# --- /api/races/{round}/strategies -------------------------------------------------
class Stint(BaseModel):
    compound: str
    start_lap: int
    end_lap: int
    red_flag_change: bool = Field(description="tyres fitted during a red flag (not a pit stop)")


class DriverStrategy(BaseModel):
    position: int | None = Field(description="official position; null for a DNF")
    code: str
    team: Team
    status: str = Field(description="finished / DNF")
    laps: int
    stops: int = Field(description="real pit stops (red flag tyre changes not counted)")
    stints: list[Stint]


class Strategies(BaseModel):
    round: int
    total_laps: int
    red_flag_laps: list[int]
    drivers: list[DriverStrategy]


# --- /api/races/{round}/deg -------------------------------------------------------
class Line(BaseModel):
    x_start: float
    y_start: float
    x_end: float
    y_end: float


class CompoundDeg(BaseModel):
    compound: str
    ran: bool = Field(description="anyone ran it in this race")
    deg_per_lap: float | None
    se: float | None
    ci_low: float | None
    ci_high: float | None
    n_laps: int | None
    n_drivers: int | None
    low_confidence: bool | None
    line: Line | None = Field(description="fitted line, same as the dashboard chart")
    band_low: Line | None = Field(description="line using the lower CI slope")
    band_high: Line | None = Field(description="line using the upper CI slope")


class DegPoint(BaseModel):
    driver: str
    compound: str
    tyre_age: int
    value: float = Field(description="lap time vs driver baseline, segment and fuel corrected (s)")


class Deg(BaseModel):
    round: int
    compounds: list[CompoundDeg]
    points: list[DegPoint]


# --- /api/races/{round}/battles -----------------------------------------------------
class Breakdown(BaseModel):
    pit_loss: float | None
    tyre_gain: float | None
    car_pace: float | None
    unexplained: float | None
    total: float | None = Field(description="sum of the four, only when all four exist")


class Battle(BaseModel):
    attacker: str
    attacker_team: Team
    defender: str
    defender_team: Team
    attacker_pit_lap: int
    defender_pit_lap: int | None
    type: str
    type_code: str
    result: str
    result_code: str | None
    tone: str = Field(description="good / bad / neutral, from the attacker's side")
    gap_before: float | None = Field(description="> 0 = attacker behind")
    gap_after: float | None
    gap_before_text: str
    gap_after_text: str
    gained: float | None = Field(description="seconds the attacker gained")
    stuck: bool | None = Field(description="result still held 3 laps later")
    gap_trend: str | None
    sc_window: bool
    teammates: bool
    early_stop: bool
    outlier: bool = Field(description="result driven by something other than strategy (see outlier_reason)")
    outlier_reason: str | None = Field(description='e.g. "gap changed 72.8s (over 30s); BOT retired"')
    breakdown: Breakdown | None
    notes: list[str]


class Battles(BaseModel):
    round: int
    total: int = Field(description="battles in the race before filters")
    battles: list[Battle]


# --- /api/races/{round}/traffic -----------------------------------------------------
class TrafficLap(BaseModel):
    lap: int
    ahead: str | None
    gap_ahead_s: float | None
    compound: str
    tyre_age: int
    actual: float
    clean_air: float
    lost: float


class SkippedLap(BaseModel):
    lap: int
    reason: str


class Traffic(BaseModel):
    round: int
    driver: str
    blocker: str | None
    reliable: bool
    total_lost: float
    uncertainty: float = Field(description="95% (±)")
    per_lap: list[TrafficLap]
    skipped: list[SkippedLap]
    actual_pos: int
    new_pos: int = Field(description="position on time alone")
    car_ahead: str | None
    gap_to_car_ahead: float | None
    traffic_gap_s: float = Field(description="'in traffic' = within this of the car ahead")
    caveat: str


# --- /api/sim/whatif ----------------------------------------------------------------
class StintIn(BaseModel):
    compound: str = Field(description="SOFT / MEDIUM / HARD (or S / M / H)")
    laps: int


class WhatIfRequest(BaseModel):
    round: int
    driver: str
    stints: list[StintIn]
    red_flag_change: bool = True
    red_flag_compound: str | None = None


class PlanStint(BaseModel):
    compound: str
    laps: int
    start_age: int
    red_flag_change: bool = Field(description="the stop at the end is a free red flag tyre change")


class PitLoss(BaseModel):
    green: float
    sc: float
    sc_source: str
    n_green_stops: int


class RedFlag(BaseModel):
    lap: int
    pre_delta: float
    post_delta: float
    slot_actual: int
    slot_sim: int


class OrderRow(BaseModel):
    position: int
    driver: str
    team: Team
    gap: str
    whatif: bool


class WhatIf(BaseModel):
    round: int
    driver: str
    reliable: bool
    sim_warning: str | None
    delta: float = Field(description="what-if minus real time; < 0 = faster")
    actual_time: float
    whatif_time: float
    actual_pos: int
    pred_pos: int
    laps: int
    real_plan: list[PlanStint]
    whatif_plan: list[PlanStint]
    pit_loss: PitLoss
    red: RedFlag | None
    notes: list[str]
    finishing_order: list[OrderRow]
