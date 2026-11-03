"""PITWALL API: the dashboard's data as JSON for the React frontend.

Run from the project root with api.bat (uvicorn on http://localhost:8000).
Interactive docs: http://localhost:8000/docs

Every endpoint reads the cached tables / models (api/cache.py), which open the
DuckDB file read-only and close it straight away. Errors always come back as
{"detail": "<readable message>"}:
    404  unknown round
    422  bad input, or a what-if / traffic request the rules refuse
    503  database busy (a pipeline script is writing) or missing
    500  anything unexpected (details in the api.bat window)
"""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # reuse the pipeline code (queries, sim, traffic)

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
from fastapi import FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import queries  # noqa: E402
import sim  # noqa: E402
import traffic as traffic_mod  # noqa: E402
from api import cache, race, schemas, whatif  # noqa: E402

log = logging.getLogger("pitwall.api")

app = FastAPI(title="PITWALL API", version="1.0",
              description="F1 race strategy data: stints, tyre degradation, pit battles, traffic, what-ifs.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_methods=["GET", "POST"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# Errors: always a readable {"detail": "..."}
# ---------------------------------------------------------------------------
@app.exception_handler(duckdb.IOException)
async def database_busy(_: Request, exc: duckdb.IOException) -> JSONResponse:
    if not queries.DB_PATH.exists():
        msg = "No database found at data/f1.duckdb. Run the pipeline scripts first (src/ingest.py ...)."
    else:
        msg = queries.BUSY_MESSAGE
    log.warning("database unavailable: %s", exc)
    return JSONResponse(status_code=503, content={"detail": msg})


@app.exception_handler(RequestValidationError)
async def bad_input(_: Request, exc: RequestValidationError) -> JSONResponse:
    """'stints.0.laps: Input should be a valid integer' instead of a nested list."""
    parts = [f"{'.'.join(str(x) for x in e['loc'] if x not in ('body', 'query'))}: {e['msg']}"
             for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "; ".join(parts) or "Invalid request."})


@app.exception_handler(Exception)
async def unexpected(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unexpected error")
    return JSONResponse(status_code=500, content={
        "detail": "Something went wrong on the server. The api.bat window has the details."})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def race_row(t: dict, rnd: int) -> pd.Series:
    """The races row for a round, or 404."""
    races = queries.season_rows(t["races"])
    row = races[races["round"] == rnd]
    if row.empty:
        loaded = ", ".join(str(r) for r in sorted(races["round"]))
        raise HTTPException(404, f"Round {rnd} isn't loaded. Loaded rounds: {loaded}.")
    return row.iloc[0]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/meta", response_model=schemas.Meta)
def meta() -> schemas.Meta:
    """Season, how many races are loaded, and the latest one."""
    races = queries.season_rows(cache.tables()["races"]).sort_values("round")
    last = None
    if not races.empty:
        r = races.iloc[-1]
        last = schemas.LastRace(round=int(r["round"]), event_name=r["event_name"], date=race.iso_date(r["date"]))
    return schemas.Meta(season=queries.SEASON, races_loaded=len(races), last_race=last)


@app.get("/api/races", response_model=list[schemas.RaceSummary])
def races() -> list[schemas.RaceSummary]:
    """Every loaded race, in round order."""
    return race.summaries(cache.tables())


@app.get("/api/races/{rnd}", response_model=schemas.RaceHeader)
def race_header(rnd: int) -> schemas.RaceHeader:
    """Header data: winner, fuel, model fit, track temp, red flags, reliability."""
    t = cache.tables()
    return race.header(t, cache.models(), cache.team_colors(), race_row(t, rnd))


@app.get("/api/races/{rnd}/strategies", response_model=schemas.Strategies)
def race_strategies(rnd: int) -> schemas.Strategies:
    """One row per driver in official finishing order (DNFs last) with their stints."""
    t = cache.tables()
    race_row(t, rnd)
    return race.strategies(t, cache.team_colors(), rnd)


@app.get("/api/races/{rnd}/deg", response_model=schemas.Deg)
def race_deg(rnd: int) -> schemas.Deg:
    """Deg per compound and the clean lap points + fitted lines for the chart."""
    t = cache.tables()
    race_row(t, rnd)
    return race.deg(t, cache.models(), rnd)


@app.get("/api/races/{rnd}/battles", response_model=schemas.Battles)
def race_battles(rnd: int,
                 hide_sc: bool = Query(True, description="hide battles with a SC/VSC in the window"),
                 hide_teammates: bool = Query(True, description="hide teammate battles"),
                 hide_early: bool = Query(True, description="hide stops before the minimum stop lap"),
                 hide_outliers: bool = Query(True, description="hide outliers (retired, lapped, penalty, "
                                                                "damage, gap change over 30s)")
                 ) -> schemas.Battles:
    """Pit stop battles with the result in plain words and the gap breakdown."""
    t = cache.tables()
    race_row(t, rnd)
    return race.battles(t, cache.team_colors(), rnd, hide_sc, hide_teammates, hide_early, hide_outliers)


@app.get("/api/races/{rnd}/traffic", response_model=schemas.Traffic)
def race_traffic(rnd: int, driver: str = Query(..., description="e.g. HAM"),
                 blocker: str | None = Query(None, description="only laps directly behind this car")
                 ) -> schemas.Traffic:
    """Time a driver lost stuck behind other cars (src/traffic.py)."""
    race_row(cache.tables(), rnd)
    blocker = blocker.strip().upper() if blocker and blocker.strip() else None
    try:
        tr = queries.traffic(cache.models(), rnd, driver.strip().upper(), blocker)
    except traffic_mod.TrafficError as exc:
        raise HTTPException(422, str(exc)) from exc
    return race.traffic(tr)


@app.post("/api/sim/whatif", response_model=schemas.WhatIf)
def sim_whatif(req: schemas.WhatIfRequest) -> schemas.WhatIf:
    """Run another strategy for one driver (src/sim.py run_whatif)."""
    t = cache.tables()
    race_row(t, req.round)
    try:
        driver, plan, red_comp = whatif.check(t, req)
        r = queries.run_whatif(cache.models(), req.round, driver, plan, req.red_flag_change, red_comp)
    except (whatif.Refused, sim.WhatIfError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return whatif.response(r, t, cache.team_colors())
