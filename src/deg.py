"""Tyre degradation model. Rebuilds all output tables + 2 charts each run.

Usage:
    python src/deg.py

Uses only clean laps (clean_laps.is_clean) on SOFT / MEDIUM / HARD.

The model, one OLS regression per race:

    lap_time = driver baseline            (each driver's own pace)
             + segment offset             (new level after each red flag)
             + compound offset            (soft/hard vs medium)
             + fuel_track * lap_number    (cars get faster as fuel burns + track rubbers in)
             + deg[compound] * tyre_life  (tyre wear, one slope per compound)

lap_number and tyre_life both go up by 1 each lap, but pit stops reset
tyre_life and not lap_number, which is what lets the model tell them apart.

Team / driver deg use the SAME model, but the tyre_life slope is split per
(compound, team) or per (compound, driver). "vs field" = that slope minus the
lap-weighted average slope of everyone on that compound in that race.
"""

import itertools
import sys

import matplotlib

matplotlib.use("Agg")  # write PNGs, never open a window
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

import duckdb

import config
import db

OUTPUT_DIR = db.PROJECT_ROOT / "outputs"
COMPOUNDS = ["SOFT", "MEDIUM", "HARD"]
REFERENCE = "MEDIUM"  # compound offsets are reported relative to this
COLORS = {"SOFT": "#d62728", "MEDIUM": "#e0b000", "HARD": "#555555"}
MIN_GROUP_LAPS = 3  # below this a slope can't be fitted at all (dropped from the fit)

NULL_EST = {"est": None, "se": None, "ci_low": None, "ci_high": None}


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------
def load_clean_laps(con) -> pd.DataFrame:
    df = con.execute("""
        WITH marked AS (
            -- A red flag "starts" on a lap with code '5' when the driver's
            -- previous lap had none. Uses ALL laps (red flag laps are never clean).
            SELECT *,
                   COALESCE(track_status LIKE '%5%', false)
                   AND NOT COALESCE(LAG(track_status LIKE '%5%') OVER w, false) AS red_start
            FROM clean_laps
            WINDOW w AS (PARTITION BY season, round, driver ORDER BY lap_number)
        ),
        segmented AS (
            -- segment = how many red flags this driver has been through so far:
            -- 0 = before any red flag, 1 = after the first, ...
            SELECT *, (SUM(red_start::INT) OVER w)::INT AS segment
            FROM marked
            WINDOW w AS (PARTITION BY season, round, driver ORDER BY lap_number)
        )
        SELECT season, round, driver, COALESCE(team, 'UNKNOWN') AS team,
               compound, stint, segment, lap_number, tyre_life, lap_time, fuel_corrected_time
        FROM segmented
        WHERE is_clean AND compound IN ('SOFT', 'MEDIUM', 'HARD')
    """).df()
    # Group labels for the team / driver slope models.
    df["compound_team"] = df["compound"] + "|" + df["team"]
    df["compound_driver"] = df["compound"] + "|" + df["driver"]
    return df


def fittable(laps: pd.DataFrame, slope_by: str) -> pd.DataFrame:
    """Drop laps whose slope or driver baseline can't be estimated at all:
    slope groups with < 3 laps or a single tyre_life value, then drivers with < 3 laps."""
    g = laps.groupby(slope_by)["tyre_life"]
    laps = laps[(g.transform("size") >= MIN_GROUP_LAPS) & (g.transform("nunique") >= 2)]
    # Red flag segments with almost no laps (e.g. a red flag on lap 2) can't
    # get their own baseline, so their few laps are dropped.
    laps = laps[laps.groupby("segment")["lap_time"].transform("size") >= MIN_GROUP_LAPS]
    n_driver = laps.groupby("driver")["lap_time"].transform("size")
    return laps[n_driver >= MIN_GROUP_LAPS].reset_index(drop=True)


def fit_race(laps: pd.DataFrame, slope_by: str, fixed_fuel: float | None = None):
    """Fit the race model with one tyre_life slope per value of `slope_by`.

    fixed_fuel: if given, the fuel+track effect is NOT estimated. It's fixed at
    that many seconds gained per lap: we add it back onto lap_time (same idea
    as fuel_corrected_time) and leave the lap_number column out.

    Returns (statsmodels result, laps actually used), or (None, laps) if the
    model can't be estimated (too few laps, or columns that can't be separated).
    """
    laps = fittable(laps, slope_by)
    if laps.empty:
        return None, laps

    # One 0/1 column per driver = each driver's own baseline (no shared intercept).
    X = pd.get_dummies(laps["driver"], prefix="driver", dtype=float)
    present = [c for c in COMPOUNDS if (laps["compound"] == c).any()]
    ref = REFERENCE if REFERENCE in present else present[0]
    for c in present:
        if c != ref:
            X[f"offset:{c}"] = (laps["compound"] == c).astype(float)
    # Red flag segments: one shared offset per segment after the first, so pace
    # before and after a red flag (new tyres, cooled track) gets its own level.
    # Offsets are relative to the earliest segment that has laps.
    segments = sorted(laps["segment"].unique())
    for k in segments[1:]:
        X[f"segment:{k}"] = (laps["segment"] == k).astype(float)
    y = laps["lap_time"].astype(float)
    if fixed_fuel is None:
        X["lap_number"] = laps["lap_number"].astype(float)
    else:
        y = y + fixed_fuel * laps["lap_number"].astype(float)
    # tyre_life for laps in this group, 0 elsewhere = a separate slope per group.
    for g in sorted(laps[slope_by].unique()):
        X[f"slope:{g}"] = laps["tyre_life"].where(laps[slope_by] == g, 0).astype(float)

    # Need more laps than unknowns, and every column must carry new information.
    if len(X) <= X.shape[1] or np.linalg.matrix_rank(X.values) < X.shape[1]:
        return None, laps
    return sm.OLS(y, X).fit(), laps


def combo(res, weights: dict[str, float]) -> dict:
    """Estimate + 95% CI of a weighted sum of coefficients, e.g. {A: 1, B: -1} = A - B."""
    if res is None or any(k not in res.params.index for k in weights):
        return dict(NULL_EST)
    c = pd.Series(0.0, index=res.params.index)
    for name, w in weights.items():
        c[name] += w
    est = float(c @ res.params)
    se = float(np.sqrt(c @ res.cov_params() @ c))
    if not np.isfinite(se) or se == 0:
        return dict(NULL_EST)
    t = stats.t.ppf(0.975, res.df_resid)
    return {"est": est, "se": se, "ci_low": est - t * se, "ci_high": est + t * se}


# ---------------------------------------------------------------------------
# Per-race tables
# ---------------------------------------------------------------------------
def choose_fuel(res) -> tuple[dict, bool]:
    """The model's fuel+track estimate, and whether to replace it with the
    season fallback: only if it's outside FUEL_SANE_RANGE or its 95% CI is
    wider than FUEL_MAX_CI_WIDTH. A CI that merely includes zero is kept: a
    small true fuel effect shouldn't be thrown out."""
    # lap_number coefficient is negative (cars get faster). Flip the sign so it
    # reads like config.FUEL_EFFECT_PER_LAP: "seconds gained per lap".
    est = combo(res, {"lap_number": -1.0})
    if est["est"] is None:
        return est, True
    lo, hi = config.FUEL_SANE_RANGE
    too_wide = est["ci_high"] - est["ci_low"] > config.FUEL_MAX_CI_WIDTH
    return est, not lo <= est["est"] <= hi or too_wide


def season_fuel_fallback(laps: pd.DataFrame, wet: dict) -> dict[int, tuple[float, int]]:
    """Per season: median fuel+track estimate over races that are reliable AND
    kept their own estimate, plus how many races that is. Per season because
    fuel use changes with the regulations."""
    good: dict[int, list[float]] = {}
    for (season, rnd), race in laps.groupby(["season", "round"]):
        free, _ = fit_race(race, "compound")
        est, needs_fallback = choose_fuel(free)
        r2 = free.rsquared if free is not None else None
        ok, _ = reliability(r2, wet.get((season, rnd), 0.0))
        good.setdefault(season, [])
        if ok and not needs_fallback:
            good[season].append(est["est"])
    # No usable race at all: fall back to the config value rather than crash.
    return {s: (float(np.median(v)), len(v)) if v else (config.FUEL_EFFECT_PER_LAP, 0)
            for s, v in good.items()}


def reliability(r2: float | None, wet_pct: float) -> tuple[bool, str | None]:
    reasons = []
    if r2 is None or r2 < config.MIN_R2:
        reasons.append("no fit" if r2 is None else f"R2 {r2:.2f} < {config.MIN_R2}")
    if wet_pct > config.WET_RACE_PCT:
        reasons.append(f"wet laps {wet_pct:.0%} > {config.WET_RACE_PCT:.0%}")
    return not reasons, "; ".join(reasons) or None


def field_rows(season: int, rnd: int, laps: pd.DataFrame, wet_pct: float,
               fallback_value: float):
    """One race_effects row + one tyre_deg row per compound.
    Also returns the fixed fuel used (None = estimated) and the final fit."""
    free, used = fit_race(laps, "compound")
    fuel, needs_fallback = choose_fuel(free)
    fixed = fallback_value if needs_fallback else None
    res = free
    if fixed is not None:
        res, used = fit_race(laps, "compound", fixed)
    # R2 always from the free fit, so it's comparable across races.
    r2 = free.rsquared if free is not None else None
    is_reliable, reason = reliability(r2, wet_pct)

    soft = combo(res, {"offset:SOFT": 1.0})
    hard = combo(res, {"offset:HARD": 1.0})
    effects = {
        "season": season, "round": rnd,
        "fuel_track_effect_per_lap": fuel["est"] if fixed is None else fixed,
        "fuel_source": "estimated" if fixed is None else "fixed",
        # The model's own estimate is always kept, even when not used.
        "fuel_estimated": fuel["est"],
        "fuel_est_ci_low": fuel["ci_low"], "fuel_est_ci_high": fuel["ci_high"],
        "fuel_ci_width": None if fuel["est"] is None else fuel["ci_high"] - fuel["ci_low"],
        "fuel_fallback_value": fallback_value,
        "soft_vs_medium_s": soft["est"], "hard_vs_medium_s": hard["est"],
        "n_laps": len(used), "r_squared": r2, "wet_pct": wet_pct,
        "is_reliable": is_reliable, "unreliable_reason": reason,
    }

    deg = []
    for c in COMPOUNDS:
        sub = used[used["compound"] == c]
        e = combo(res, {f"slope:{c}": 1.0})
        n, n_drv = len(sub), sub["driver"].nunique()
        deg.append({
            "season": season, "round": rnd, "compound": c,
            "deg_per_lap": e["est"], "se": e["se"],
            "ci_low": e["ci_low"], "ci_high": e["ci_high"],
            "n_laps": n, "n_drivers": n_drv,
            "low_confidence": e["est"] is None
                or n < config.MIN_LAPS_FIELD or n_drv < config.MIN_DRIVERS_FIELD,
        })
    return effects, deg, fixed, res


def group_rows(season, rnd, laps, key: str, min_laps: int, fixed_fuel: float | None = None) -> tuple[list[dict], object, pd.DataFrame]:
    """Deg vs field per (compound, key), key = 'team' or 'driver'."""
    slope_by = f"compound_{key}"
    res, used = fit_race(laps, slope_by, fixed_fuel)
    rows = []
    for c in COMPOUNDS:
        sub = used[used["compound"] == c]
        counts = sub.groupby(key).size()
        if counts.empty:
            continue
        # Field average = lap-weighted mean of all the group slopes on this compound.
        weights = counts / counts.sum()
        field_avg = {f"slope:{c}|{k}": -w for k, w in weights.items()}
        for k, n in counts.items():
            own = f"slope:{c}|{k}"
            vs_field = dict(field_avg)
            vs_field[own] = vs_field[own] + 1.0
            e, absolute = combo(res, vs_field), combo(res, {own: 1.0})
            row = {"season": season, "round": rnd, "compound": c, key: k}
            if key == "driver":
                row["team"] = sub.loc[sub["driver"] == k, "team"].iloc[0]
            rows.append(row | {
                "deg_per_lap": absolute["est"], "deg_vs_field": e["est"], "se": e["se"],
                "ci_low": e["ci_low"], "ci_high": e["ci_high"], "n_laps": int(n),
                "low_confidence": e["est"] is None or n < min_laps,
            })
    return rows, res, used


def teammate_rows(season, rnd, res, used: pd.DataFrame) -> list[dict]:
    """Driver A deg minus driver B deg, same team, same compound, same race."""
    rows = []
    for (c, team), sub in used.groupby(["compound", "team"]):
        counts = sub.groupby("driver").size()
        for a, b in itertools.combinations(sorted(counts.index), 2):
            e = combo(res, {f"slope:{c}|{a}": 1.0, f"slope:{c}|{b}": -1.0})
            rows.append({
                "season": season, "round": rnd, "compound": c, "team": team,
                "driver_a": a, "driver_b": b,
                "a_minus_b": e["est"], "se": e["se"],
                "ci_low": e["ci_low"], "ci_high": e["ci_high"],
                "n_laps_a": int(counts[a]), "n_laps_b": int(counts[b]),
                "low_confidence": e["est"] is None
                    or min(counts[a], counts[b]) < config.MIN_LAPS_DRIVER,
            })
    return rows


# ---------------------------------------------------------------------------
# Season summaries: inverse-variance weighted average
# ---------------------------------------------------------------------------
def pool(df: pd.DataFrame, keys: list[str], value: str, reliable: set[str]) -> pd.DataFrame:
    """Combine per-race estimates. Weight = 1 / se^2, so a race with a CI twice
    as wide counts 4x less. Left out: low-confidence rows and unreliable races.
    Rows based on fewer than MIN_RACES_SEASON races are dropped. Produces one
    row per key per compound, plus a compound='ALL' row pooling all compounds."""
    cols = keys + ["compound", value, "se", "ci_low", "ci_high", "n_races"]
    race = df["season"].astype(str) + "-" + df["round"].astype(str)
    ok = df[~df["low_confidence"].astype(bool) & df["se"].notna() & race.isin(reliable)].copy()
    if ok.empty:
        return pd.DataFrame(columns=cols)
    ok["race"] = race[ok.index]
    ok["w"] = 1.0 / ok["se"].astype(float) ** 2
    ok["wx"] = ok["w"] * ok[value].astype(float)

    def agg(group_keys):
        g = ok.groupby(group_keys).agg(w=("w", "sum"), wx=("wx", "sum"), n_races=("race", "nunique"))
        return g.reset_index()

    out = pd.concat([agg(keys + ["compound"]), agg(keys).assign(compound="ALL")])
    out = out[out["n_races"] >= config.MIN_RACES_SEASON]
    out[value] = out["wx"] / out["w"]
    out["se"] = np.sqrt(1.0 / out["w"])
    out["ci_low"] = out[value] - 1.96 * out["se"]
    out["ci_high"] = out[value] + 1.96 * out["se"]
    return out[cols].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Cliff check
# ---------------------------------------------------------------------------
def cliff_rows(laps: pd.DataFrame) -> list[dict]:
    """Fit a straight line to each long stint's earlier laps, then see how much
    slower the last CLIFF_LAST_N laps were than that line predicts. Raw lap_time
    is fine here: within one stint, fuel burn is also a straight line.
    kind: 'cliff' above CLIFF_THRESHOLD_S, 'probable_damage' above
    DAMAGE_THRESHOLD_S (too sudden for wear), else None."""
    rows = []
    n_last = config.CLIFF_LAST_N
    for (season, rnd, driver, stint), s in laps.groupby(["season", "round", "driver", "stint"]):
        if len(s) < config.CLIFF_MIN_STINT:
            continue
        s = s.sort_values("tyre_life")
        early, last = s.iloc[:-n_last], s.iloc[-n_last:]
        slope, intercept = np.polyfit(early["tyre_life"].astype(float), early["lap_time"], 1)
        excess = float((last["lap_time"] - (intercept + slope * last["tyre_life"].astype(float))).mean())
        kind = ("probable_damage" if excess > config.DAMAGE_THRESHOLD_S
                else "cliff" if excess > config.CLIFF_THRESHOLD_S else None)
        rows.append({
            "season": season, "round": rnd, "driver": driver, "stint": int(stint),
            "compound": s["compound"].iloc[0], "n_laps": len(s),
            "avg_excess_s": excess, "possible_cliff": kind == "cliff", "kind": kind,
        })
    return rows


# ---------------------------------------------------------------------------
# Strategies (raw laps, every driver with at least one lap)
# ---------------------------------------------------------------------------
STRATEGIES_SQL = """
CREATE OR REPLACE TABLE strategies AS
WITH stints AS (
    SELECT season, round, driver, stint,
           CASE mode(compound) WHEN 'SOFT' THEN 'S' WHEN 'MEDIUM' THEN 'M'
                WHEN 'HARD' THEN 'H' WHEN 'INTERMEDIATE' THEN 'I'
                WHEN 'WET' THEN 'W' ELSE '?' END AS letter,
           count(*) AS n_laps,
           -- Did this stint end under a red flag? (code '5' on its last lap.)
           -- Tyres can be changed during a red flag, which bumps the stint
           -- number, but that isn't a pit stop.
           COALESCE(arg_max(track_status, lap_number) LIKE '%5%', false) AS ended_by_red
    FROM laps GROUP BY ALL
),
per_driver AS (
    SELECT season, round, driver,
           any_value(team) AS team,
           arg_max(position, lap_number) AS last_position,
           -- when the driver's final recorded lap ended (NULL if no lap time)
           arg_max(lap_start_time + lap_time, lap_number) AS last_lap_end
    FROM laps GROUP BY ALL
),
-- The winner's finish: earliest end of the race's highest lap number.
-- Every finisher (lapped or not) crosses the line after the winner;
-- a retired car's last lap ended earlier, or has no lap time at all.
winner AS (
    SELECT season, round, min(lap_start_time + lap_time) AS finish_time
    FROM laps l
    WHERE lap_number = (SELECT max(lap_number) FROM laps x
                        WHERE x.season = l.season AND x.round = l.round)
    GROUP BY ALL
)
SELECT p.season, p.round, p.driver, p.team,
       -- stint changes, minus tyre changes made during a red flag
       count(*) - 1 - count(*) FILTER (WHERE s.ended_by_red
                                       AND s.stint < (SELECT max(stint) FROM stints x
                                           WHERE x.season = s.season AND x.round = s.round
                                             AND x.driver = s.driver)) AS n_stops,
       count(*) FILTER (WHERE s.ended_by_red)                AS red_flag_tyre_changes,
       string_agg(s.letter, '-' ORDER BY s.stint)            AS compound_sequence,
       string_agg(s.n_laps::VARCHAR, '-' ORDER BY s.stint)   AS stint_lengths,
       CASE WHEN COALESCE(p.last_lap_end >= w.finish_time - 0.001, false)
            THEN p.last_position END                         AS finish_position,
       COALESCE(p.last_lap_end >= w.finish_time - 0.001, false) AS finished
FROM per_driver p
JOIN stints s USING (season, round, driver)
JOIN winner w USING (season, round)
GROUP BY p.season, p.round, p.driver, p.team, p.last_position, p.last_lap_end, w.finish_time
ORDER BY p.season, p.round, finish_position NULLS LAST, p.driver
"""


# ---------------------------------------------------------------------------
# Build everything
# ---------------------------------------------------------------------------
def save(con, name: str, rows) -> pd.DataFrame:
    """Write rows (list of dicts or DataFrame) to a table, replacing it."""
    df = pd.DataFrame(rows)
    con.register("incoming", df)
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM incoming")
    con.unregister("incoming")
    return df


def wet_share(con) -> dict:
    """Fraction of each race's laps (all laps, not just clean) flagged is_wet."""
    rows = con.execute("SELECT season, round, avg(is_wet::INT) FROM clean_laps GROUP BY ALL").fetchall()
    return {(s, r): float(p) for s, r, p in rows}


def build_all(con) -> dict:
    laps = load_clean_laps(con)
    wet = wet_share(con)
    out = {k: [] for k in ["race_effects", "tyre_deg", "team_deg", "driver_deg", "teammate_deg"]}
    fits = {}  # (season, round) -> (field fit, fixed fuel or None), for the race chart
    # Pass 1: season fuel fallback from the races we trust.
    fallback = season_fuel_fallback(laps, wet)

    for (season, rnd), race in laps.groupby(["season", "round"]):
        # Pass 2: fit each race, using the fallback where its own estimate is bad.
        effects, deg, fixed, res = field_rows(season, rnd, race, wet.get((season, rnd), 0.0),
                                              fallback[season][0])
        fits[(season, rnd)] = (res, fixed)
        out["race_effects"].append(effects)
        out["tyre_deg"] += deg
        # Team / driver fits use the same fuel decision as the field fit.
        team, _, _ = group_rows(season, rnd, race, "team", config.MIN_LAPS_TEAM, fixed)
        out["team_deg"] += team
        drv, res_d, used = group_rows(season, rnd, race, "driver", config.MIN_LAPS_DRIVER, fixed)
        out["driver_deg"] += drv
        out["teammate_deg"] += teammate_rows(season, rnd, res_d, used)

    tables = {name: save(con, name, rows) for name, rows in out.items()}
    eff = tables["race_effects"][tables["race_effects"]["is_reliable"]]
    reliable = set(eff["season"].astype(str) + "-" + eff["round"].astype(str))
    for name, keys, value in [("team_deg", ["team"], "deg_vs_field"),
                              ("driver_deg", ["driver"], "deg_vs_field"),
                              ("teammate_deg", ["team", "driver_a", "driver_b"], "a_minus_b")]:
        tables[f"{name}_season"] = save(con, f"{name}_season",
                                        pool(tables[name], keys, value, reliable))
    tables["cliff_flags"] = save(con, "cliff_flags", cliff_rows(laps))
    con.execute(STRATEGIES_SQL)
    tables["strategies"] = con.execute("SELECT * FROM strategies").df()
    tables["laps"] = laps
    tables["fits"] = fits
    tables["fuel_fallback"] = fallback
    return tables


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def chart_race(t: dict, season: int, rnd: int, name: str) -> None:
    """Each clean lap minus that driver's baseline, its red-flag segment offset
    and the model's own fuel+track effect for this race. What's left is the
    compound offset + tyre wear + noise, so fast and slow cars line up.
    The lines are exactly the model's compound offset + deg slope."""
    res, fixed = t["fits"][(season, rnd)]
    if res is None:
        return
    p = res.params
    laps = fittable(t["laps"].query("season == @season and round == @rnd"), "compound")
    base = (laps["driver"].map(lambda d: p[f"driver_{d}"])
            + laps["segment"].map(lambda k: p.get(f"segment:{k}", 0.0)))
    fuel_coef = p["lap_number"] if fixed is None else -fixed
    laps = laps.assign(y=laps["lap_time"] - base - fuel_coef * laps["lap_number"])
    deg = t["tyre_deg"].query("season == @season and round == @rnd").set_index("compound")

    fig, ax = plt.subplots(figsize=(10, 6))
    for c in COMPOUNDS:
        sub = laps[laps["compound"] == c]
        if sub.empty:
            continue
        x = sub["tyre_life"].astype(float)
        ax.scatter(x, sub["y"], s=10, alpha=0.4, color=COLORS[c])
        d = deg.loc[c]
        if pd.notna(d["deg_per_lap"]):
            xs = np.array([x.min(), x.max()])
            ax.plot(xs, p.get(f"offset:{c}", 0.0) + d["deg_per_lap"] * xs, color=COLORS[c], lw=2.5,
                    label=f"{c}: {d['deg_per_lap']:+.3f} s/lap "
                          f"[{d['ci_low']:+.3f}, {d['ci_high']:+.3f}], {len(sub)} laps")
    source = "fixed" if fixed is not None else "estimated"
    ax.set(xlabel="Tyre age (laps)",
           ylabel="Lap time vs driver + segment baseline, fuel-corrected (s)",
           title=f"{season} {name}: clean laps vs tyre age\n"
                 f"fuel+track {-fuel_coef:.3f} s/lap ({source}), 95% CI in legend")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / f"deg_{season}_R{rnd:02d}.png", dpi=120)
    plt.close(fig)


def chart_season_teams(t: dict) -> None:
    """Season team deg vs field, one panel per compound, with 95% CI bars."""
    s = t["team_deg_season"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 6), sharex=True)
    for ax, c in zip(axes, COMPOUNDS):
        sub = s[s["compound"] == c].sort_values("deg_vs_field")
        y = np.arange(len(sub))
        err = [sub["deg_vs_field"] - sub["ci_low"], sub["ci_high"] - sub["deg_vs_field"]]
        ax.errorbar(sub["deg_vs_field"], y, xerr=err, fmt="o", color=COLORS[c], capsize=3)
        ax.axvline(0, color="black", lw=0.8)
        ax.set_yticks(y, [f"{tm} ({n})" for tm, n in zip(sub["team"], sub["n_races"])])
        ax.set_title(c)
        ax.set_xlabel("Deg vs field (s/lap)\n<- easier on tyres | harder ->")
    fig.suptitle("Season tyre degradation vs field (95% CI, (n) = races)")
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "team_deg_season.png", dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Printed summary
# ---------------------------------------------------------------------------
def fmt(x, nd: int = 3) -> str:
    return "  n/a " if x is None or pd.isna(x) else f"{x:+.{nd}f}"


def print_fuel(t: dict, names: dict) -> None:
    lo, hi = config.FUEL_SANE_RANGE
    print(f"Fuel + track effect per race (s gained/lap). Fallback if outside "
          f"[{lo}, {hi}] or CI width > {config.FUEL_MAX_CI_WIDTH}:")
    print(f"  {'race':<30}{'est':>7}  {'95% CI':<17}{'width':>6}  {'reliable':<9}{'source':<10}{'used':>6}")
    for r in t["race_effects"].itertuples():
        ci = f"[{fmt(r.fuel_est_ci_low)}, {fmt(r.fuel_est_ci_high)}]"
        width = "  n/a" if pd.isna(r.fuel_ci_width) else f"{r.fuel_ci_width:.3f}"
        print(f"  R{r.round:02d} {names.get((r.season, r.round), '?'):<26}{fmt(r.fuel_estimated):>7}  "
              f"{ci:<17}{width:>6}  {str(r.is_reliable):<9}{r.fuel_source:<10}"
              f"{r.fuel_track_effect_per_lap:>6.3f}")
    for season, (median, n) in t["fuel_fallback"].items():
        print(f"\n  {season} fallback (median of reliable, self-estimated races): "
              f"{median:.3f} s/lap from {n} races  (config FUEL_EFFECT_PER_LAP = "
              f"{config.FUEL_EFFECT_PER_LAP})")
    unreliable = t["race_effects"].query("not is_reliable")
    for r in unreliable.itertuples():
        print(f"  R{r.round:02d} unreliable: {r.unreliable_reason}")


def print_race_deg(t: dict, season: int, rnd: int, name: str) -> None:
    print(f"\nDeg per compound, {season} {name} (s/lap per lap of tyre age):")
    deg = t["tyre_deg"].query("season == @season and round == @rnd")
    for r in deg.itertuples():
        low = "  (low confidence)" if r.low_confidence else ""
        print(f"  {r.compound:<7}{fmt(r.deg_per_lap)}  [{fmt(r.ci_low)}, {fmt(r.ci_high)}]"
              f"  {r.n_laps} laps, {r.n_drivers} drivers{low}")


def print_season(t: dict) -> None:
    teams = t["team_deg_season"].query("compound == 'ALL'").sort_values("deg_vs_field")
    print("\nSeason: teams from easiest to hardest on tyres (all compounds, s/lap vs field):")
    for i, r in enumerate(teams.itertuples(), 1):
        print(f"  {i:>2}. {r.team:<22}{fmt(r.deg_vs_field)}  [{fmt(r.ci_low)}, {fmt(r.ci_high)}]"
              f"  {r.n_races} races")

    pairs = t["teammate_deg_season"].query("compound == 'ALL'").sort_values("team")
    print("\nTeammates (A minus B, s/lap; negative = A easier on tyres):")
    for r in pairs.itertuples():
        sig = "" if r.ci_low < 0 < r.ci_high else "  * CI excludes 0"
        print(f"  {r.team:<22}{r.driver_a} - {r.driver_b}: {fmt(r.a_minus_b)}"
              f"  [{fmt(r.ci_low)}, {fmt(r.ci_high)}]  {r.n_races} races{sig}")


def print_cliffs(t: dict) -> None:
    c = t["cliff_flags"]
    if c.empty:
        print("\nNo stints long enough for the cliff check.")
        return
    cliffs, dmg = c[c["kind"] == "cliff"], c[c["kind"] == "probable_damage"]
    print(f"\nLong stints checked: {len(c)}. Cliffs: {len(cliffs)} ({len(cliffs) / len(c):.0%}),"
          f" probable damage: {len(dmg)}")
    for label, df in (("cliff", cliffs), ("damage", dmg)):
        for rnd, g in df.groupby("round"):
            print(f"  {label:<7}R{rnd:02d}: " + ", ".join(
                f"{r.driver} {r.compound[0]} (+{r.avg_excess_s:.2f}s)" for r in g.itertuples()))


COMPARE_ROUNDS = [4, 6, 12, 13, 16, 19]


def read_previous(con) -> dict:
    """race_effects / tyre_deg from the previous run, read before rebuilding."""
    prev = {}
    for name in ("race_effects", "tyre_deg", "team_deg_season", "teammate_deg_season"):
        try:
            prev[name] = con.execute(f"SELECT * FROM {name}").df()
        except duckdb.Error:  # first run: table doesn't exist yet
            prev[name] = pd.DataFrame()
    return prev


def value(df: pd.DataFrame, rnd: int, col: str, compound: str | None = None):
    """One cell for a round of the latest season, or None if it's not there."""
    if df.empty or col not in df.columns:
        return None
    m = (df["round"] == rnd) & (df["season"] == df["season"].max())
    if compound:
        m &= df["compound"] == compound
    return df.loc[m, col].iloc[0] if m.any() else None


def print_before_after(prev: dict, t: dict) -> None:
    pe, pdeg, ne, ndeg = prev["race_effects"], prev["tyre_deg"], t["race_effects"], t["tyre_deg"]
    print("\nBefore -> after (previous run vs this run):")
    for rnd in COMPARE_ROUNDS:
        if value(ne, rnd, "n_laps") is None:
            continue
        reason = value(ne, rnd, "unreliable_reason")
        print(f"  R{rnd:02d}  R2 {fmt(value(pe, rnd, 'r_squared'), 2)} -> {fmt(value(ne, rnd, 'r_squared'), 2)}"
              f"   fuel {fmt(value(pe, rnd, 'fuel_track_effect_per_lap'))}"
              f" -> {fmt(value(ne, rnd, 'fuel_track_effect_per_lap'))} ({value(ne, rnd, 'fuel_source')})"
              f"   reliable: {value(ne, rnd, 'is_reliable')}" + (f" ({reason})" if reason else ""))
        print("       deg " + "   ".join(
            f"{c[0]} {fmt(value(pdeg, rnd, 'deg_per_lap', c))} -> {fmt(value(ndeg, rnd, 'deg_per_lap', c))}"
            for c in COMPOUNDS))


def print_strategies(t: dict, season: int, rnd: int, name: str) -> None:
    s = t["strategies"].query("season == @season and round == @rnd")
    print(f"\nStrategies, {season} {name}:")
    for r in s.itertuples():
        pos = f"P{int(r.finish_position)}" if pd.notna(r.finish_position) else "DNF"
        print(f"  {pos:<4}{r.driver:<5}{str(r.team):<22}{r.n_stops} stop(s)  "
              f"{r.compound_sequence:<14}{r.stint_lengths}")


def print_season_vs_previous(prev: dict, t: dict) -> None:
    """Season team ranking and teammate gaps (all compounds): previous -> now."""
    def all_rows(df, keys, col):
        if df.empty:
            return pd.DataFrame(columns=keys + [col])
        return df[df["compound"] == "ALL"][keys + [col]]

    b = all_rows(prev["team_deg_season"], ["team"], "deg_vs_field")
    a = all_rows(t["team_deg_season"], ["team"], "deg_vs_field")
    m = a.merge(b, on="team", how="outer", suffixes=("", "_before")).sort_values("deg_vs_field")
    m["rank"] = m["deg_vs_field"].rank()
    m["rank_before"] = m["deg_vs_field_before"].rank()
    print("\nSeason team ranking, easiest -> hardest (s/lap vs field), previous -> now:")
    for r in m.itertuples():
        print(f"  {r.team:<22}{fmt(r.deg_vs_field_before)} -> {fmt(r.deg_vs_field)}"
              f"   rank {r.rank_before:.0f} -> {r.rank:.0f}")

    keys = ["team", "driver_a", "driver_b"]
    b = all_rows(prev["teammate_deg_season"], keys, "a_minus_b")
    a = all_rows(t["teammate_deg_season"], keys, "a_minus_b")
    m = a.merge(b, on=keys, how="outer", suffixes=("", "_before")).sort_values("team")
    print("\nTeammates (A minus B, s/lap), previous -> now:")
    for r in m.itertuples():
        print(f"  {r.team:<22}{r.driver_a} - {r.driver_b}: {fmt(r.a_minus_b_before)} -> {fmt(r.a_minus_b)}")


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    OUTPUT_DIR.mkdir(exist_ok=True)
    con = db.connect()
    prev = read_previous(con)  # for the before/after comparison
    t = build_all(con)
    names = {(s, r): n for s, r, n in con.execute(
        "SELECT season, round, event_name FROM races").fetchall()}

    # "Recent race" = latest RELIABLE race whose field model produced estimates.
    reliable = t["race_effects"].loc[t["race_effects"]["is_reliable"], ["season", "round"]]
    ok = (t["tyre_deg"].dropna(subset=["deg_per_lap"]).merge(reliable)
          .sort_values(["season", "round"]))
    season, rnd = (int(v) for v in ok.iloc[-1][["season", "round"]])
    name = names.get((season, rnd), "?")

    chart_race(t, season, rnd, name)
    chart_season_teams(t)
    print_fuel(t, names)
    print_race_deg(t, season, rnd, name)
    print_cliffs(t)
    print_before_after(prev, t)
    print_season_vs_previous(prev, t)
    print(f"\nCharts written to {OUTPUT_DIR}")
    con.close()


if __name__ == "__main__":
    main()
