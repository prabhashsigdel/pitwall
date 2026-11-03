"""Official race results from FastF1 -> race_results table (rebuilt each run).

Usage:
    python src/results.py --season 2026

Why: summing a driver's lap times does NOT give their finishing time in red flag
races (FastF1's lap times around the suspension don't add up the same way for
every car), so the order and gaps can come out wrong. FastF1's session results
are the official classification.

Columns: position (official), classified_position ('1'.., 'R' = retired, ...),
status ('Finished', 'Lapped', 'Retired', ...), laps, total_time_s (winner's race
time + gap; only for lead-lap finishers, NULL otherwise), gap_to_winner_s.
"""

import argparse
import logging
import sys

import fastf1
import pandas as pd

import db
import ingest

log = logging.getLogger("results")


def load_results(season: int, rnd: int) -> pd.DataFrame:
    """Official classification for one race (light load: no laps/telemetry)."""
    session = fastf1.get_session(season, rnd, "R")
    session.load(laps=False, telemetry=False, weather=False, messages=False)
    r = session.results.copy()
    lead_laps = r["Laps"].max()
    # Time: the winner's total race time; for everyone else the gap to the winner.
    # Only meaningful for cars that finished on the lead lap.
    t = r["Time"].dt.total_seconds()
    winner = t[r["Position"] == 1].iloc[0]
    on_lead_lap = (r["Laps"] == lead_laps) & r["Status"].isin(["Finished", "Lapped"]) & t.notna()
    gap = t.where(on_lead_lap & (r["Position"] != 1), 0.0).where(on_lead_lap)
    return pd.DataFrame({
        "season": season, "round": rnd, "driver": r["Abbreviation"], "team": r["TeamName"],
        "position": r["Position"].astype("Int64"), "classified_position": r["ClassifiedPosition"].astype(str),
        "status": r["Status"], "laps": r["Laps"].astype("Int64"),
        "gap_to_winner_s": gap, "total_time_s": winner + gap,
    })


def compare_with_lap_sums(con, season: int) -> None:
    """Show where the old method (sum of lap times) disagrees with the official order."""
    rows = con.execute("""
        WITH filled AS (  -- exactly what sim.py / traffic.py used to do
            SELECT round, driver,
                   COALESCE(lap_time, LEAD(lap_start_time) OVER w - lap_start_time) AS lap_time
            FROM laps WHERE season = ?
            WINDOW w AS (PARTITION BY round, driver ORDER BY lap_number)
        ),
        sums AS (
            SELECT round, driver, count(*) AS laps,
                   CASE WHEN count(*) = count(lap_time) THEN sum(lap_time) END AS lap_sum
            FROM filled GROUP BY ALL
        ),
        old AS (
            SELECT round, driver,
                   row_number() OVER (PARTITION BY round ORDER BY laps DESC, lap_sum NULLS LAST) AS old_pos
            FROM sums
        )
        SELECT o.round, o.driver, o.old_pos, r.position
        FROM old o JOIN race_results r ON r.season = ? AND r.round = o.round AND r.driver = o.driver
        WHERE o.old_pos <> r.position AND r.status IN ('Finished', 'Lapped')
        ORDER BY o.round, r.position
    """, [season, season]).fetchall()
    print("\nWhere summing lap times gave a different finishing order than the official result:")
    if not rows:
        print("  nowhere")
    for rnd, drv, old, new in rows:
        print(f"  R{rnd:02d} {drv}: lap-sum order P{old}, official P{new}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Store official race results.")
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    fastf1.Cache.configure(cache_dir=str(ingest.CACHE_DIR))
    fastf1.set_log_level("WARNING")
    logging.getLogger("fastf1").propagate = False

    con = db.connect()
    rounds = [r for (r,) in con.execute("SELECT round FROM races WHERE season = ? ORDER BY round",
                                       [args.season]).fetchall()]
    frames, failed = [], []
    for rnd in rounds:
        try:
            frames.append(load_results(args.season, rnd))
        except Exception as exc:  # one race failing shouldn't stop the rest
            log.exception("R%02d failed", rnd)
            failed.append(f"R{rnd:02d}: {type(exc).__name__}: {exc}")
    df = pd.concat(frames, ignore_index=True)
    con.execute("CREATE TABLE IF NOT EXISTS race_results AS SELECT * FROM df LIMIT 0")
    con.execute("DELETE FROM race_results WHERE season = ?", [args.season])
    con.execute("INSERT INTO race_results BY NAME SELECT * FROM df")
    print(f"race_results: {len(frames)} races, {len(df)} drivers stored for {args.season}")
    for f in failed:
        print(f"  FAILED {f}")
    compare_with_lap_sums(con, args.season)
    con.close()


if __name__ == "__main__":
    main()
