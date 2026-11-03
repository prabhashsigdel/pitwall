"""In-memory cache of the tables and fitted race models.

Loaded on first use and reloaded automatically when a pipeline script writes the
database (its file / WAL modification time changes). Loading opens the database
read-only and closes it again (queries.read_only), so nothing stays open.
"""

import threading

import queries

_lock = threading.Lock()  # FastAPI runs sync endpoints in a thread pool
_state: dict = {"sig": None, "tables": None, "models": None, "ff1": None}


def _fresh() -> dict:
    """The cached state, reloaded first if the database changed since last time."""
    sig = queries.db_signature()
    with _lock:
        if _state["sig"] != sig:
            tables = queries.load_all()   # raises duckdb.IOException if busy: cache untouched
            models = queries.load_models()
            _state.update(sig=sig, tables=tables, models=models)
        return _state


def tables() -> dict:
    return _fresh()["tables"]


def models() -> dict:
    return _fresh()["models"]


def team_colors() -> dict[str, str]:
    """team -> readable colour for every team in the season (fastf1 looked up once)."""
    teams = tuple(sorted({t for t in tables()["strategies"].get("team", []) if isinstance(t, str)}))
    with _lock:
        if _state["ff1"] is None:
            _state["ff1"] = queries.fastf1_team_colors(teams)
    return queries.team_colors(teams, _state["ff1"])
