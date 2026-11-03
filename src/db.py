"""DuckDB connection + schema.

Every table carries (season, round). That pair is the key that links a lap,
a weather sample or a race control message back to its row in `races`, and it
means adding another season later (e.g. 2025) needs no schema change.
"""

from pathlib import Path

import duckdb

# Resolve paths relative to the project root, so the scripts work no matter
# which directory you run them from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "f1.duckdb"

SCHEMA = """
-- One row per race. A race only appears here once ALL of its data has been
-- written (see ingest.py), so this table doubles as the "already loaded" list.
CREATE TABLE IF NOT EXISTS races (
    season      INTEGER NOT NULL,
    round       INTEGER NOT NULL,
    event_name  VARCHAR,
    circuit     VARCHAR,
    country     VARCHAR,
    date        DATE,
    total_laps  INTEGER,
    PRIMARY KEY (season, round)
);

-- One row per driver per lap. Raw: nothing filtered out.
-- All *_time columns are float seconds. lap_time / sector times are durations;
-- pit_in_time, pit_out_time and lap_start_time are "seconds since the
-- session clock started", so they line up with weather.session_time.
CREATE TABLE IF NOT EXISTS laps (
    season          INTEGER NOT NULL,
    round           INTEGER NOT NULL,
    driver          VARCHAR,    -- three-letter code, e.g. 'VER'
    driver_number   VARCHAR,
    team            VARCHAR,
    lap_number      INTEGER,
    lap_time        DOUBLE,
    sector1_time    DOUBLE,
    sector2_time    DOUBLE,
    sector3_time    DOUBLE,
    compound        VARCHAR,    -- SOFT / MEDIUM / HARD / INTERMEDIATE / WET
    tyre_life       INTEGER,    -- laps on this set, including laps from earlier sessions
    stint           INTEGER,
    fresh_tyre      BOOLEAN,
    pit_in_time     DOUBLE,     -- set on the lap the driver entered the pits (in-lap)
    pit_out_time    DOUBLE,     -- set on the lap the driver left the pits (out-lap)
    lap_start_time  DOUBLE,
    track_status    VARCHAR,    -- string of status codes seen during the lap, e.g. '1', '4', '26'
    is_accurate     BOOLEAN,
    deleted         BOOLEAN,
    position        INTEGER
);

-- Derived from laps: one row each time a driver's stint number goes up.
-- `lap` is the in-lap (the last lap of the old stint).
CREATE TABLE IF NOT EXISTS pit_stops (
    season          INTEGER NOT NULL,
    round           INTEGER NOT NULL,
    driver          VARCHAR,
    lap             INTEGER,
    stint_before    INTEGER,
    stint_after     INTEGER,
    compound_before VARCHAR,
    compound_after  VARCHAR
);

-- Roughly one sample per minute. session_time = seconds since session start.
CREATE TABLE IF NOT EXISTS weather (
    season       INTEGER NOT NULL,
    round        INTEGER NOT NULL,
    session_time DOUBLE,
    air_temp     DOUBLE,
    track_temp   DOUBLE,
    humidity     DOUBLE,
    rainfall     BOOLEAN
);

-- Race control messages (flags, safety car, penalties, track limits, ...).
-- `time` is a real wall-clock timestamp (UTC), not a duration.
CREATE TABLE IF NOT EXISTS race_control (
    season   INTEGER NOT NULL,
    round    INTEGER NOT NULL,
    time     TIMESTAMP,
    lap      INTEGER,
    category VARCHAR,
    message  VARCHAR,
    flag     VARCHAR
);
"""


def connect(db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    """Open (or create) the database file and make sure all tables exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(SCHEMA)
    return con


def loaded_rounds(con: duckdb.DuckDBPyConnection, season: int) -> set[int]:
    """Rounds of `season` that are already fully in the database."""
    rows = con.execute("SELECT round FROM races WHERE season = ?", [season]).fetchall()
    return {r[0] for r in rows}
