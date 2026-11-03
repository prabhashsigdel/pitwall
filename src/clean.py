"""Build the clean_laps table used by the tyre degradation model.

Usage:
    python src/clean.py

clean_laps keeps EVERY row of the raw laps table. Nothing is deleted. Instead
each lap gets one boolean column per exclusion reason (is_lap1, is_pit_in, ...)
and a final is_clean, which is true only if no flag is set. So you can always
see why a lap was excluded, and change your mind later.

The raw laps table is only ever read, never modified. clean_laps is rebuilt
from scratch every run (CREATE OR REPLACE), so tweaking config.py and
re-running is always safe.
"""

import sys

import duckdb
import pandas as pd

import config
import db

# Track status codes from FastF1 (fastf1.api.track_status_data):
#   '1' track clear   '2' yellow   '4' Safety Car   '5' red flag
#   '6' VSC deployed  '7' VSC ending
# A lap's track_status is a string of every code seen during that lap,
# e.g. '671' = VSC, VSC ending, then clear. So "contains any of 4/5/6/7".
SC_VSC_RED_CODES = "4567"

# Every flag column, in the order they're printed. is_outlier is computed last
# because it depends on all the others.
FLAGS = [
    "is_lap1", "is_pit_in", "is_pit_out", "is_sc_vsc", "is_restart",
    "is_inaccurate", "is_deleted", "is_wet", "is_traffic", "is_missing_data",
    "is_outlier",
]


def build_sql() -> str:
    """The SQL that builds clean_laps. Each CTE adds one piece; read top-down."""
    fuel = float(config.FUEL_EFFECT_PER_LAP)
    traffic = float(config.TRAFFIC_GAP_S)
    outlier = float(config.OUTLIER_PCT)
    race_outlier = float(config.RACE_OUTLIER_PCT)
    min_stint = int(config.MIN_STINT_LAPS_FOR_MEDIAN)
    # A lap is an outlier if EITHER check says so (a NULL median = no verdict):
    #   stint check: > OUTLIER_PCT slower than the driver's stint median
    #   race check:  > RACE_OUTLIER_PCT slower than the driver's whole-race median
    outlier_expr = (f"(COALESCE(p.lap_time > m.stint_median * (1 + {outlier}), false)"
                    f" OR COALESCE(p.lap_time > rm.race_median * (1 + {race_outlier}), false))")

    return f"""
CREATE OR REPLACE TABLE clean_laps AS
WITH
-- 1. Every moment a car crossed the timing line ON TRACK.
--    A lap's start time is the moment the car crossed the line to begin it.
--    Left out:
--      * lap 1: every car's lap 1 starts at the same instant (the grid start),
--        which isn't a real line crossing.
--      * out-laps (pit_out_time set): that crossing happened in the pit lane,
--        so that car isn't physically ahead of anyone on track.
crossings AS (
    SELECT season, round, driver AS ahead_driver, lap_start_time AS crossed_at
    FROM laps
    WHERE lap_number > 1
      AND pit_out_time IS NULL
      AND lap_start_time IS NOT NULL
),

-- 2. Gap to the car physically ahead. For each lap start, an ASOF join finds
--    the most recent crossing strictly before it, by ANY car (so lapped and
--    lapping cars count, not just the car one position ahead).
--    If that most recent crossing was our own previous lap, then nobody
--    crossed in front of us for a whole lap: no car ahead, so the gap is NULL.
with_gap AS (
    SELECT l.*,
           CASE WHEN l.lap_number > 1 AND c.ahead_driver <> l.driver
                THEN l.lap_start_time - c.crossed_at
           END AS gap_ahead_s
    FROM laps l
    ASOF LEFT JOIN crossings c
      ON  l.season = c.season
      AND l.round  = c.round
      AND l.lap_start_time > c.crossed_at
),

-- 3. Weather: the sample closest BEFORE the lap started (another ASOF join).
--    weather.session_time and laps.lap_start_time use the same session clock.
with_weather AS (
    SELECT g.*, w.track_temp, w.air_temp, w.rainfall
    FROM with_gap g
    ASOF LEFT JOIN weather w
      ON  g.season = w.season
      AND g.round  = w.round
      AND g.lap_start_time >= w.session_time
),

-- 4. The simple per-lap flags. COALESCE(..., false) turns "unknown" (NULL)
--    into "flag not set" so every flag is a clean true/false.
flagged AS (
    SELECT *,
        COALESCE(lap_number = 1, false)                        AS is_lap1,
        pit_in_time  IS NOT NULL                               AS is_pit_in,
        pit_out_time IS NOT NULL                               AS is_pit_out,
        COALESCE(regexp_matches(track_status, '[{SC_VSC_RED_CODES}]'), false)
                                                               AS is_sc_vsc,
        -- Unknown accuracy counts as inaccurate (safer for the model).
        NOT COALESCE(is_accurate, false)                       AS is_inaccurate,
        COALESCE(deleted, false)                               AS is_deleted,
        COALESCE(compound IN ('INTERMEDIATE', 'WET'), false)
            OR COALESCE(rainfall, false)                       AS is_wet,
        COALESCE(gap_ahead_s < {traffic}, false)               AS is_traffic,
        -- Can't model a lap without a time, a compound or a tyre age.
        lap_time IS NULL OR compound IS NULL OR tyre_life IS NULL
                                                               AS is_missing_data
    FROM with_weather
),

-- 5. Restart lap: this lap is green, but this driver's previous lap had
--    SC/VSC/red flag. LAG looks one lap back for the same driver in the same race.
with_restart AS (
    SELECT *,
        NOT is_sc_vsc AND COALESCE(
            LAG(is_sc_vsc) OVER (PARTITION BY season, round, driver
                                 ORDER BY lap_number),
            false)                                             AS is_restart
    FROM flagged
),

-- 6. Did the lap pass every flag except is_outlier?
pre_clean AS (
    SELECT *,
        NOT (is_lap1 OR is_pit_in OR is_pit_out OR is_sc_vsc OR is_restart
             OR is_inaccurate OR is_deleted OR is_wet OR is_traffic
             OR is_missing_data)                               AS passes_other_flags
    FROM with_restart
),

-- 7. Each driver's median lap per stint, using ONLY laps that passed every
--    other flag. That way SC laps and pit laps don't drag the median up.
--    Stints with fewer than {min_stint} such laps get no median: with 1-2
--    laps the "median" is the bad lap itself, so the check can't catch it.
stint_medians AS (
    SELECT season, round, driver, stint, MEDIAN(lap_time) AS stint_median
    FROM pre_clean
    WHERE passes_other_flags
    GROUP BY season, round, driver, stint
    HAVING count(*) >= {min_stint}
),

-- 7b. Backup: each driver's median lap over the WHOLE race (same laps).
--     Always available as long as the driver has any lap passing the flags.
race_medians AS (
    SELECT season, round, driver, MEDIAN(lap_time) AS race_median
    FROM pre_clean
    WHERE passes_other_flags
    GROUP BY season, round, driver
)

-- 8. Final table: raw columns, then the extras, then the flags.
SELECT
    p.season, p.round, p.driver, p.driver_number, p.team,
    p.lap_number, p.lap_time, p.sector1_time, p.sector2_time, p.sector3_time,
    p.compound, p.tyre_life, p.stint, p.fresh_tyre,
    p.pit_in_time, p.pit_out_time, p.lap_start_time,
    p.track_status, p.is_accurate, p.deleted, p.position,

    -- Normalise to lap-1 fuel load: later laps are lighter (so faster),
    -- adding the fuel time back leaves tyre wear + track evolution.
    p.lap_time + {fuel} * (p.lap_number - 1)                   AS fuel_corrected_time,
    p.gap_ahead_s, p.track_temp, p.air_temp, p.rainfall,

    p.is_lap1, p.is_pit_in, p.is_pit_out, p.is_sc_vsc, p.is_restart,
    p.is_inaccurate, p.is_deleted, p.is_wet, p.is_traffic, p.is_missing_data,
    -- Only flags SLOW laps. NULL median or NULL lap_time = can't tell = false.
    {outlier_expr} AS is_outlier,

    p.passes_other_flags AND NOT {outlier_expr}                AS is_clean
FROM pre_clean p
LEFT JOIN stint_medians m
  ON  p.season = m.season AND p.round = m.round
  AND p.driver = m.driver AND p.stint = m.stint
LEFT JOIN race_medians rm
  ON  p.season = rm.season AND p.round = rm.round AND p.driver = rm.driver
ORDER BY p.season, p.round, p.driver, p.lap_number
"""


def build_clean_laps(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(build_sql())


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------
def print_overall(con: duckdb.DuckDBPyConnection) -> None:
    total, clean = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE is_clean) FROM clean_laps"
    ).fetchall()[0]
    print(f"Total laps: {total}")
    print(f"Clean laps: {clean} ({clean / total:.1%})" if total else "Clean laps: 0")


def print_flag_counts(con: duckdb.DuckDBPyConnection) -> None:
    """Count per flag. 'only reason' = laps where this is the ONLY flag set,
    i.e. laps you'd get back if you switched that flag off."""
    print(f"\n{'flag':<17}{'laps':>7}{'only reason':>13}")
    flag_sum = " + ".join(f"{f}::INT" for f in FLAGS)
    for f in FLAGS:
        n, only = con.execute(
            f"SELECT count(*) FILTER (WHERE {f}),"
            f"       count(*) FILTER (WHERE {f} AND ({flag_sum}) = 1)"
            f" FROM clean_laps"
        ).fetchall()[0]
        print(f"{f:<17}{n:>7}{only:>13}")


def print_per_race(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute("""
        SELECT c.season, c.round, r.event_name, count(*) AS laps,
               avg(c.is_clean::INT) AS pct_clean
        FROM clean_laps c
        LEFT JOIN races r USING (season, round)
        GROUP BY ALL
        ORDER BY c.season, c.round
    """).fetchall()
    print(f"\n% clean per race (<< marks races under {config.LOW_CLEAN_RACE_PCT:.0%}):")
    for season, rnd, name, laps, pct in rows:
        marker = "  << LOW" if pct < config.LOW_CLEAN_RACE_PCT else ""
        print(f"  {season} R{rnd:02d} {name or '?':<26}{laps:>6} laps {pct:>6.1%}{marker}")


def print_example_stint(con: duckdb.DuckDBPyConnection) -> None:
    """The stint with the most clean laps from the most recent race."""
    pick = con.execute("""
        SELECT season, round, driver, stint, count(*) FILTER (WHERE is_clean) AS n
        FROM clean_laps
        WHERE (season, round) = (SELECT (season, round) FROM races
                                 ORDER BY season DESC, round DESC LIMIT 1)
        GROUP BY ALL
        ORDER BY n DESC, driver
        LIMIT 1
    """).fetchall()
    if not pick:
        return
    season, rnd, driver, stint, _ = pick[0]
    df: pd.DataFrame = con.execute("""
        SELECT lap_number, lap_time, fuel_corrected_time, compound, tyre_life, is_clean
        FROM clean_laps
        WHERE season = ? AND round = ? AND driver = ? AND stint = ?
        ORDER BY lap_number
    """, [season, rnd, driver, stint]).df()
    print(f"\nExample stint: {season} R{rnd:02d} {driver}, stint {stint}")
    print(df.to_string(index=False, float_format=lambda x: f"{x:.3f}"))


def snapshot_previous(con: duckdb.DuckDBPyConnection) -> bool:
    """Copy the previous run's flags into a temp table (gone when we close)."""
    try:
        con.execute("""CREATE OR REPLACE TEMP TABLE prev_clean AS
                       SELECT season, round, driver, lap_number, is_outlier, is_clean
                       FROM clean_laps""")
        return True
    except duckdb.Error:  # first run: no clean_laps yet
        return False


def print_vs_previous(con: duckdb.DuckDBPyConnection) -> None:
    before, after, new_out, gone_out = con.execute("""
        SELECT count(*) FILTER (WHERE p.is_clean), count(*) FILTER (WHERE c.is_clean),
               count(*) FILTER (WHERE c.is_outlier AND NOT p.is_outlier),
               count(*) FILTER (WHERE p.is_outlier AND NOT c.is_outlier)
        FROM clean_laps c
        JOIN prev_clean p USING (season, round, driver, lap_number)
    """).fetchall()[0]
    print(f"vs previous run: clean laps {before} -> {after} ({after - before:+d}); "
          f"new outliers {new_out}, no longer outliers {gone_out}\n")


def main() -> None:
    sys.stdout.reconfigure(errors="replace")  # Windows console + accented names
    con = db.connect()
    had_previous = snapshot_previous(con)
    build_clean_laps(con)
    print("Rebuilt clean_laps.\n")
    if had_previous:
        print_vs_previous(con)
    print_overall(con)
    print_flag_counts(con)
    print_per_race(con)
    print_example_stint(con)
    con.close()


if __name__ == "__main__":
    main()
