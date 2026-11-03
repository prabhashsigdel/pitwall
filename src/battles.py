"""Undercut / overcut / offset judge. Rebuilds the `battles` table each run.

Usage:
    python src/battles.py

How a battle is found:
  * Car A makes a real pit stop (in-lap L). Tyre changes during a red flag
    are not stops.
  * At the end of lap L-1 (the lap before A's in-lap), every car B that crossed
    the line within BATTLE_GAP_S of A, on the same lap, is in a battle with A.
  * B's response = B's next real stop with in-lap >= L.

SIGN CONVENTION (used everywhere in this file):
  gap = (time A crossed the line) - (time B crossed the line), same lap.
        Positive = A is BEHIND B by that many seconds. Negative = A is ahead.
  gap_change = gap_before - gap_after = seconds A GAINED on B (positive is good for A).
  pit_loss_diff, expected_tyre_gain and unexplained also count in A's favour,
  so: gap_change = pit_loss_diff + expected_tyre_gain + unexplained.
"""

import sys

import duckdb
import numpy as np
import pandas as pd

import config
import db

SC_CODES = "4567"  # SC, red flag, VSC, VSC ending (see clean.py)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
LAPS_SQL = """
SELECT l.season, l.round, l.driver, l.team, l.lap_number, l.tyre_life, l.stint,
       l.pit_in_time, l.pit_out_time,
       -- When the car crossed the line at the END of this lap: the next lap's
       -- start, or start + lap time for its final lap.
       COALESCE(LEAD(l.lap_start_time) OVER w, l.lap_start_time + l.lap_time) AS end_time,
       COALESCE(regexp_matches(l.track_status, '[{codes}]'), false) AS is_sc,
       c.gap_ahead_s
FROM laps l
LEFT JOIN clean_laps c USING (season, round, driver, lap_number)
WINDOW w AS (PARTITION BY l.season, l.round, l.driver ORDER BY l.lap_number)
""".format(codes=SC_CODES)

# Real stops only: drop stint changes whose in-lap had a red flag ('5').
STOPS_SQL = """
SELECT p.season, p.round, p.driver, p.lap AS in_lap,
       p.stint_before, p.compound_before, p.compound_after, l.tyre_life AS tyre_age
FROM pit_stops p
JOIN laps l ON l.season = p.season AND l.round = p.round
           AND l.driver = p.driver AND l.lap_number = p.lap
WHERE NOT COALESCE(l.track_status LIKE '%5%', false)
ORDER BY p.season, p.round, p.lap
"""

# The opposite: tyre changes made DURING a red flag (free stops).
RED_STOPS_SQL = """
SELECT p.season, p.round, p.driver, p.lap AS in_lap
FROM pit_stops p
JOIN laps l ON l.season = p.season AND l.round = p.round
           AND l.driver = p.driver AND l.lap_number = p.lap
WHERE COALESCE(l.track_status LIKE '%5%', false)
"""

# Race control messages that could explain a slow pit stop.
PENALTY_SQL = """
SELECT season, round, lap, message FROM race_control
WHERE regexp_matches(message, 'PENALTY|UNSAFE|STOP AND GO|STOP/GO|DRIVE THROUGH|PIT LANE', 'i')
"""


class Race:
    """Lap-by-lap lookups for one race: value(column, driver, lap)."""

    def __init__(self, laps: pd.DataFrame, stops: pd.DataFrame, deg: pd.DataFrame,
                 effects: pd.Series | None, cliffs: set, baselines: dict,
                 red_stops: pd.DataFrame, penalties: pd.DataFrame):
        self.laps = laps.set_index(["driver", "lap_number"]).sort_index()
        self.stops = stops
        self.deg = deg.set_index("compound") if not deg.empty else deg
        self.effects = effects
        self.reliable = effects is not None and bool(effects["is_reliable"])
        self.cliffs = cliffs  # {(driver, stint)} with a possible cliff
        self.baselines = baselines  # driver -> baseline pace from deg.py's field model
        self.red_stops = red_stops  # tyre changes during a red flag
        self.penalties = penalties  # penalty-ish race control messages
        self.team = laps.groupby("driver")["team"].first().to_dict()
        self.last_lap = laps.groupby("driver")["lap_number"].max().to_dict()

    def value(self, col: str, driver: str, lap: int):
        try:
            v = self.laps.at[(driver, lap), col]
        except KeyError:
            return None
        return None if pd.isna(v) else v

    def gap(self, a: str, b: str, lap: int) -> float | None:
        """A's line crossing minus B's at the end of `lap` (positive = A behind)."""
        ta, tb = self.value("end_time", a, lap), self.value("end_time", b, lap)
        return None if ta is None or tb is None else float(ta - tb)

    def next_stop(self, driver: str, from_lap: int) -> pd.Series | None:
        s = self.stops[(self.stops["driver"] == driver) & (self.stops["in_lap"] >= from_lap)]
        return None if s.empty else s.iloc[0]

    def red_change_between(self, driver: str, from_lap: int, to_lap: int | None) -> bool:
        """Did `driver` get a red flag tyre change in [from_lap, to_lap)?"""
        s = self.red_stops[(self.red_stops["driver"] == driver) & (self.red_stops["in_lap"] >= from_lap)]
        if to_lap is not None:
            s = s[s["in_lap"] < to_lap]
        return not s.empty


# ---------------------------------------------------------------------------
# Context columns
# ---------------------------------------------------------------------------
def gap_trend(r: Race, a: str, b: str, ref_lap: int) -> tuple[str | None, float | None]:
    """Slope of |gap| over the TREND_LAPS laps ending at ref_lap.
    closing = cars getting closer, dropping = cars drifting apart."""
    laps = range(ref_lap - config.TREND_LAPS + 1, ref_lap + 1)
    pts = [(k, abs(g)) for k in laps if (g := r.gap(a, b, k)) is not None]
    if len(pts) < 3:
        return None, None
    slope = float(np.polyfit(*zip(*pts), 1)[0])
    if slope < -config.TREND_THRESHOLD:
        return "closing", slope
    if slope > config.TREND_THRESHOLD:
        return "dropping", slope
    return "stable", slope


def pit_loss(r: Race, driver: str, in_lap: int) -> float | None:
    """Time in the pit lane: pit_out_time (out-lap) minus pit_in_time (in-lap)."""
    t_in, t_out = r.value("pit_in_time", driver, in_lap), r.value("pit_out_time", driver, in_lap + 1)
    return None if t_in is None or t_out is None else float(t_out - t_in)


def pace(r: Race, compound: str, age) -> float | None:
    """Model lap time (relative to a medium on new tyres) = offset + deg * age."""
    offsets = {"MEDIUM": 0.0, "SOFT": r.effects["soft_vs_medium_s"],
               "HARD": r.effects["hard_vs_medium_s"]}
    if compound not in offsets or age is None or pd.isna(offsets[compound]):
        return None
    return offsets[compound] + r.deg.at[compound, "deg_per_lap"] * float(age)


def expected_tyre_gain(r: Race, a: str, a_comp: str, b: str, b_comp: str,
                       a_in: int, b_in: int) -> tuple[float | None, str | None]:
    """Time A should gain on the laps where A is on fresh tyres and B is still
    on old ones (A's out-lap up to B's in-lap), from the field deg model."""
    if r.effects is None or not r.effects["is_reliable"]:
        return None, "race unreliable"
    for c in {a_comp, b_comp}:
        if r.deg.empty or c not in r.deg.index or r.deg.at[c, "low_confidence"]:
            return None, f"no confident deg for {c}"
    total = 0.0
    for k in range(a_in + 1, b_in + 1):
        pb = pace(r, b_comp, r.value("tyre_life", b, k))
        pa = pace(r, a_comp, r.value("tyre_life", a, k))
        if pa is None or pb is None:
            return None, f"missing tyre age / offset on lap {k}"
        total += pb - pa  # positive = B slower than A = A gains
    return total, None


def any_sc(r: Race, drivers: tuple[str, str], first: int, last: int) -> bool:
    return any(r.value("is_sc", d, k) for d in drivers for k in range(first, last + 1))


def car_pace_gain(r: Race, a: str, b: str, first: int, last: int) -> tuple[float | None, str | None]:
    """(B's baseline - A's baseline) x green flag laps in [first, last].
    Positive = B's car is slower, so A gains. A lap is green if both cars
    completed it with no SC / VSC / red flag code."""
    missing = [d for d in (a, b) if d not in r.baselines]
    if missing:
        return None, f"no baseline for {', '.join(missing)}"
    green = sum(1 for k in range(first, last + 1)
                if all(r.value("end_time", d, k) is not None and not r.value("is_sc", d, k)
                       for d in (a, b)))
    return (r.baselines[b] - r.baselines[a]) * green, None


def stick_check(r: Race, a: str, b: str, judged: int, gap_after: float) -> tuple[float | None, bool | None]:
    """Gap STICK_LAPS laps after the judged lap, and whether the same car is
    still ahead. (None, None) if either car pits, retires or there's an SC/VSC/red."""
    end = judged + config.STICK_LAPS
    for k in range(judged + 1, end + 1):
        for d in (a, b):
            if r.value("pit_in_time", d, k) is not None or r.value("is_sc", d, k):
                return None, None
    g = r.gap(a, b, end)
    if g is None:  # someone didn't complete that lap: retired
        return None, None
    return g, (g < 0) == (gap_after < 0)


def penalty_note(r: Race, drivers: tuple[str, str], first: int, last: int) -> str:
    """Penalty-ish race control messages naming either driver, laps first..last.
    Messages name drivers like 'CAR 44 (HAM)'."""
    m = r.penalties[(r.penalties["lap"] >= first) & (r.penalties["lap"] <= last)]
    hits = [msg for msg in m["message"] if any(f"({d})" in msg for d in drivers)]
    return " | ".join(dict.fromkeys(hits))[:300] or "no penalty message found"


def as_int(v) -> int | None:
    return None if v is None or pd.isna(v) else int(v)


# ---------------------------------------------------------------------------
# Judging one battle
# ---------------------------------------------------------------------------
def judge_undercut(row: dict, r: Race, a: str, b: str, b_in: int) -> None:
    """Gap at the end of B's out-lap, both cars on fresh tyres."""
    judged = b_in + 1
    after = r.gap(a, b, judged)
    row["judged_lap"] = judged
    if after is None:
        row["dnf_in_window"] = True
        return
    before = row["gap_before"]
    row.update(gap_after=after, gap_change=before - after)
    if before > 0:
        row["result"] = "undercut_success" if after < 0 else "undercut_failed"
    else:
        row["result"] = "cover_success" if after < 0 else "overcut_success"

    row["gap_after_stick"], row["stuck"] = stick_check(r, a, b, judged, after)

    row["pit_loss_a"], row["pit_loss_b"] = pit_loss(r, a, row["a_in_lap"]), pit_loss(r, b, b_in)
    if row["pit_loss_a"] is not None and row["pit_loss_b"] is not None:
        row["pit_loss_diff"] = row["pit_loss_b"] - row["pit_loss_a"]  # positive = A quicker
        row["pit_loss_suspicious"] = abs(row["pit_loss_diff"]) > config.PIT_LOSS_SUSPICIOUS_S
        if row["pit_loss_suspicious"]:
            row["pit_note"] = penalty_note(r, (a, b), row["a_in_lap"] - 1, judged + 1)
    gain, note = expected_tyre_gain(r, a, row["a_compound_after"], b, row["b_compound_before"],
                                    row["a_in_lap"], b_in)
    row.update(expected_tyre_gain=gain, expected_gain_note=note)


def judge_offset(row: dict, r: Race, a: str, b: str, b_in: int) -> None:
    """B (the later stopper) vs A at the last lap both completed."""
    judged = min(r.last_lap[a], r.last_lap[b])
    after = r.gap(a, b, judged)
    row["judged_lap"] = judged
    row["dnf_in_window"] = judged < b_in + 1  # someone retired before B's stop played out
    if after is None:
        return
    before = row["gap_before"]
    row.update(gap_after=after, gap_change=before - after)
    row["later_stopper_gained"] = after > before  # A fell further behind B = B gained
    # "Passed" only makes sense if B started behind A.
    row["later_stopper_passed"] = (after > 0) if before < 0 else None
    row["result"] = ("later_passed" if row["later_stopper_passed"]
                     else "later_gained" if row["later_stopper_gained"] else "later_lost")


def build_row(r: Race, stop: pd.Series, b: str, gap_before: float) -> dict:
    a, a_in = stop["driver"], int(stop["in_lap"])
    b_stop = r.next_stop(b, a_in)
    b_in = None if b_stop is None else int(b_stop["in_lap"])
    laps_apart = None if b_in is None else b_in - a_in
    kind = ("no_response" if b_in is None
            else "undercut_window" if laps_apart <= config.UNDERCUT_MAX_LAPS else "offset")
    # B got new tyres for free in a red flag before its next real stop:
    # not a real strategic response, so it's not judged.
    if r.red_change_between(b, a_in, b_in):
        kind = "red_flag_response"
    b_stint = b_stop["stint_before"] if b_stop is not None else r.value("stint", b, a_in - 1)
    trend, slope = gap_trend(r, a, b, a_in - 1)
    window_end = (b_in if b_in is not None else a_in) + 1  # B's (or A's) out-lap
    a_ahead_next = r.value("gap_ahead_s", a, a_in + 2)  # lap after A's out-lap

    row = {
        "season": int(stop["season"]), "round": int(stop["round"]),
        "a_driver": a, "a_team": r.team.get(a), "b_driver": b, "b_team": r.team.get(b),
        "teammates": r.team.get(a) is not None and r.team.get(a) == r.team.get(b),
        "a_in_lap": a_in, "b_in_lap": b_in, "laps_apart": laps_apart,
        "battle_type": kind, "result": None, "judged_lap": None,
        "gap_before": gap_before, "gap_after": None, "gap_change": None,
        "gap_trend": trend, "gap_trend_slope": slope,
        "a_compound_before": stop["compound_before"], "a_compound_after": stop["compound_after"],
        "b_compound_before": b_stop["compound_before"] if b_stop is not None else r.value("compound", b, a_in - 1),
        "b_compound_after": b_stop["compound_after"] if b_stop is not None else None,
        "a_tyre_age_at_stop": as_int(stop["tyre_age"]),
        "b_tyre_age_at_stop": as_int(b_stop["tyre_age"]) if b_stop is not None else None,
        "race_reliable": r.reliable,
        "early_stop": a_in < config.MIN_STOP_LAP,
        "sc_vsc_in_window": any_sc(r, (a, b), a_in, window_end),
        "a_outlap_traffic": a_ahead_next is not None and a_ahead_next < config.TRAFFIC_GAP_S,
        "b_possible_cliff": (b, b_stint) in r.cliffs,
        "dnf_in_window": False,
        "pit_loss_a": None, "pit_loss_b": None, "pit_loss_diff": None,
        "expected_tyre_gain": None, "expected_gain_note": None,
        "car_pace_gain": None, "car_pace_note": None, "unexplained": None,
        "pit_loss_suspicious": None, "pit_note": None,
        "gap_after_stick": None, "stuck": None,
        "later_stopper_gained": None, "later_stopper_passed": None,
    }
    if kind == "undercut_window":
        judge_undercut(row, r, a, b, b_in)
    elif kind == "offset":
        judge_offset(row, r, a, b, b_in)

    # Car pace: window = A's in-lap to the judged lap (B's out-lap for undercuts).
    if row["judged_lap"] is not None:
        row["car_pace_gain"], row["car_pace_note"] = car_pace_gain(r, a, b, a_in, row["judged_lap"])
    parts = [row["gap_change"], row["pit_loss_diff"], row["expected_tyre_gain"], row["car_pace_gain"]]
    if kind == "undercut_window" and all(p is not None for p in parts):
        row["unexplained"] = parts[0] - parts[1] - parts[2] - parts[3]
    return row


def race_battles(r: Race) -> list[dict]:
    rows = []
    # (driver, in_lap) of stops already used as B's response in an earlier
    # battle with a given A. That stop won't start a new battle with that A.
    responses: set[tuple[str, int, str]] = set()
    for _, stop in r.stops.iterrows():
        a, a_in = stop["driver"], int(stop["in_lap"])
        ref = a_in - 1
        if ref < 1:
            continue
        for b in r.last_lap:
            if b == a or (a, a_in, b) in responses:
                continue
            gap = r.gap(a, b, ref)
            # Same lap crossing, close enough, and B not itself diving into the pits.
            if gap is None or abs(gap) > config.BATTLE_GAP_S or r.value("pit_in_time", b, ref) is not None:
                continue
            b_stop = r.next_stop(b, a_in)
            # Both pitting on the SAME lap shows up from both sides: keep only
            # the version where A is the car behind (the attacker).
            if b_stop is not None and int(b_stop["in_lap"]) == a_in and gap < 0:
                continue
            if b_stop is not None:
                responses.add((b, int(b_stop["in_lap"]), a))
            rows.append(build_row(r, stop, b, gap))
    return rows


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def build(con) -> pd.DataFrame:
    laps = con.execute(LAPS_SQL).df()
    laps = laps.merge(con.execute(
        "SELECT season, round, driver, lap_number, compound FROM laps").df(),
        on=["season", "round", "driver", "lap_number"])
    stops = con.execute(STOPS_SQL).df()
    deg = con.execute("SELECT * FROM tyre_deg").df()
    effects = con.execute("SELECT * FROM race_effects").df()
    cliffs = con.execute(
        "SELECT season, round, driver, stint FROM cliff_flags WHERE possible_cliff").df()
    baselines = con.execute("SELECT season, round, driver, baseline_s FROM driver_baselines").df()
    red_stops = con.execute(RED_STOPS_SQL).df()
    penalties = con.execute(PENALTY_SQL).df()

    def this_race(df, season, rnd):
        return df[(df["season"] == season) & (df["round"] == rnd)]

    rows = []
    for (season, rnd), race_laps in laps.groupby(["season", "round"]):
        eff = this_race(effects, season, rnd)
        c = this_race(cliffs, season, rnd)
        base = this_race(baselines, season, rnd)
        r = Race(race_laps, this_race(stops, season, rnd).reset_index(drop=True),
                 this_race(deg, season, rnd),
                 eff.iloc[0] if not eff.empty else None,
                 set(zip(c["driver"], c["stint"])),
                 dict(zip(base["driver"], base["baseline_s"])),
                 this_race(red_stops, season, rnd),
                 this_race(penalties, season, rnd))
        rows += race_battles(r)

    df = pd.DataFrame(rows)
    # Lap columns can be NULL (no response), keep them as integers anyway.
    for col in ("b_in_lap", "laps_apart", "judged_lap", "a_tyre_age_at_stop", "b_tyre_age_at_stop"):
        df[col] = df[col].astype("Int64")
    con.register("incoming", df)
    con.execute("CREATE OR REPLACE TABLE battles AS SELECT * FROM incoming")
    con.unregister("incoming")
    return df


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def flag(b: pd.DataFrame, name: str, default: bool) -> pd.Series:
    """A boolean column; a column missing from an older table counts as `default`."""
    if name not in b.columns:
        return pd.Series(default, index=b.index)
    return b[name].astype("boolean").fillna(default).astype(bool)


def counts(b: pd.DataFrame, exclude_sc: bool = True) -> pd.Series:
    """Battles that count in stats: judged, reliable race, not teammates, no
    early stop, no DNF, not a red flag response and (optionally) no SC/VSC/red."""
    m = (b["result"].notna() & flag(b, "race_reliable", True) & ~flag(b, "teammates", False)
         & ~flag(b, "early_stop", False) & ~flag(b, "dnf_in_window", False)
         & (b["battle_type"] != "red_flag_response"))
    if exclude_sc:
        m &= ~flag(b, "sc_vsc_in_window", False)
    return m


def attempts(b: pd.DataFrame) -> pd.DataFrame:
    """Clean undercut attempts: undercut window, A started behind."""
    return b[counts(b) & (b["battle_type"] == "undercut_window") & (b["gap_before"] > 0)]


def rate(k: int, n: int) -> str:
    return f"{k}/{n} ({k / n:.0%})" if n else "0/0"


def success_line(att: pd.DataFrame) -> str:
    ok = att["result"] == "undercut_success"
    line = f"success {rate(int(ok.sum()), len(att))}"
    if "stuck" in att.columns:
        stuck = ok & flag(att, "stuck", False)
        line += (f", success that stuck {rate(int(stuck.sum()), len(att))}"
                 f" (stick check impossible for {int(att['stuck'].isna().sum())})")
    return line


def print_summary(b: pd.DataFrame, prev: pd.DataFrame | None) -> None:
    print(f"Battles: {len(b)}\n")
    print(b.groupby(["battle_type", "result"], dropna=False).size().to_string(), "\n")

    att = attempts(b)
    print("Clean undercut attempts (A behind; reliable race, no SC/VSC/red, not teammates,\n"
          f"no stop before lap {config.MIN_STOP_LAP}, no DNF, no red flag response):")
    if prev is not None:
        print(f"  before: {success_line(attempts(prev))}")
    print(f"  now:    {success_line(att)}")

    print("\nWhat decides them (clean attempts, s in A's favour):   mean   mean|x|")
    for c in ["gap_change", "pit_loss_diff", "expected_tyre_gain", "car_pace_gain", "unexplained"]:
        v = att[c].dropna().astype(float)
        print(f"  {c:<20}{v.mean():+8.2f}{v.abs().mean():8.2f}   n={len(v)}")

    pl = att["pit_loss_diff"].dropna().astype(float).abs()
    print(f"\n|pit_loss_diff|, clean attempts: median {pl.median():.2f}s, "
          f"p90 {pl.quantile(0.9):.2f}s, max {pl.max():.2f}s")
    sus = b[flag(b, "pit_loss_suspicious", False)]
    found = sus["pit_note"].ne("no penalty message found").sum()
    print(f"Suspicious pit loss (>{config.PIT_LOSS_SUSPICIOUS_S}s): {len(sus)} battles, "
          f"penalty message found for {found}")

    print("\nBy gap trend:")
    for trend, g in att.groupby(att["gap_trend"].fillna("unknown")):
        print(f"  {trend:<9} {success_line(g)}")

    for exclude_sc in (True, False):
        off = b[counts(b, exclude_sc) & (b["battle_type"] == "offset")]
        passable = off[off["later_stopper_passed"].notna()]
        label = "without SC/VSC/red windows" if exclude_sc else "including SC/VSC/red windows"
        print(f"\nOffsets {label}: n={len(off)}. Later stopper gained "
              f"{rate(int(flag(off, 'later_stopper_gained', False).sum()), len(off))}, passed "
              f"{rate(int(flag(passable, 'later_stopper_passed', False).sum()), len(passable))}"
              f" (of those that started behind)")

    teams = att.groupby(att["a_team"].fillna("UNKNOWN"))["result"].agg(
        n="size", ok=lambda s: (s == "undercut_success").sum())
    ranked = teams[teams["n"] >= 5].assign(pct=lambda t: t["ok"] / t["n"]).sort_values("pct", ascending=False)
    print("\nUndercut success per team (A's team, >= 5 clean attempts):")
    for team, t in ranked.iterrows():
        print(f"  {team:<22}{rate(int(t['ok']), int(t['n']))}")
    few = teams[teams["n"] < 5]
    print("  not enough data: " + ", ".join(f"{t} ({int(n)})" for t, n in few["n"].items()))


def print_round(b: pd.DataFrame, rnd: int) -> None:
    season = b["season"].max()
    r = b[(b["season"] == season) & (b["round"] == rnd) & (b["battle_type"] == "undercut_window")]
    cols = ["a_driver", "b_driver", "a_in_lap", "b_in_lap", "result", "gap_before", "gap_after",
            "gap_after_stick", "stuck", "gap_change", "pit_loss_diff", "expected_tyre_gain",
            "car_pace_gain", "unexplained", "sc_vsc_in_window", "teammates"]
    print(f"\nUndercut window battles, {season} round {rnd} (gap > 0 = A behind B):")
    with pd.option_context("display.width", 250, "display.max_columns", None):
        print(r[cols].to_string(index=False, float_format=lambda x: f"{x:+.2f}"))


def read_previous(con) -> pd.DataFrame | None:
    try:
        return con.execute("SELECT * FROM battles").df()
    except duckdb.Error:  # first run
        return None


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    con = db.connect()
    prev = read_previous(con)  # for the before/after line
    battles = build(con)
    print_summary(battles, prev)
    print_round(battles, 11)
    con.close()


if __name__ == "__main__":
    main()
