"""Ingest one season of F1 race data from FastF1 into DuckDB.

Usage:
    python src/ingest.py --season 2026

What it does, per race in the season's schedule:
  1. Skip testing events and races that haven't happened yet.
  2. Skip races already in the database (so re-running is cheap and resumable).
  3. Load the race session from FastF1 (no telemetry, which keeps it fast).
  4. Convert laps / weather / race control messages into tidy DataFrames.
  5. Write everything for that race in ONE transaction. If anything fails,
     nothing for that race is kept, and the next run simply retries it.

No cleaning happens here: inaccurate laps, pit laps, safety car laps etc. are
all stored as-is. Cleaning is phase 2.
"""

import argparse
import logging
import sys
from dataclasses import dataclass, field

import duckdb
import fastf1
import pandas as pd

import db

CACHE_DIR = db.PROJECT_ROOT / "cache"

# A race counts as "in the past" only this many hours after its start time.
# That gives the race time to finish and FastF1's data source time to publish.
# If the data still isn't there, the race fails and the next run retries it.
RACE_DATA_DELAY_HOURS = 4

log = logging.getLogger("ingest")

# Text values FastF1 uses to mean "unknown". Stored as NULL instead.
TEXT_NULL_PLACEHOLDERS = ["None", ""]


# ---------------------------------------------------------------------------
# Column mappings: (FastF1 column, our column, how to convert it)
#
# Conversion kinds:
#   "seconds"   timedelta -> float seconds
#   "int"       float-with-NaN -> nullable integer (FastF1 stores lap numbers
#               etc. as floats because pandas ints can't hold NaN)
#   "float"     numeric
#   "bool"      nullable boolean
#   "text"      string
#   "timestamp" datetime
# ---------------------------------------------------------------------------
LAP_COLUMNS = [
    ("Driver", "driver", "text"),
    ("DriverNumber", "driver_number", "text"),
    ("Team", "team", "text"),
    ("LapNumber", "lap_number", "int"),
    ("LapTime", "lap_time", "seconds"),
    ("Sector1Time", "sector1_time", "seconds"),
    ("Sector2Time", "sector2_time", "seconds"),
    ("Sector3Time", "sector3_time", "seconds"),
    ("Compound", "compound", "text"),
    ("TyreLife", "tyre_life", "int"),
    ("Stint", "stint", "int"),
    ("FreshTyre", "fresh_tyre", "bool"),
    ("PitInTime", "pit_in_time", "seconds"),
    ("PitOutTime", "pit_out_time", "seconds"),
    ("LapStartTime", "lap_start_time", "seconds"),
    ("TrackStatus", "track_status", "text"),
    ("IsAccurate", "is_accurate", "bool"),
    ("Deleted", "deleted", "bool"),
    ("Position", "position", "int"),
]

WEATHER_COLUMNS = [
    ("Time", "session_time", "seconds"),
    ("AirTemp", "air_temp", "float"),
    ("TrackTemp", "track_temp", "float"),
    ("Humidity", "humidity", "float"),
    ("Rainfall", "rainfall", "bool"),
]

RACE_CONTROL_COLUMNS = [
    ("Time", "time", "timestamp"),
    ("Lap", "lap", "int"),
    ("Category", "category", "text"),
    ("Message", "message", "text"),
    ("Flag", "flag", "text"),
]


@dataclass
class RunSummary:
    """Collects what happened during a run so we can print it at the end."""

    loaded: list[tuple[int, str, int]] = field(default_factory=list)  # (round, name, laps)
    skipped_future: list[tuple[int, str]] = field(default_factory=list)
    skipped_existing: list[tuple[int, str]] = field(default_factory=list)
    failed: list[tuple[int, str, str]] = field(default_factory=list)  # (round, name, error)
    # "laps.compound" -> [rounds where that field came back empty]
    empty_fields: dict[str, list[int]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Converting FastF1 DataFrames into our table shapes
# ---------------------------------------------------------------------------
def convert(series: pd.Series, kind: str) -> pd.Series:
    """Convert one FastF1 column into the type we store in DuckDB."""
    if kind == "seconds":
        return pd.to_timedelta(series).dt.total_seconds()
    if kind == "int":
        return pd.to_numeric(series, errors="coerce").round().astype("Int64")
    if kind == "float":
        return pd.to_numeric(series, errors="coerce").astype("float64")
    if kind == "bool":
        return series.astype("boolean")
    if kind == "timestamp":
        return pd.to_datetime(series)
    if kind == "text":
        # FastF1 sometimes uses the literal string "None" (or "") to mean
        # "unknown", e.g. Compound and Team. Turn those into real NULLs so
        # `WHERE compound IS NULL` works. This is a type fix, not a lap filter.
        return series.astype("string").replace(TEXT_NULL_PLACEHOLDERS, pd.NA)
    raise ValueError(f"Unknown conversion kind: {kind}")


def is_empty(series: pd.Series) -> bool:
    """True if a column has no usable values (all null or all placeholders)."""
    if series.dtype == object or pd.api.types.is_string_dtype(series):
        series = series.replace(TEXT_NULL_PLACEHOLDERS, pd.NA)
    return bool(series.isna().all())


def build_table(
    source: pd.DataFrame,
    columns: list[tuple[str, str, str]],
    season: int,
    round_number: int,
    table: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Pick, rename and convert columns from a FastF1 DataFrame.

    Missing columns don't crash: they're filled with nulls. Returns the new
    DataFrame plus a list of "table.column" names that came back missing or
    completely empty, so we can report them.
    """
    out = pd.DataFrame(index=source.index)
    empty: list[str] = []

    for src_col, dest_col, kind in columns:
        if src_col in source.columns:
            raw = source[src_col]
        else:
            # Column doesn't exist at all (e.g. FastF1 changed for 2026 regs).
            raw = pd.Series([None] * len(source), index=source.index, dtype=object)
        if is_empty(raw):
            empty.append(f"{table}.{dest_col}")
        out[dest_col] = convert(raw, kind)

    out.insert(0, "round", round_number)
    out.insert(0, "season", season)
    return out.reset_index(drop=True), empty


def get_session_data(session: fastf1.core.Session, attr: str) -> pd.DataFrame:
    """Read session.laps / .weather_data / .race_control_messages safely.

    FastF1 raises DataNotLoadedError if a part of the data couldn't be loaded.
    We treat that as "no rows" so one missing piece doesn't sink the race.
    """
    try:
        data = getattr(session, attr)
    except Exception as exc:  # FastF1 raises DataNotLoadedError (and friends)
        log.warning("  %s not available: %s", attr, exc)
        return pd.DataFrame()
    return pd.DataFrame(data) if data is not None else pd.DataFrame()


# ---------------------------------------------------------------------------
# Writing to DuckDB
# ---------------------------------------------------------------------------
def insert_df(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> None:
    """Append a DataFrame to a table, matching columns by name."""
    if df.empty:
        return
    con.register("incoming", df)
    con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM incoming")
    con.unregister("incoming")


# Pit stops are derived from laps, with SQL. For each driver we look at the
# NEXT lap's stint (LEAD). Wherever it goes up, the driver pitted at the end
# of the current lap (the in-lap). Drivers who retire just have no next lap.
PIT_STOPS_SQL = """
INSERT INTO pit_stops
SELECT season, round, driver,
       lap_number      AS lap,
       stint           AS stint_before,
       next_stint      AS stint_after,
       compound        AS compound_before,
       next_compound   AS compound_after
FROM (
    SELECT season, round, driver, lap_number, stint, compound,
           LEAD(stint)    OVER w AS next_stint,
           LEAD(compound) OVER w AS next_compound
    FROM laps
    WHERE season = ? AND round = ?
    WINDOW w AS (PARTITION BY driver ORDER BY lap_number)
)
WHERE next_stint > stint
"""


def write_race(
    con: duckdb.DuckDBPyConnection,
    race_row: dict,
    laps: pd.DataFrame,
    weather: pd.DataFrame,
    race_control: pd.DataFrame,
) -> None:
    """Write all data for one race atomically (all or nothing)."""
    season, round_number = race_row["season"], race_row["round"]
    con.begin()
    try:
        insert_df(con, "laps", laps)
        con.execute(PIT_STOPS_SQL, [season, round_number])
        insert_df(con, "weather", weather)
        insert_df(con, "race_control", race_control)
        # The races row goes in LAST: it's the marker that says "this race is
        # complete". If we crash before here, the rollback removes the rest.
        insert_df(con, "races", pd.DataFrame([race_row]))
        con.commit()
    except Exception:
        con.rollback()
        raise


# ---------------------------------------------------------------------------
# One race
# ---------------------------------------------------------------------------
def ingest_race(
    con: duckdb.DuckDBPyConnection, season: int, event: pd.Series
) -> tuple[int, list[str]]:
    """Load one race from FastF1 and store it. Returns (lap count, empty fields)."""
    round_number = int(event["RoundNumber"])
    session = fastf1.get_session(season, round_number, "R")
    session.load(laps=True, telemetry=False, weather=True, messages=True)

    laps_raw = get_session_data(session, "laps")
    if laps_raw.empty:
        # Without laps the race is useless. Fail so the next run retries it.
        raise RuntimeError("no lap data returned (data may not be published yet)")

    laps, empty_laps = build_table(laps_raw, LAP_COLUMNS, season, round_number, "laps")
    weather, empty_weather = build_table(
        get_session_data(session, "weather_data"), WEATHER_COLUMNS, season, round_number, "weather"
    )
    race_control, empty_rc = build_table(
        get_session_data(session, "race_control_messages"),
        RACE_CONTROL_COLUMNS, season, round_number, "race_control",
    )

    # A table with zero rows means every one of its fields is empty, so
    # report it once as "table (no rows)" instead of listing every column.
    empty_fields = list(empty_laps)
    for name, df, empties in (("weather", weather, empty_weather),
                              ("race_control", race_control, empty_rc)):
        empty_fields += [f"{name} (no rows)"] if df.empty else empties

    total_laps = getattr(session, "total_laps", None)
    if total_laps is None:
        empty_fields.append("races.total_laps")

    race_row = {
        "season": season,
        "round": round_number,
        "event_name": event["EventName"],
        "circuit": circuit_name(session, event),
        "country": event["Country"],
        "date": pd.Timestamp(event["EventDate"]).date(),
        "total_laps": total_laps,
    }
    write_race(con, race_row, laps, weather, race_control)
    return len(laps), empty_fields


def circuit_name(session: fastf1.core.Session, event: pd.Series) -> str:
    """Circuit short name from the live timing metadata, else the event location."""
    try:
        return session.session_info["Meeting"]["Circuit"]["ShortName"]
    except (AttributeError, KeyError, TypeError):
        return event["Location"]


def race_start_utc(event: pd.Series) -> pd.Timestamp | None:
    """Find the 'Race' session among Session1..Session5 and return its UTC start."""
    for i in range(1, 6):
        if event.get(f"Session{i}") == "Race":
            start = event.get(f"Session{i}DateUtc")
            return None if pd.isna(start) else pd.Timestamp(start)
    return None


# ---------------------------------------------------------------------------
# Whole season
# ---------------------------------------------------------------------------
def ingest_season(season: int) -> RunSummary:
    summary = RunSummary()
    con = db.connect()
    already_loaded = db.loaded_rounds(con, season)

    # include_testing=False drops pre-season testing (round 0) up front.
    schedule = fastf1.get_event_schedule(season, include_testing=False)
    # Schedule times are UTC but timezone-naive, so compare against naive UTC.
    cutoff = pd.Timestamp.now(tz="UTC").tz_localize(None) - pd.Timedelta(hours=RACE_DATA_DELAY_HOURS)

    for _, event in schedule.iterrows():
        round_number = int(event["RoundNumber"])
        name = event["EventName"]

        if round_number in already_loaded:
            summary.skipped_existing.append((round_number, name))
            continue

        start = race_start_utc(event)
        if start is None or start > cutoff:
            summary.skipped_future.append((round_number, name))
            continue

        log.info("Round %d: %s - loading...", round_number, name)
        try:
            lap_count, empty_fields = ingest_race(con, season, event)
        except Exception as exc:
            # One bad race shouldn't stop the season. Log it and move on.
            log.exception("Round %d: %s FAILED", round_number, name)
            summary.failed.append((round_number, name, f"{type(exc).__name__}: {exc}"))
            continue

        summary.loaded.append((round_number, name, lap_count))
        for f in empty_fields:
            summary.empty_fields.setdefault(f, []).append(round_number)
        log.info("Round %d: %s - stored %d laps", round_number, name, lap_count)

    con.close()
    return summary


def print_summary(season: int, s: RunSummary) -> None:
    print()
    print(f"===== Ingest summary: {season} =====")

    print(f"\nLoaded this run: {len(s.loaded)}")
    for rnd, name, laps in s.loaded:
        print(f"  R{rnd:02d} {name:<28} {laps:>5} laps")

    print(f"\nSkipped (already in DB): {len(s.skipped_existing)}")
    for rnd, name in s.skipped_existing:
        print(f"  R{rnd:02d} {name}")

    print(f"\nSkipped (not raced yet): {len(s.skipped_future)}")
    for rnd, name in s.skipped_future:
        print(f"  R{rnd:02d} {name}")

    print(f"\nFailed: {len(s.failed)}")
    for rnd, name, err in s.failed:
        print(f"  R{rnd:02d} {name}: {err}")

    if s.empty_fields:
        print("\nFields that came back empty (missing or all null):")
        for f, rounds in sorted(s.empty_fields.items()):
            print(f"  {f:<28} rounds {', '.join(str(r) for r in rounds)}")
    elif s.loaded:
        print("\nNo empty fields in races loaded this run.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest F1 race data into DuckDB.")
    parser.add_argument("--season", type=int, required=True, help="e.g. 2026")
    args = parser.parse_args()

    # Windows consoles can choke on names like "São Paulo"; replace instead of crash.
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    # .configure() is the current API; .enable_cache() is deprecated since 3.9.
    CACHE_DIR.mkdir(exist_ok=True)
    fastf1.Cache.configure(cache_dir=str(CACHE_DIR))
    # FastF1 is very chatty at INFO. It has its own console handler, so show
    # only its warnings, and stop them also bubbling up to our handler (which
    # would print every FastF1 INFO line and every warning twice).
    fastf1.set_log_level("WARNING")
    logging.getLogger("fastf1").propagate = False

    summary = ingest_season(args.season)
    print_summary(args.season, summary)


if __name__ == "__main__":
    main()
