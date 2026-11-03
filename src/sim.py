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
    Returns (total, [(stint, lap, residual actual - model, is_clean)]) for the cliff check."""
    total, residuals = 0.0, []
    for lap in d.itertuples():
        use_real = lap.lap_number == 1 or lap.neutral or lap.after_red or lap.is_in or lap.is_out
        pred = None if use_real or lap.compound not in DRY else m.lap(
            lap.driver, lap.compound, lap.tyre_life, lap.lap_number, lap.segment)
        if pred is not None:
            total += pred
            if pd.notna(lap.lap_time):
                residuals.append((lap.stint, lap.lap_number, lap.lap_time - pred, bool(lap.is_clean)))
        elif pd.notna(lap.lap_time):
            total += lap.lap_time
        else:
            return None, []  # needed the real time and it's missing
    return total, residuals


def replay_race(m: RaceModel, laps: pd.DataFrame, finishers: pd.DataFrame, rnd: int):
    rows, residuals = [], []
    for f in finishers.itertuples():
        d = laps[laps["driver"] == f.driver].sort_values("lap_number")
        sim, res = replay_driver(m, d)
        actual = d["lap_time"].sum(min_count=len(d)) if d["lap_time"].notna().all() else None
        if sim is None or actual is None:
            continue
        rows.append({"season": SEASON, "round": rnd, "driver": f.driver, "laps": len(d),
                     "actual_time": float(actual), "sim_time": sim, "error_s": sim - float(actual),
                     "actual_pos": int(f.finish_position)})
        residuals += [(f.driver,) + r for r in res]
    df = pd.DataFrame(rows)
    if df.empty:
        return df, residuals
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
        df, res = replay_race(m, laps[laps["round"] == rnd], finished[finished["round"] == rnd], rnd)
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
                  plan: list[tuple[str, int, int, bool]], loss: dict) -> tuple[float, list[str]]:
    """plan: [(compound, laps, start_age, stop_is_free)]. Returns (time, notes).

    Lap base times: real on lap 1 and neutralised laps; on a neutralised lap where
    the driver really pitted, the field's median lap instead (so the real stop
    isn't counted). Green laps from the model. Each stop adds a pit loss on its in-lap."""
    d = d.set_index("lap_number")
    total, notes, lap = 0.0, [], 1
    for i, (comp, n, start_age, free_stop) in enumerate(plan):
        for j in range(n):
            row = d.loc[lap]
            age = start_age + j
            if row["neutral"] and (row["is_in"] or row["is_out"]):
                total += field_median[lap]
            elif lap == 1 or row["neutral"] or row["after_red"]:
                total += row["lap_time"]
            else:
                pred = m.lap(driver, comp, age, lap, row["segment"])
                if pred is None:
                    raise ValueError(f"no model for {comp} on lap {lap}")
                total += pred
            lap += 1
        if i < len(plan) - 1 and not free_stop:  # a stop at the end of this stint
            in_lap, out_lap = d.loc[lap - 1], d.loc[lap]
            if in_lap["neutral"] or out_lap["neutral"]:
                total += loss["sc"]
                notes.append(f"stop lap {lap - 1}: SC/VSC pit loss {loss['sc']:.1f}s ({loss['sc_source']})")
            else:
                total += loss["green"]
                notes.append(f"stop lap {lap - 1}: green pit loss {loss['green']:.1f}s")
    return total, notes


def real_plan(d: pd.DataFrame) -> list[tuple[str, int, int, bool]]:
    """The driver's real stints: compound, laps, starting tyre age, and whether
    the stop at the end was a free red flag tyre change."""
    plan = []
    for _, s in d.sort_values("lap_number").groupby("stint", sort=True):
        plan.append((s["compound"].mode().iloc[0], len(s), int(s["tyre_life"].iloc[0]),
                     bool(s["red"].iloc[-1])))
    return plan


def fmt_plan(plan) -> str:
    return " - ".join(f"{c[0]}{n}" for c, n, *_ in plan)


def whatif(con, models: dict, rnd: int, driver: str, stints: str) -> None:
    m = models.get(rnd)
    if m is None:
        raise SystemExit(f"No model for round {rnd}.")
    laps = con.execute(LAPS_SQL, [SEASON]).df()
    laps = laps[laps["round"] == rnd]
    d = laps[laps["driver"] == driver].sort_values("lap_number")
    if d.empty:
        raise SystemExit(f"{driver} has no laps in round {rnd}.")

    # --- rules ---------------------------------------------------------------
    plan_in = parse_plan(stints)
    used = set(laps["compound"].dropna()) & DRY
    if sum(n for _, n in plan_in) != len(d):
        raise SystemExit(f"Stints add up to {sum(n for _, n in plan_in)} laps; {driver} ran {len(d)}.")
    bad = [c for c, _ in plan_in if c not in used]
    if bad:
        raise SystemExit(f"Compound(s) {bad} weren't used in this race (used: {sorted(used)}).")
    if len({c for c, _ in plan_in}) < 2:
        raise SystemExit("A dry race needs at least 2 different dry compounds.")
    if not m.reliable:
        print("WARNING: this race is marked unreliable in race_effects.")

    # --- pit loss for this circuit ---------------------------------------------
    field_median = laps[~laps["is_in"] & ~laps["is_out"]].groupby("lap_number")["lap_time"].median()
    green, sc = pit_losses(m, laps, field_median)
    loss = {"green": float(np.median(green))}
    loss["sc"], loss["sc_source"] = ((float(np.median(sc)), f"median of {len(sc)} SC stops")
                                     if sc else (loss["green"] / 2, "no SC stops: half the green loss"))

    # --- delta method -------------------------------------------------------------
    real = real_plan(d)
    wi = [(c, n, int(d["tyre_life"].iloc[0]) if i == 0 else 1, False)
          for i, (c, n) in enumerate(plan_in)]  # what-if sets are new (age 1 on the out-lap)
    sim_real, _ = simulate_plan(m, driver, d, field_median, real, loss)
    sim_wi, notes = simulate_plan(m, driver, d, field_median, wi, loss)
    actual = float(d["lap_time"].sum())
    wi_time = actual + (sim_wi - sim_real)

    # Place against everyone else's ACTUAL time (more laps first, then time).
    others = laps[laps["driver"] != driver].groupby("driver").agg(
        n=("lap_number", "size"), t=("lap_time", lambda s: s.sum() if s.notna().all() else np.inf))
    pos = 1 + int(((others["n"] > len(d)) | ((others["n"] == len(d)) & (others["t"] < wi_time))).sum())
    actual_pos = con.execute("SELECT finish_position FROM strategies WHERE season=? AND round=? AND driver=?",
                             [SEASON, rnd, driver]).fetchone()[0]

    print(f"\nWhat-if R{rnd:02d} {driver}  (pit loss: green median {loss['green']:.1f}s of {len(green)} stops)")
    print(f"  real    {fmt_plan(real)}")
    print(f"  what-if {fmt_plan(wi)}")
    for n in notes:
        print(f"    {n}")
    print(f"  time vs real: {wi_time - actual:+.1f}s   predicted P{pos} (actual P{actual_pos})")


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="F1 strategy simulator v1")
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("replay")
    w = sub.add_parser("whatif")
    w.add_argument("--round", type=int, required=True)
    w.add_argument("--driver", required=True)
    w.add_argument("--stints", required=True, help='e.g. "M:20,H:50"')
    args = parser.parse_args()

    con = db.connect()
    if args.mode == "replay":
        run_replay(con)
        models = load_models(con, SEASON)
        print("\nExample what-ifs, round 11:")
        for drv, plan in EXAMPLES:
            whatif(con, models, 11, drv, plan)
    else:
        whatif(con, load_models(con, SEASON), args.round, args.driver.upper(), args.stints)
    con.close()


# Round 11 examples printed after a replay (see the summary in the chat for why).
EXAMPLES = [
    ("HAM", "S:25,H:45"),              # HAM on a 1-stop
    ("LEC", "S:13,H:20,H:20,S:17"),    # LEC pitting 3 laps earlier each time (real 16-20-20-14)
    ("HAM", "M:25,H:25,S:20"),         # my pick: a VER-style 2-stop on new sets
]

if __name__ == "__main__":
    main()
