"""Shared data loading for the Streamlit dashboard AND the FastAPI backend.

No Streamlit and no web code here: plain functions that return pandas data or
the dicts from sim.py / traffic.py. Each caller adds its own caching
(Streamlit: st.cache_*, API: api/cache.py).

DATABASE RULE: no connection is ever kept open. Every read opens the DuckDB
file READ-ONLY, loads what it needs and closes it straight away, so the
pipeline scripts (ingest, clean, deg, battles, sim) can always write to it.
If a script is writing right now, duckdb raises duckdb.IOException: callers
turn that into a readable "database busy" message.
"""

from contextlib import contextmanager

import duckdb
import pandas as pd

import db
import sim
import traffic as traffic_mod

DB_PATH = db.DB_PATH
SEASON = sim.SEASON
DRY = ["SOFT", "MEDIUM", "HARD"]

BUSY_MESSAGE = ("The database is busy (a pipeline script is writing to it). "
                "Wait for it to finish, then try again.")

# Tracks where cars can't pass: the sim's "no traffic" assumption breaks down.
PROCESSIONAL = {"Monte Carlo"}
# Replay mean absolute error above this = sim unreliable for that race. (Not rank
# correlation: bunched races get a low one even when the error is tiny.)
SIM_MAX_MAE_S = 15.0

# Every table the dashboard pages read.
TABLES = {
    "races": "SELECT * FROM races",
    "race_effects": "SELECT * FROM race_effects",
    "tyre_deg": "SELECT * FROM tyre_deg",
    "team_deg_season": "SELECT * FROM team_deg_season",
    "teammate_deg_season": "SELECT * FROM teammate_deg_season",
    "battles": "SELECT * FROM battles",
    "strategies": "SELECT * FROM strategies",
    "sim_replay": "SELECT * FROM sim_replay",
    "sc_odds": "SELECT * FROM sc_circuit_odds",
    # for battle outliers (src/outliers.py)
    "race_results": "SELECT * FROM race_results",
    "race_control": "SELECT season, round, lap, message FROM race_control",
    "cliff_flags": "SELECT * FROM cliff_flags",
}

# Battle labels in plain words (battles.py stores codes).
TYPE_WORDS = {"undercut_window": "Undercut window", "offset": "Offset",
              "no_response": "No response", "red_flag_response": "Red flag response"}
RESULT_WORDS = {"undercut_success": "Undercut worked", "undercut_failed": "Undercut failed",
                "cover_success": "Cover worked", "overcut_success": "Overcut worked",
                "later_passed": "Later stopper passed", "later_gained": "Later stopper gained",
                "later_lost": "Later stopper lost"}
NOT_JUDGED = "Not judged"


def result_tone(words: str) -> str:
    """good / bad / neutral for a RESULT_WORDS value (from the attacker's side)."""
    if any(w in words for w in ("worked", "gained", "passed")):
        return "good"
    if any(w in words for w in ("failed", "lost")):
        return "bad"
    return "neutral"


# ---------------------------------------------------------------------------
# Database access
# ---------------------------------------------------------------------------
@contextmanager
def read_only():
    """Open the database read-only and ALWAYS close it, even on errors.
    Raises duckdb.IOException if a pipeline script holds the write lock."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        yield con
    finally:
        con.close()


def db_signature() -> tuple:
    """Changes whenever a pipeline script writes the database (file + WAL mtimes).
    The API uses it to drop its cache automatically."""
    wal = DB_PATH.with_name(DB_PATH.name + ".wal")
    return tuple(p.stat().st_mtime_ns if p.exists() else 0 for p in (DB_PATH, wal))


def load_all() -> dict[str, pd.DataFrame]:
    """Every table in TABLES plus the sim's per lap table (incl. segment, is_clean)."""
    out = {}
    with read_only() as con:
        for name, sql in TABLES.items():
            try:
                out[name] = con.execute(sql).df()
            except duckdb.CatalogException:  # that pipeline step hasn't been run yet
                out[name] = pd.DataFrame()
        out["laps"] = con.execute(sim.LAPS_SQL, [SEASON]).df()
        out["weather"] = con.execute("SELECT * FROM weather WHERE season = ?", [SEASON]).df()
    return out


def load_models() -> dict:
    """Per race field models (same fit as deg.py), via sim.load_models."""
    with read_only() as con:
        return sim.load_models(con, SEASON)


def run_whatif(models: dict, rnd: int, driver: str, plan: list[tuple[str, int]],
               red_flag_change: bool = True, red_flag_compound: str | None = None) -> dict:
    """sim.run_whatif with its own short read-only connection.
    Raises sim.WhatIfError on rule breaks (readable message)."""
    with read_only() as con:
        return sim.run_whatif(con, models, rnd, driver, plan, red_flag_change, red_flag_compound)


def traffic(models: dict, rnd: int, driver: str, blocker: str | None) -> dict:
    """Time lost in traffic (traffic.traffic_cost). Raises traffic.TrafficError."""
    with read_only() as con:
        return traffic_mod.traffic_cost(rnd, driver, blocker, con=con, models=models)


def season_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["season"] == SEASON] if "season" in df.columns else df


def sim_reliability(sim_replay: pd.DataFrame, races: pd.DataFrame) -> dict[int, str | None]:
    """round -> why the sim is unreliable there, or None if it's fine."""
    mae = (sim_replay.groupby("round")["error_s"].apply(lambda e: e.abs().mean())
           if not sim_replay.empty else pd.Series(dtype=float))
    out = {}
    for r in races.itertuples():
        if r.circuit in PROCESSIONAL:
            out[r.round] = "processional track: no overtaking means traffic decides, not strategy"
        elif r.round in mae.index and mae[r.round] > SIM_MAX_MAE_S:
            out[r.round] = f"replay error {mae[r.round]:.1f}s is over {SIM_MAX_MAE_S:.0f}s"
        else:
            out[r.round] = None
    return out


# ---------------------------------------------------------------------------
# Stints (one row per driver per stint), shared by the stint chart and the API
# ---------------------------------------------------------------------------
def stints(race_laps: pd.DataFrame) -> pd.DataFrame:
    """driver, stint, start, end, compound, age, red_fitted.
    red_fitted = tyres fitted during a red flag (the PREVIOUS stint ended under one)."""
    ordered = race_laps.sort_values("lap_number")
    st_df = (ordered.groupby(["driver", "stint"])
             .agg(start=("lap_number", "min"), end=("lap_number", "max"),
                  compound=("compound", lambda s: s.mode().iloc[0] if s.notna().any() else "UNKNOWN"),
                  age=("tyre_life", "first"), ended_red=("red", "last"))
             .reset_index())
    st_df["red_fitted"] = st_df.groupby("driver")["ended_red"].shift(fill_value=False).astype(bool)
    st_df["length"] = st_df["end"] - st_df["start"] + 1
    return st_df


# ---------------------------------------------------------------------------
# Team colours
# ---------------------------------------------------------------------------
GREY = "#8A8A8A"
SURFACE = "#15151E"  # card background the colours are drawn on
MIN_CONTRAST = 3.0   # WCAG minimum for graphics against their background
FALLBACK_TEAM_COLORS = {
    "Mercedes": "#27F4D2", "Red Bull Racing": "#3671C6", "Ferrari": "#E8002D",
    "McLaren": "#FF8000", "Aston Martin": "#229971", "Alpine": "#FF87BC",
    "Williams": "#64C4FF", "Racing Bulls": "#6692FF", "Haas F1 Team": "#B6BABD",
    "Audi": "#F2F2F2", "Cadillac": "#C9A227", "UNKNOWN": GREY,
}


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _luminance(hex_color: str) -> float:
    """WCAG relative luminance."""
    def chan(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable(hex_color: str, background: str = SURFACE) -> str:
    """Mix a colour with white in small steps until it reaches MIN_CONTRAST on the
    background. Colours that already pass come back unchanged."""
    r, g, b = _rgb(hex_color)
    for step in range(21):  # 0%, 5%, ... 100% white
        t = step / 20
        mixed = "#{:02X}{:02X}{:02X}".format(*(round(c + (255 - c) * t) for c in (r, g, b)))
        if contrast(mixed, background) >= MIN_CONTRAST:
            return mixed
    return "#FFFFFF"


def fastf1_team_colors(teams: tuple[str, ...]) -> dict[str, str]:
    """Team colours from fastf1.plotting (needs a session, loaded light from cache).
    Teams fastf1 doesn't know, or no cache / offline: left out (fallback map)."""
    colors = {}
    try:
        import fastf1
        import fastf1.plotting
        fastf1.Cache.configure(cache_dir=str(db.PROJECT_ROOT / "cache"))
        fastf1.set_log_level("ERROR")
        session = fastf1.get_session(SEASON, 1, "R")
        session.load(laps=False, telemetry=False, weather=False, messages=False)
        for team in teams:
            try:
                colors[team] = fastf1.plotting.get_team_color(team, session=session)
            except Exception:  # team not known to fastf1
                pass
    except Exception:  # no cache / offline
        pass
    return colors


def team_colors(teams, ff1_colors: dict[str, str]) -> dict[str, str]:
    """team -> colour, readable on SURFACE. ff1_colors = fastf1_team_colors(...) (cached
    by the caller). Always has an "UNKNOWN" entry (e.g. HIR, who has no team in the data)."""
    teams = sorted({t for t in teams if isinstance(t, str)})
    colors = {**FALLBACK_TEAM_COLORS, **ff1_colors}
    colors["UNKNOWN"] = GREY
    return {t: readable(colors.get(t, GREY)) for t in teams + ["UNKNOWN"]}
