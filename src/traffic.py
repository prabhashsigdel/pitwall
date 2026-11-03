"""Time lost in traffic: how much a driver lost stuck behind another car.

Usage:
    python src/traffic.py --round 12 --driver HAM --blocker LEC
    python src/traffic.py --round 12 --driver HAM --laps 40,41,42

For each traffic lap: time lost = actual lap time - the model's clean air
prediction for that lap (driver baseline + segment + compound offset +
deg x tyre age + fuel/track x lap, the same per race model as sim.py).
The model is fitted on clean laps only (no traffic), so it is the driver's
clean air pace.
"""

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

import config
import db
import sim

# Which car was physically ahead at the start of each lap: the most recent line
# crossing by any other car, on track (not lap 1, not an out-lap). This is the same
# rule clean.py uses for gap_ahead_s, repeated here because clean_laps doesn't store
# WHO was ahead.
AHEAD_SQL = """
WITH crossings AS (
    SELECT season, round, driver AS ahead_driver, lap_start_time AS crossed_at
    FROM laps
    WHERE lap_number > 1 AND pit_out_time IS NULL AND lap_start_time IS NOT NULL
      AND season = ? AND round = ?
)
SELECT l.driver, l.lap_number,
       CASE WHEN l.lap_number > 1 AND c.ahead_driver <> l.driver THEN c.ahead_driver END AS ahead_driver,
       cl.gap_ahead_s, cl.is_wet, cl.is_restart
FROM laps l
ASOF LEFT JOIN crossings c
  ON l.season = c.season AND l.round = c.round AND l.lap_start_time > c.crossed_at
LEFT JOIN clean_laps cl
  ON cl.season = l.season AND cl.round = l.round AND cl.driver = l.driver AND cl.lap_number = l.lap_number
WHERE l.season = ? AND l.round = ?
"""


class TrafficError(ValueError):
    """The request can't be answered (no model, unknown driver, ...)."""


def skip_reason(row, m: sim.RaceModel, driver: str) -> str | None:
    """Why a lap can't be used for a clean air comparison, or None if it can."""
    if row.lap_number == 1:
        return "lap 1"
    if row.red or row.after_red:
        return "red flag"
    if row.neutral:
        return "SC/VSC"
    if row.is_in or row.is_out:
        return "pit in/out lap"
    if bool(row.is_wet):
        return "wet"
    if bool(row.is_restart):
        return "restart after SC/VSC"
    if row.compound not in sim.DRY or f"slope:{row.compound}" not in m.p.index:
        return f"no model for {row.compound}"
    if pd.isna(row.lap_time) or pd.isna(row.tyre_life):
        return "missing lap time / tyre age"
    return None


def finishing_impact(fin: pd.DataFrame, driver: str, saved: float) -> dict:
    """Place the driver's OFFICIAL time minus `saved` against everyone else's official
    times (sim.finish_times: summing lap times is wrong in red flag races)."""
    if driver not in fin.index or not np.isfinite(fin.loc[driver, "time"]):
        raise TrafficError(f"{driver} has no official finishing time.")
    me = fin.loc[driver]
    others = fin.drop(driver)
    new_time = me["time"] - saved

    def place(t: float) -> int:
        return 1 + int(((others["laps"] > me["laps"]) | ((others["laps"] == me["laps"]) & (others["time"] < t))).sum())

    same_lap = others[others["laps"] == me["laps"]]
    ahead = same_lap[same_lap["time"] < me["time"]].sort_values("time")
    car_ahead = ahead.iloc[-1] if not ahead.empty else None
    return {
        "actual_pos": int(me["position"]), "new_pos": place(new_time),
        "actual_time": float(me["time"]), "new_time": float(new_time),
        "car_ahead": None if car_ahead is None else car_ahead.name,
        "gap_to_car_ahead": None if car_ahead is None else float(me["time"] - car_ahead["time"]),
    }


def traffic_cost(round: int, driver: str, blocker: str | None = None, laps: list[int] | None = None,
                 *, season: int = sim.SEASON, con=None, models: dict | None = None) -> dict:
    """Time `driver` lost in traffic in `round`.

    blocker: only laps where this car was directly ahead (None = any car).
    laps:    use exactly these laps instead of detecting traffic.
    con / models: optional, to reuse an open read-only connection and fitted models.
    """
    own = con is None
    if own:
        con = duckdb.connect(str(db.DB_PATH), read_only=True)
    try:
        models = models if models is not None else sim.load_models(con, season)
        m = models.get(round)
        if m is None:
            raise TrafficError(f"No race model for round {round}.")
        if f"driver_{driver}" not in m.p.index:
            raise TrafficError(f"{driver} has too few clean laps in round {round} for a clean air baseline.")
        race = con.execute(sim.LAPS_SQL, [season]).df()
        race = race[race["round"] == round]
        info = con.execute(AHEAD_SQL, [season, round, season, round]).df()
        try:
            fin = sim.finish_times(con, season, round, race)
        except sim.WhatIfError as exc:
            raise TrafficError(str(exc)) from exc
    finally:
        if own:
            con.close()

    d = race[race["driver"] == driver].merge(info, on=["driver", "lap_number"], how="left")
    if d.empty:
        raise TrafficError(f"{driver} has no laps in round {round}.")

    # Which laps to look at: given, or "directly behind the blocker / any car".
    if laps is not None:
        cand = d[d["lap_number"].isin(laps)]
    else:
        close = d["gap_ahead_s"] < config.TRAFFIC_GAP_S
        if blocker is not None:
            close &= d["ahead_driver"] == blocker
        cand = d[close.fillna(False)]

    used, skipped = [], []
    for row in cand.sort_values("lap_number").itertuples():
        reason = skip_reason(row, m, driver)
        if reason:
            skipped.append({"lap": int(row.lap_number), "reason": reason})
            continue
        pred = m.lap(driver, row.compound, row.tyre_life, row.lap_number, row.segment)
        used.append({"lap": int(row.lap_number), "ahead": row.ahead_driver,
                     "gap_ahead_s": None if pd.isna(row.gap_ahead_s) else float(row.gap_ahead_s),
                     "compound": row.compound, "tyre_age": int(row.tyre_life),
                     "actual": float(row.lap_time), "clean_air": float(pred),
                     "lost": float(row.lap_time - pred)})

    total = float(sum(u["lost"] for u in used))
    # Rough 95% uncertainty: each lap's clean air prediction is off by about the
    # model's residual std, independently, so n laps -> sigma * sqrt(n).
    uncertainty = 1.96 * m.sigma * np.sqrt(len(used)) if used else 0.0
    return {
        "round": round, "driver": driver, "blocker": blocker, "reliable": m.reliable,
        "laps_used": [u["lap"] for u in used], "per_lap": used, "skipped": skipped,
        "total_lost": total, "uncertainty": float(uncertainty), "sigma": m.sigma,
        **finishing_impact(fin, driver, total),
    }


def print_result(r: dict) -> None:
    who = f"held up by {r['blocker']}" if r["blocker"] else "in traffic (any car)"
    print(f"R{r['round']:02d} {r['driver']} {who}")
    if not r["reliable"]:
        print("  WARNING: this race is marked unreliable overall; only non-wet laps are used.")
    for u in r["per_lap"]:
        print(f"  lap {u['lap']:>2}  behind {u['ahead']}  gap {u['gap_ahead_s']:.2f}s  {u['compound'][0]}{u['tyre_age']:<3}"
              f"  actual {u['actual']:.3f}  clean air {u['clean_air']:.3f}  lost {u['lost']:+.3f}s")
    for s in r["skipped"]:
        print(f"  lap {s['lap']:>2}  skipped: {s['reason']}")
    print(f"  total lost {r['total_lost']:+.2f}s ± {r['uncertainty']:.2f}s (95%, {len(r['per_lap'])} laps)")
    print(f"  finish: P{r['actual_pos']} -> P{r['new_pos']} on time alone", end="")
    if r["car_ahead"]:
        print(f"  (was {r['gap_to_car_ahead']:.2f}s behind {r['car_ahead']} at the flag)")
    else:
        print()
    print("  Being ahead on time doesn't guarantee finishing ahead: rivals would have raced differently.")


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description="Time lost in traffic")
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--driver", required=True)
    p.add_argument("--blocker")
    p.add_argument("--laps", help="comma separated lap numbers, e.g. 40,41,42")
    a = p.parse_args()
    laps = [int(x) for x in a.laps.split(",")] if a.laps else None
    try:
        print_result(traffic_cost(a.round, a.driver.upper(), a.blocker.upper() if a.blocker else None, laps))
    except TrafficError as exc:
        raise SystemExit(str(exc))
    except duckdb.IOException:
        raise SystemExit("The database is busy (another process has it open). Close it and try again.")


if __name__ == "__main__":
    main()
