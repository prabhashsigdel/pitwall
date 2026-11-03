"""Strategy simulator v1: replay real races from the model, and what-if strategies.

Usage:
    python src/sim.py replay
    python src/sim.py whatif --round 11 --driver HAM --stints "M:20,H:50"

Lap model (the per race field model from deg.py, re-fitted here with deg.py's
own functions so the parameters are identical, incl. fuel fallback refits):

    lap_time = driver baseline + segment offset + compound offset
             + deg[compound] * tyre age + fuel_track * lap number

No traffic, no overtaking, no SC randomness: every car runs on its own.
"""

import argparse
import sys

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

import config
import db
import deg

DRY = {"SOFT", "MEDIUM", "HARD"}
LETTERS = {"S": "SOFT", "M": "MEDIUM", "H": "HARD"}
SEASON = 2026
CLIFF_STINT_LAPS, CLIFF_LAST = 15, 5

# Every lap with what the sim needs. lap_time NULLs are filled from the next
# lap's start (same thing, different source). segment = red flags so far,
# exactly like deg.load_clean_laps.
LAPS_SQL = """
WITH base AS (
    SELECT season, round, driver, lap_number, compound, tyre_life, stint, lap_start_time,
           COALESCE(lap_time, LEAD(lap_start_time) OVER w - lap_start_time) AS lap_time,
           pit_in_time IS NOT NULL  AS is_in,
           pit_out_time IS NOT NULL AS is_out,
           COALESCE(regexp_matches(track_status, '[4567]'), false) AS neutral,
           COALESCE(track_status LIKE '%5%', false) AS red
    FROM laps
    WINDOW w AS (PARTITION BY season, round, driver ORDER BY lap_number)
),
marked AS (
    SELECT *, red AND NOT COALESCE(LAG(red) OVER w, false) AS red_start,
           -- The restart lap after a red flag is green but includes the restart
           -- procedure (grid, standing start), so it always uses the real time.
           COALESCE(LAG(red) OVER w, false) AND NOT red AS after_red
    FROM base WINDOW w AS (PARTITION BY season, round, driver ORDER BY lap_number)
),
-- Laps where the WHOLE FIELD was slow (field median > RACE_OUTLIER_PCT slower than
-- the race median) count as neutralised even if track status says green, e.g.
-- restart laps behind the SC after a red flag (Monza: 198s / 168s laps marked '1').
lap_med AS (
    -- pit in/out laps left out: a lap where many cars pit isn't a slow lap
    SELECT season, round, lap_number, median(lap_time) AS lap_median
    FROM base WHERE NOT is_in AND NOT is_out GROUP BY ALL
),
slow AS (
    SELECT l.season, l.round, l.lap_number,
           l.lap_median > (1 + {slow_pct}) * median(b.lap_time) AS field_slow
    FROM lap_med l JOIN base b USING (season, round)
    GROUP BY l.season, l.round, l.lap_number, l.lap_median
)
SELECT m.* REPLACE (m.neutral OR COALESCE(s.field_slow, false) AS neutral),
       (SUM(red_start::INT) OVER w)::INT AS segment, c.is_clean
FROM marked m
LEFT JOIN slow s USING (season, round, lap_number)
LEFT JOIN clean_laps c USING (season, round, driver, lap_number)
WHERE m.season = ?
WINDOW w AS (PARTITION BY m.season, m.round, m.driver ORDER BY m.lap_number)
""".replace("{slow_pct}", str(float(config.RACE_OUTLIER_PCT)))


# ---------------------------------------------------------------------------
# The lap model
# ---------------------------------------------------------------------------
class RaceModel:
    """Predicts a lap from the race's final field fit (deg.field_rows)."""

    def __init__(self, res, fixed_fuel: float | None, reliable: bool):
        self.p = res.params
        # Estimated: the lap_number coefficient. Fixed: the fit added
        # fixed * lap_number to lap_time, so the lap time effect is -fixed.
        self.fuel = self.p["lap_number"] if fixed_fuel is None else -fixed_fuel
        self.fuel_source = "estimated" if fixed_fuel is None else "fixed"
        self.reliable = reliable
        # Typical size of one clean lap's deviation from the model (s): residual std.
        self.sigma = float(np.sqrt(res.mse_resid))

    def lap(self, driver: str, compound: str, age, lap: int, segment: int) -> float | None:
        base, slope = self.p.get(f"driver_{driver}"), self.p.get(f"slope:{compound}")
        if base is None or slope is None or age is None or pd.isna(age):
            return None
        return (base + self.p.get(f"segment:{segment}", 0.0) + self.p.get(f"offset:{compound}", 0.0)
                + slope * float(age) + self.fuel * lap)


def load_models(con, season: int) -> dict[int, RaceModel]:
    """Re-fit every race of the season exactly like deg.py does."""
    laps = deg.load_clean_laps(con)
    laps = laps[laps["season"] == season]
    wet = deg.wet_share(con)
    fallback = deg.season_fuel_fallback(laps, wet)[season][0]
    models = {}
    for rnd, race in laps.groupby("round"):
        effects, _, fixed, res = deg.field_rows(season, rnd, race, wet.get((season, rnd), 0.0), fallback)
        if res is not None:
            models[int(rnd)] = RaceModel(res, fixed, effects["is_reliable"])
    return models


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
def replay_driver(m: RaceModel, d: pd.DataFrame) -> tuple[float | None, list[tuple]]:
    """Sim time for one driver. Real time for lap 1, neutralised laps and the
    in/out laps of real stops (= model + that driver's actual pit_loss).
    Returns (total, [(stint, lap, residual actual - model, is_clean)] for the cliff check,
    {lap: sim - actual} for the laps the model predicted)."""
    total, residuals, diffs = 0.0, [], {}
    for lap in d.itertuples():
        use_real = lap.lap_number == 1 or lap.neutral or lap.after_red or lap.is_in or lap.is_out
        pred = None if use_real or lap.compound not in DRY else m.lap(
            lap.driver, lap.compound, lap.tyre_life, lap.lap_number, lap.segment)
        if pred is not None:
            total += pred
            if pd.notna(lap.lap_time):
                residuals.append((lap.stint, lap.lap_number, lap.lap_time - pred, bool(lap.is_clean)))
                diffs[int(lap.lap_number)] = pred - lap.lap_time
        elif pd.notna(lap.lap_time):
            total += lap.lap_time
        else:
            return None, [], {}  # needed the real time and it's missing
    return total, residuals, diffs


# ---------------------------------------------------------------------------
# Red flags reset the gaps
# ---------------------------------------------------------------------------
def red_flag_split(d: pd.DataFrame, diffs: dict) -> dict | None:
    """One driver at the LAST red flag of the race, or None if they weren't running.

    laps_before / crossing: laps completed and when they last crossed the line
    before the suspension (session clock: this gives the real order).
    pre_shift:   simulated change (s) before the suspension -> only moves the restart order.
    post_shift:  simulated change after the restart -> counts toward the finish.
    post_actual: real time driven after the restart (from the lap after the restart lap)."""
    d = d.sort_values("lap_number")
    starts = d[d["red_start"].astype(bool)]
    if starts.empty:
        return None
    red_lap = int(starts["lap_number"].iloc[-1])
    last_red = int(d.loc[d["red"].astype(bool), "lap_number"].max())
    post = d[(d["lap_number"] > last_red) & ~d["after_red"].astype(bool)]
    if post["lap_time"].isna().any():
        return None
    return {"red_lap": red_lap, "laps_before": red_lap - 1,
            "crossing": float(starts["lap_start_time"].iloc[-1]),
            "pre_shift": float(sum(v for k, v in diffs.items() if k < red_lap)),
            "post_shift": float(sum(diffs.get(int(k), 0.0) for k in post["lap_number"])),
            "post_actual": float(post["lap_time"].sum())}


def red_flag_finish(df: pd.DataFrame) -> pd.DataFrame:
    """Finish times when a red flag reset the gaps.

    df (index = driver): laps_before, crossing, pre_shift, post_actual, post_shift, official.
    The restart order is the order at the suspension (more laps first, then who crossed
    the line first), using the SIMULATED crossing (real + pre_shift): time gained before
    the stoppage can change the order, but the gaps reset. Each restart slot keeps the
    starting point its real occupant had (official finish - time driven after the
    restart); whoever the sim puts in that slot adds their own post-restart driving.
    If nobody changes slot: finish = official + post_shift."""
    real_order = df.sort_values(["laps_before", "crossing"], ascending=[False, True]).index
    sim_order = (df.assign(c=df["crossing"] + df["pre_shift"])
                 .sort_values(["laps_before", "c"], ascending=[False, True]).index)
    slot_start = (df.loc[real_order, "official"] - df.loc[real_order, "post_actual"]).to_numpy()
    out = pd.DataFrame(index=df.index, columns=["slot_actual", "slot_sim", "finish"], dtype=float)
    out.loc[real_order, "slot_actual"] = np.arange(1, len(df) + 1)
    out.loc[sim_order, "slot_sim"] = np.arange(1, len(df) + 1)
    out.loc[sim_order, "finish"] = slot_start + (df.loc[sim_order, "post_actual"]
                                                 + df.loc[sim_order, "post_shift"]).to_numpy()
    return out


def replay_race(m: RaceModel, laps: pd.DataFrame, finishers: pd.DataFrame, rnd: int, fin: pd.DataFrame):
    """error_s = sim minus the sum of the same real laps (a fair like-for-like error).
    sim_time = official finishing time + that error, so the sim order isn't thrown off
    by lap sums that don't add up across a red flag."""
    rows, residuals = [], []
    for f in finishers.itertuples():
        d = laps[laps["driver"] == f.driver].sort_values("lap_number")
        sim, res, diffs = replay_driver(m, d)
        actual = d["lap_time"].sum(min_count=len(d)) if d["lap_time"].notna().all() else None
        if (sim is None or actual is None or f.driver not in fin.index
                or not np.isfinite(fin.loc[f.driver, "time"])):
            continue
        official, error = float(fin.loc[f.driver, "time"]), sim - float(actual)
        rows.append({"season": SEASON, "round": rnd, "driver": f.driver, "laps": int(fin.loc[f.driver, "laps"]),
                     "actual_time": official, "sim_time": official + error, "error_s": error,
                     "actual_pos": int(fin.loc[f.driver, "position"]),
                     "red": red_flag_split(d, diffs)})
        residuals += [(f.driver,) + r for r in res]
    df = pd.DataFrame(rows)
    if df.empty:
        return df, residuals
    # Red flag race: only the order at the suspension carries over, not the gaps.
    has_red = df["red"].notna()
    if has_red.sum() >= 2:
        info = pd.DataFrame(list(df.loc[has_red, "red"]), index=df.loc[has_red, "driver"])
        info["official"] = df.loc[has_red, "actual_time"].to_numpy()
        out = red_flag_finish(info)
        df.loc[has_red, "sim_time"] = out.loc[df.loc[has_red, "driver"], "finish"].to_numpy()
    df = df.drop(columns="red")
    # Order = more laps first, then less time (lapped cars stay behind).
    df = df.sort_values(["laps", "sim_time"], ascending=[False, True]).reset_index(drop=True)
    df["sim_pos"] = df.index + 1
    return df, residuals


def cliff_check(residuals: pd.DataFrame) -> None:
    """Clean laps only (no traffic / outliers): model error (actual - model) on
    the last 5 laps of 15+ lap stints vs the rest of the same stints."""
    r = residuals[residuals["is_clean"]]
    out = []
    for _, s in r.groupby(["round", "driver", "stint"]):
        if s["lap"].max() - s["lap"].min() + 1 < CLIFF_STINT_LAPS or len(s) < CLIFF_LAST + 3:
            continue
        s = s.sort_values("lap")
        out.append((s["resid"].iloc[-CLIFF_LAST:].mean(), s["resid"].iloc[:-CLIFF_LAST].mean()))
    if not out:
        print("\nCliff check: no long stints with enough clean laps.")
        return
    last, rest = np.array(out).T
    diff = last - rest
    t = stats.ttest_1samp(diff, 0.0)
    print(f"\nCliff check ({len(out)} stints of {CLIFF_STINT_LAPS}+ laps, clean laps, actual - model):")
    print(f"  last {CLIFF_LAST} laps {last.mean():+.3f}s/lap, rest {rest.mean():+.3f}s/lap, "
          f"difference {diff.mean():+.3f}s/lap (p={t.pvalue:.3f}); "
          f"end of stint worse in {(diff > 0).mean():.0%} of stints")


def run_replay(con) -> None:
    models = load_models(con, SEASON)
    laps = con.execute(LAPS_SQL, [SEASON]).df()
    finished = con.execute(
        "SELECT round, driver, finish_position FROM strategies WHERE season = ? AND finished",
        [SEASON]).df()
    names = dict(con.execute("SELECT round, event_name FROM races WHERE season = ?", [SEASON]).fetchall())

    all_rows, all_res, summary = [], [], []
    for rnd, m in sorted(models.items()):
        if not m.reliable:
            continue  # unreliable races are skipped entirely
        race_laps = laps[laps["round"] == rnd]
        df, res = replay_race(m, race_laps, finished[finished["round"] == rnd], rnd,
                              finish_times(con, SEASON, rnd, race_laps))
        if df.empty:
            continue
        # Positions within the simulated set, so a skipped driver doesn't shift everyone.
        actual_rank = df["actual_pos"].rank()
        rho = stats.spearmanr(df["actual_pos"], df["sim_pos"]).statistic
        neutral = laps[(laps["round"] == rnd)]["neutral"].mean()
        summary.append({"round": rnd, "name": names.get(rnd, "?"), "n": len(df),
                        "mae": df["error_s"].abs().mean(), "bias": df["error_s"].mean(),
                        "pos_err": (df["sim_pos"] - actual_rank).abs().mean(), "rho": rho,
                        "fuel": m.fuel_source, "neutral": neutral})
        all_rows.append(df)
        all_res += [(rnd,) + r for r in res]

    out = pd.concat(all_rows)
    db_out = out[["season", "round", "driver", "actual_time", "sim_time", "error_s", "actual_pos", "sim_pos"]]
    con.register("incoming", db_out)
    con.execute("CREATE OR REPLACE TABLE sim_replay AS SELECT * FROM incoming")
    con.unregister("incoming")

    s = pd.DataFrame(summary)
    print("Replay, 2026 reliable races. Cars run independently: no traffic, no overtaking.")
    print("Real lap times on lap 1, SC/VSC/red laps, red flag restart laps and pit in/out laps; model everywhere else.\n")
    print(f"  {'race':<30}{'drivers':>8}{'MAE s':>8}{'bias s':>8}{'pos err':>8}{'rank r':>8}"
          f"  fuel       neutral laps")
    for r in s.itertuples():
        print(f"  R{r.round:02d} {r.name:<26}{r.n:>8}{r.mae:>8.2f}{r.bias:>+8.2f}{r.pos_err:>8.2f}"
              f"{r.rho:>8.2f}  {r.fuel:<11}{r.neutral:>5.0%}")
    print(f"  {'season average':<30}{s['n'].sum():>8}{s['mae'].mean():>8.2f}{s['bias'].mean():>+8.2f}"
          f"{s['pos_err'].mean():>8.2f}{s['rho'].mean():>8.2f}")
    worst = s.nlargest(3, "mae")
    print("\nWorst fits: " + ", ".join(f"R{r.round:02d} {r.name} ({r.mae:.1f}s)" for r in worst.itertuples()))

    cliff_check(pd.DataFrame(all_res, columns=["round", "driver", "stint", "lap", "resid", "is_clean"]))


# ---------------------------------------------------------------------------
# What-if
# ---------------------------------------------------------------------------
def parse_plan(text: str) -> list[tuple[str, int]]:
    """'M:20,H:50' -> [('MEDIUM', 20), ('HARD', 50)]"""
    plan = []
    for part in text.split(","):
        comp, laps = part.strip().split(":")
        comp = comp.strip().upper()
        plan.append((LETTERS.get(comp, comp), int(laps)))
    return plan


def pit_losses(m: RaceModel, laps: pd.DataFrame,
               field_median: pd.Series) -> tuple[list[float], list[float]]:
    """Every real stop's pit_loss = actual (in-lap + out-lap) - expected (in-lap + out-lap).
    Expected = the model on a green lap, or the field's median non-pit lap on a
    neutralised lap (the model knows nothing about SC pace; using it would count
    the SC slowness as pit loss). Split into green stops and SC/VSC stops."""
    green, sc = [], []
    for drv, d in laps.groupby("driver"):
        d = d.set_index("lap_number")
        for k in d.index[d["is_in"] & ~d["red"]]:
            if k + 1 not in d.index:
                continue
            i, o = d.loc[k], d.loc[k + 1]
            mi = (field_median.get(k) if i["neutral"]
                  else m.lap(drv, i["compound"], i["tyre_life"], k, i["segment"]))
            mo = (field_median.get(k + 1) if o["neutral"]
                  else m.lap(drv, o["compound"], o["tyre_life"], k + 1, o["segment"]))
            if None in (mi, mo) or pd.isna(i["lap_time"]) or pd.isna(o["lap_time"]):
                continue
            loss = i["lap_time"] + o["lap_time"] - mi - mo
            (sc if i["neutral"] or o["neutral"] else green).append(loss)
    return green, sc


def simulate_plan(m: RaceModel, driver: str, d: pd.DataFrame, field_median: pd.Series,
                  plan: list[tuple[str, int, int, bool]], loss: dict) -> tuple[float, list[str], dict]:
    """plan: [(compound, laps, start_age, stop_is_free)]. Returns (time, notes, {lap: time}).

    Lap base times: real on lap 1 and neutralised laps; on a neutralised lap where
    the driver really pitted, the field's median lap instead (so the real stop
    isn't counted). Green laps from the model. Each stop adds a pit loss on its in-lap."""
    d = d.set_index("lap_number")
    per, notes, lap = {}, [], 1
    for i, (comp, n, start_age, free_stop) in enumerate(plan):
        for j in range(n):
            row = d.loc[lap]
            age = start_age + j
            median = field_median.get(lap, np.nan)
            if row["neutral"] and (row["is_in"] or row["is_out"]) and not row["red"] and pd.notna(median):
                # Really pitted under SC/VSC: use a non-pitting car's lap so the real stop isn't counted.
                per[lap] = median
            elif lap == 1 or row["neutral"] or row["after_red"]:
                # Lap 1, neutralised laps, and red flag laps (every car is in the pit lane, so
                # there's no non-pitting lap to compare with): the driver's own real time. Both
                # the real and the what-if simulation take this branch, so it cancels in the delta.
                if pd.isna(row["lap_time"]):
                    raise ValueError(f"no lap time for {driver} on lap {lap}")
                per[lap] = row["lap_time"]
            else:
                pred = m.lap(driver, comp, age, lap, row["segment"])
                if pred is None:
                    raise ValueError(f"no model for {comp} on lap {lap}")
                per[lap] = pred
            lap += 1
        if i < len(plan) - 1 and not free_stop:  # a stop at the end of this stint: cost on the in-lap
            in_lap, out_lap = d.loc[lap - 1], d.loc[lap]
            if in_lap["neutral"] or out_lap["neutral"]:
                per[lap - 1] += loss["sc"]
                notes.append(f"stop lap {lap - 1}: SC/VSC pit loss {loss['sc']:.1f}s ({loss['sc_source']})")
            else:
                per[lap - 1] += loss["green"]
                notes.append(f"stop lap {lap - 1}: green pit loss {loss['green']:.1f}s")
    return float(sum(per.values())), notes, per


def real_plan(d: pd.DataFrame) -> list[tuple[str, int, int, bool]]:
    """The driver's real stints: compound, laps, starting tyre age, and whether
    the stop at the end was a free red flag tyre change."""
    plan = []
    for _, s in d.sort_values("lap_number").groupby("stint", sort=True):
        plan.append((s["compound"].mode().iloc[0], len(s), start_age(s), bool(s["red"].iloc[-1])))
    return plan


def start_age(stint_laps: pd.DataFrame) -> int:
    """Tyre age on a stint's first lap. FastF1 sometimes has no tyre_life for some
    or all laps of a stint: count back from the first known age, or assume a new
    set (age 1) if the whole stint is missing."""
    ages = stint_laps.sort_values("lap_number")["tyre_life"].reset_index(drop=True)
    first = ages.first_valid_index()
    return 1 if first is None else max(1, int(ages[first]) - first)


def fmt_plan(plan) -> str:
    return " - ".join(f"{c[0]}{n}" for c, n, *_ in plan)


class WhatIfError(ValueError):
    """A what-if that breaks the rules or can't be simulated."""


def finish_times(con, season: int, rnd: int, race_laps: pd.DataFrame) -> pd.DataFrame:
    """Official finish per driver (index = driver): laps, time, position, status.

    time = official race time (winner's time + gap, from race_results) for lead-lap
    finishers. Lapped cars: the sum of their own lap times, which is only ever compared
    with cars on the same lap count. Retired cars: inf. Summing lap times for EVERYONE
    is wrong in red flag races, which is why the official results come first."""
    try:
        r = con.execute("SELECT driver, laps, position, status, total_time_s FROM race_results "
                        "WHERE season = ? AND round = ?", [season, rnd]).df()
    except duckdb.CatalogException:
        r = pd.DataFrame()
    if r.empty:
        raise WhatIfError(f"No official results for round {rnd}: run python src/results.py --season {season}")
    sums = race_laps.groupby("driver")["lap_time"].agg(lambda s: s.sum() if s.notna().all() else np.inf)
    r["time"] = r["total_time_s"].astype(float)
    lapped = r["time"].isna()
    r.loc[lapped, "time"] = r.loc[lapped, "driver"].map(sums).astype(float).fillna(np.inf)
    r.loc[~r["status"].isin(["Finished", "Lapped"]), "time"] = np.inf
    return r.set_index("driver")[["laps", "time", "position", "status"]]


def red_flag_lap(d: pd.DataFrame) -> int | None:
    """The driver's last lap under a red flag (tyres can be changed for free then)."""
    red = d.loc[d["red"].astype(bool), "lap_number"]
    return None if red.empty else int(red.max())


def apply_red_flag_change(wi: list[tuple], d: pd.DataFrame,
                          compound: str | None = None) -> tuple[list[tuple], str | None]:
    """Give a what-if plan the free tyre change during the (last) red flag, like every
    real driver could take: the stint running at the red flag ends there with no pit
    loss, and the rest of it continues on a FRESH set of `compound` (default: the same
    compound). A planned stop that falls exactly on the red flag lap becomes free."""
    lap_red = red_flag_lap(d)
    if lap_red is None:
        return wi, None
    out, note, start = [], None, 1
    for i, (comp, n, age, free) in enumerate(wi):
        end = start + n - 1
        if start <= lap_red < end:  # red flag in the middle of this stint: split it
            new = compound or comp
            out += [(comp, lap_red - start + 1, age, True), (new, end - lap_red, 1, free)]
            note = f"red flag lap {lap_red}: free tyre change onto a fresh {new.title()} set"
        elif end == lap_red and i < len(wi) - 1:  # planned stop right at the red flag
            out.append((comp, n, age, True))
            note = f"red flag lap {lap_red}: the planned stop is taken for free during the red flag"
        else:
            out.append((comp, n, age, free))
        start = end + 1
    return out, note


def run_whatif(con, models: dict, rnd: int, driver: str, stints,
               red_flag_change: bool = True, red_flag_compound: str | None = None) -> dict:
    """Run one what-if and return the result (no printing).

    stints: "M:20,H:50" or [("MEDIUM", 20), ("HARD", 50)].
    red_flag_change: in a red flag race, take the free tyre change during the red flag
    (as the real drivers could), onto a fresh set of red_flag_compound (None = same compound).
    Raises WhatIfError if the strategy breaks a rule or the race has no model."""
    m = models.get(rnd)
    if m is None:
        raise WhatIfError(f"No model for round {rnd}.")
    laps = con.execute(LAPS_SQL, [SEASON]).df()
    laps = laps[laps["round"] == rnd]
    d = laps[laps["driver"] == driver].sort_values("lap_number")
    if d.empty:
        raise WhatIfError(f"{driver} has no laps in round {rnd}.")

    # --- rules ---------------------------------------------------------------
    plan_in = parse_plan(stints) if isinstance(stints, str) else list(stints)
    used = set(laps["compound"].dropna()) & DRY
    if sum(n for _, n in plan_in) != len(d):
        raise WhatIfError(f"Stints add up to {sum(n for _, n in plan_in)} laps; {driver} ran {len(d)}.")
    bad = [c for c, _ in plan_in if c not in used]
    if bad:
        raise WhatIfError(f"Compound(s) {bad} weren't used in this race (used: {sorted(used)}).")
    if len({c for c, _ in plan_in}) < 2:
        raise WhatIfError("A dry race needs at least 2 different dry compounds.")
    if any(n < 1 for _, n in plan_in):
        raise WhatIfError("Every stint needs at least 1 lap.")
    if f"driver_{driver}" not in m.p.index:
        raise WhatIfError(f"{driver} has too few clean laps in this race for a pace baseline, "
                          "so this race can't be simulated for them.")
    modelled = {c for c in DRY if f"slope:{c}" in m.p.index}
    missing = sorted({c for c, _ in plan_in} - modelled)
    if missing:
        raise WhatIfError(f"No tyre model for {', '.join(missing)} in this race (too few clean laps "
                          f"on it). Modelled compounds: {', '.join(sorted(modelled))}.")
    real_missing = sorted({c for c in d["compound"].dropna() if c in DRY} - modelled)
    if real_missing:
        raise WhatIfError(f"{driver}'s real strategy used {', '.join(real_missing)}, which has no tyre "
                          "model in this race, so the real strategy can't be simulated to compare against.")

    # --- pit loss for this circuit ---------------------------------------------
    field_median = laps[~laps["is_in"] & ~laps["is_out"]].groupby("lap_number")["lap_time"].median()
    green, sc = pit_losses(m, laps, field_median)
    loss = {"green": float(np.median(green))}
    loss["sc"], loss["sc_source"] = ((float(np.median(sc)), f"median of {len(sc)} SC stops")
                                     if sc else (loss["green"] / 2, "no SC stops: half the green loss"))

    # --- delta method -------------------------------------------------------------
    real = real_plan(d)
    first_age = start_age(d[d["stint"] == d["stint"].iloc[0]])
    wi = [(c, n, first_age if i == 0 else 1, False)
          for i, (c, n) in enumerate(plan_in)]  # what-if sets are new (age 1 on the out-lap)
    red_note = None
    if red_flag_change:
        if red_flag_compound is not None and red_flag_compound not in modelled:
            raise WhatIfError(f"No tyre model for {red_flag_compound} in this race.")
        wi, red_note = apply_red_flag_change(wi, d, red_flag_compound)
    try:
        sim_real, _, per_real = simulate_plan(m, driver, d, field_median, real, loss)
        sim_wi, notes, per_wi = simulate_plan(m, driver, d, field_median, wi, loss)
        if red_note:
            notes.append(red_note)
    except ValueError as exc:
        raise WhatIfError(str(exc)) from exc
    # The driver's OFFICIAL finishing time (not a sum of lap times: wrong in red flag races).
    fin = finish_times(con, SEASON, rnd, laps)
    if driver not in fin.index or not np.isfinite(fin.loc[driver, "time"]):
        raise WhatIfError(f"{driver} has no official finishing time in round {rnd}.")
    actual = float(fin.loc[driver, "time"])
    my_laps = int(fin.loc[driver, "laps"])
    wi_time = actual + (sim_wi - sim_real)
    times = fin["time"].copy()

    # Red flag race: the time gained before the stoppage only changes the restart order;
    # only the time after the last restart counts. Others can drop a slot if we pass them.
    red = None
    mine = red_flag_split(d, {k: per_wi[k] - per_real.get(k, 0.0) for k in per_wi})
    if mine is not None:
        rows = {driver: {**mine, "official": actual}}
        for drv, g in laps[laps["driver"] != driver].groupby("driver"):
            info = red_flag_split(g, {})
            if info is not None and drv in fin.index and np.isfinite(fin.loc[drv, "time"]):
                rows[drv] = {**info, "official": float(fin.loc[drv, "time"])}
        if len(rows) >= 2:
            out = red_flag_finish(pd.DataFrame.from_dict(rows, orient="index"))
            times.loc[out.index] = out["finish"].astype(float)
            wi_time = float(out.loc[driver, "finish"])
            red = {"lap": mine["red_lap"], "pre_delta": mine["pre_shift"], "post_delta": mine["post_shift"],
                   "slot_actual": int(out.loc[driver, "slot_actual"]),
                   "slot_sim": int(out.loc[driver, "slot_sim"])}

    # Place against everyone else's official time (more laps first, then time).
    others = fin.drop(driver).assign(time=times.drop(driver))
    pos = 1 + int(((others["laps"] > my_laps) | ((others["laps"] == my_laps) & (others["time"] < wi_time))).sum())
    actual_pos = int(fin.loc[driver, "position"])
    return {"round": rnd, "driver": driver, "reliable": m.reliable,
            "real_plan": real, "whatif_plan": wi, "notes": notes, "loss": loss,
            "n_green_stops": len(green), "laps": my_laps,
            "actual_time": actual, "whatif_time": wi_time, "delta": wi_time - actual,
            "pred_pos": pos, "actual_pos": actual_pos, "others": others.reset_index(), "red": red,
            "red_change": red_note}


def whatif(con, models: dict, rnd: int, driver: str, stints: str,
           red_flag_change: bool = True, red_flag_compound: str | None = None) -> None:
    """CLI wrapper: run a what-if and print it."""
    try:
        r = run_whatif(con, models, rnd, driver, stints, red_flag_change, red_flag_compound)
    except WhatIfError as exc:
        raise SystemExit(str(exc))
    if not r["reliable"]:
        print("WARNING: this race is marked unreliable in race_effects.")
    print(f"\nWhat-if R{rnd:02d} {driver}  (pit loss: green median {r['loss']['green']:.1f}s"
          f" of {r['n_green_stops']} stops)")
    print(f"  real    {fmt_plan(r['real_plan'])}")
    print(f"  what-if {fmt_plan(r['whatif_plan'])}")
    for n in r["notes"]:
        print(f"    {n}")
    if r["red"]:
        rf = r["red"]
        print(f"  red flag lap {rf['lap']}: {rf['pre_delta']:+.1f}s before the stoppage -> restart slot "
              f"P{rf['slot_actual']} -> P{rf['slot_sim']} (gap reset); {rf['post_delta']:+.1f}s after the restart counts")
    print(f"  time vs real: {r['delta']:+.1f}s   predicted P{r['pred_pos']} (actual P{r['actual_pos']})")


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="F1 strategy simulator v1")
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("replay")
    w = sub.add_parser("whatif")
    w.add_argument("--round", type=int, required=True)
    w.add_argument("--driver", required=True)
    w.add_argument("--stints", required=True, help='e.g. "M:20,H:50"')
    w.add_argument("--no-red-flag-change", action="store_true",
                   help="don't take the free tyre change during a red flag")
    w.add_argument("--red-flag-compound", help="S / M / H for the fresh set after the red flag "
                                               "(default: same compound)")
    args = parser.parse_args()

    con = db.connect()
    if args.mode == "replay":
        run_replay(con)
        models = load_models(con, SEASON)
        print("\nExample what-ifs, round 11:")
        for drv, plan in EXAMPLES:
            whatif(con, models, 11, drv, plan)
    else:
        comp = args.red_flag_compound
        comp = LETTERS.get(comp.upper(), comp.upper()) if comp else None
        whatif(con, load_models(con, SEASON), args.round, args.driver.upper(), args.stints,
               not args.no_red_flag_change, comp)
    con.close()


# Round 11 examples printed after a replay (see the summary in the chat for why).
EXAMPLES = [
    ("HAM", "S:25,H:45"),              # HAM on a 1-stop
    ("LEC", "S:13,H:20,H:20,S:17"),    # LEC pitting 3 laps earlier each time (real 16-20-20-14)
    ("HAM", "M:25,H:25,S:20"),         # my pick: a VER-style 2-stop on new sets
]

if __name__ == "__main__":
    main()
