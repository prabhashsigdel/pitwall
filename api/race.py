"""Builds the per race responses from the cached tables and models.

Every number is taken from the same tables / model parameters the Streamlit
Race page uses (dashboard/pages/1_Race.py); nothing is recalculated differently.
"""

import math

import numpy as np
import pandas as pd

import config
import queries
from api import schemas

DRY = queries.DRY


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def num(v) -> float | None:
    """pandas / numpy value -> float, or None for NaN / NA / None / inf."""
    if v is None or v is pd.NA:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def whole(v) -> int | None:
    f = num(v)
    return None if f is None else int(f)


def flag(v) -> bool | None:
    """Nullable boolean column value -> bool or None."""
    return None if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)) else bool(v)


def text(v) -> str | None:
    return v if isinstance(v, str) and v else None


def iso_date(v) -> str:
    """Race date as YYYY-MM-DD (DuckDB DATE comes back as a pandas Timestamp)."""
    return pd.Timestamp(v).date().isoformat()


def team(name, colors: dict[str, str]) -> schemas.Team:
    name = name if isinstance(name, str) else "UNKNOWN"
    return schemas.Team(name=name, colour=colors.get(name, colors["UNKNOWN"]))


def race_laps(t: dict, rnd: int) -> pd.DataFrame:
    return t["laps"][t["laps"]["round"] == rnd]


def effects_row(t: dict, rnd: int) -> pd.Series | None:
    e = queries.season_rows(t["race_effects"])
    e = e[e["round"] == rnd] if not e.empty else e
    return None if e.empty else e.iloc[0]


def red_flag_laps(laps: pd.DataFrame) -> list[int]:
    """One lap per red flag: the last lap run under it by the leader (the highest lap
    number in that red period). That's where the free tyre change happens."""
    red = laps[laps["red"].astype(bool)]
    return sorted(int(v) for v in red.groupby("segment")["lap_number"].max())


# ---------------------------------------------------------------------------
# /api/races and /api/races/{round}
# ---------------------------------------------------------------------------
def summaries(t: dict) -> list[schemas.RaceSummary]:
    races = queries.season_rows(t["races"]).sort_values("round")
    out = []
    for r in races.itertuples():
        e = effects_row(t, r.round)
        out.append(schemas.RaceSummary(
            round=int(r.round), event_name=r.event_name, circuit=r.circuit, date=iso_date(r.date),
            is_reliable=None if e is None else bool(e["is_reliable"]),
            fuel_source=None if e is None else text(e["fuel_source"]),
            red_flag_laps=red_flag_laps(race_laps(t, r.round))))
    return out


def _mean_temp(weather: pd.DataFrame, start: float | None, end: float | None) -> float | None:
    """Average track temp of the weather samples between two session times. If no sample
    falls inside (short window), the sample closest to the middle of it."""
    if weather.empty or start is None or end is None:
        return None
    inside = weather[(weather["session_time"] >= start) & (weather["session_time"] <= end)]
    if not inside.empty:
        return num(inside["track_temp"].mean())
    mid = (start + end) / 2
    return num(weather.loc[(weather["session_time"] - mid).abs().idxmin(), "track_temp"])


def track_temps(t: dict, rnd: int) -> schemas.TrackTemp:
    """Average track temperature over the first 10 and the last 10 laps of the race
    (lap times from the leader: first car to start lap N)."""
    laps = race_laps(t, rnd)
    w = t["weather"][t["weather"]["round"] == rnd].dropna(subset=["track_temp", "session_time"])
    if laps.empty:
        return schemas.TrackTemp(first_10=None, last_10=None)
    total = int(laps["lap_number"].max())
    starts = laps.groupby("lap_number")["lap_start_time"].min()
    race_end = num((laps["lap_start_time"] + laps["lap_time"]).max())

    def start_of(lap: int) -> float | None:
        return num(starts.get(lap)) if lap in starts.index else None

    first_end = start_of(11) if total > 10 else race_end
    return schemas.TrackTemp(first_10=_mean_temp(w, start_of(1), first_end),
                             last_10=_mean_temp(w, start_of(max(1, total - 9)), race_end))


def strategy_summary(row) -> str:
    """'M27-S18-S26 · 1 stop' (+ red flag tyre change)."""
    legs = "-".join(f"{c}{n}" for c, n in zip(row.compound_sequence.split("-"), row.stint_lengths.split("-")))
    stops = int(row.n_stops)
    out = f"{legs} · {stops} stop{'s' if stops != 1 else ''}"
    if int(row.red_flag_tyre_changes or 0) > 0:
        out += " + red flag tyre change"
    return out


def header(t: dict, models: dict, colors: dict, race: pd.Series) -> schemas.RaceHeader:
    rnd = int(race["round"])
    s = queries.season_rows(t["strategies"])
    win = s[(s["round"] == rnd) & (s["finish_position"] == 1)]
    winner = None
    if not win.empty:
        w = next(win.itertuples())
        winner = schemas.Winner(code=w.driver, team=team(w.team, colors), strategy=strategy_summary(w),
                                compound_sequence=w.compound_sequence, stint_lengths=w.stint_lengths,
                                stops=int(w.n_stops))
    e = effects_row(t, rnd)
    fuel = None
    if e is not None:
        estimated = e["fuel_source"] == "estimated"
        fuel = schemas.Fuel(value=num(e["fuel_track_effect_per_lap"]), source=e["fuel_source"],
                            ci_low=num(e["fuel_est_ci_low"]) if estimated else None,
                            ci_high=num(e["fuel_est_ci_high"]) if estimated else None,
                            own_estimate=None if estimated else num(e["fuel_estimated"]))
    m = models.get(rnd)
    return schemas.RaceHeader(
        round=rnd, event_name=race["event_name"], circuit=race["circuit"], country=text(race.get("country")),
        date=iso_date(race["date"]), total_laps=whole(race.get("total_laps")), winner=winner, fuel=fuel,
        r_squared=None if e is None else num(e["r_squared"]),
        clean_laps=None if e is None else whole(e["n_laps"]),
        drivers_in_model=0 if m is None else int(sum(k.startswith("driver_") for k in m.p.index)),
        track_temp=track_temps(t, rnd), red_flag_laps=red_flag_laps(race_laps(t, rnd)),
        is_reliable=None if e is None else bool(e["is_reliable"]),
        unreliable_reason=None if e is None else text(e["unreliable_reason"]))


# ---------------------------------------------------------------------------
# /api/races/{round}/strategies
# ---------------------------------------------------------------------------
def strategies(t: dict, colors: dict, rnd: int) -> schemas.Strategies:
    laps = race_laps(t, rnd)
    st_df = queries.stints(laps)
    n_laps = laps.groupby("driver").size()
    s = queries.season_rows(t["strategies"])
    s = s[s["round"] == rnd].assign(n=lambda d: d["driver"].map(n_laps).fillna(0))
    # Official finishing order; DNFs below, the ones who went furthest first.
    fin = s[s["finished"].astype(bool)].sort_values("finish_position")
    dnf = s[~s["finished"].astype(bool)].sort_values(["n", "driver"], ascending=[False, True])
    rows = []
    for r in pd.concat([fin, dnf]).itertuples():
        mine = st_df[st_df["driver"] == r.driver].sort_values("start")
        rows.append(schemas.DriverStrategy(
            position=whole(r.finish_position) if r.finished else None, code=r.driver,
            team=team(r.team, colors), status="finished" if r.finished else "DNF", laps=int(r.n),
            stops=int(r.n_stops),
            stints=[schemas.Stint(compound=x.compound, start_lap=int(x.start), end_lap=int(x.end),
                                  red_flag_change=bool(x.red_fitted)) for x in mine.itertuples()]))
    return schemas.Strategies(round=rnd, total_laps=int(laps["lap_number"].max()) if not laps.empty else 0,
                              red_flag_laps=red_flag_laps(laps), drivers=rows)


# ---------------------------------------------------------------------------
# /api/races/{round}/deg  (same maths as deg_figure in 1_Race.py)
# ---------------------------------------------------------------------------
def deg(t: dict, models: dict, rnd: int) -> schemas.Deg:
    laps = race_laps(t, rnd)
    d = queries.season_rows(t["tyre_deg"])
    d = d[d["round"] == rnd].set_index("compound") if not d.empty else d
    m = models.get(rnd)
    clean = pd.DataFrame()
    if m is not None:
        p = m.p
        clean = laps[laps["is_clean"].fillna(False).astype(bool) & laps["compound"].isin(DRY)].copy()
        clean["base"] = clean["driver"].map(lambda drv: p.get(f"driver_{drv}", np.nan))
        clean = clean.dropna(subset=["base", "tyre_life"])
        # lap time - driver baseline - segment offset - the race's fuel effect
        clean["y"] = (clean["lap_time"] - clean["base"]
                      - clean["segment"].map(lambda k: p.get(f"segment:{k}", 0.0))
                      - m.fuel * clean["lap_number"])

    ran = set(laps["compound"].dropna())
    compounds = []
    for comp in DRY:
        row = d.loc[comp] if comp in getattr(d, "index", []) else None
        sub = clean[clean["compound"] == comp] if not clean.empty else clean
        lines = {"line": None, "band_low": None, "band_high": None}
        if row is not None and num(row["deg_per_lap"]) is not None and not sub.empty:
            x0, x1 = float(sub["tyre_life"].min()), float(sub["tyre_life"].max())
            off = float(m.p.get(f"offset:{comp}", 0.0))
            for key, slope in (("line", row["deg_per_lap"]), ("band_low", row["ci_low"]),
                               ("band_high", row["ci_high"])):
                if num(slope) is not None:
                    lines[key] = schemas.Line(x_start=x0, y_start=off + slope * x0, x_end=x1, y_end=off + slope * x1)
        compounds.append(schemas.CompoundDeg(
            compound=comp, ran=comp in ran,
            deg_per_lap=None if row is None else num(row["deg_per_lap"]),
            se=None if row is None else num(row["se"]),
            ci_low=None if row is None else num(row["ci_low"]),
            ci_high=None if row is None else num(row["ci_high"]),
            n_laps=None if row is None else whole(row["n_laps"]),
            n_drivers=None if row is None else whole(row["n_drivers"]),
            low_confidence=None if row is None else flag(row["low_confidence"]), **lines))
    points = ([] if clean.empty else
              [schemas.DegPoint(driver=r.driver, compound=r.compound, tyre_age=int(r.tyre_life), value=float(r.y))
               for r in clean.sort_values(["compound", "tyre_life"]).itertuples()])
    return schemas.Deg(round=rnd, compounds=compounds, points=points)


# ---------------------------------------------------------------------------
# /api/races/{round}/battles  (same filters as the Race page)
# ---------------------------------------------------------------------------
def gap_text(attacker: str, gap) -> str:
    """Gap from the attacker's side: > 0 = attacker behind."""
    g = num(gap)
    if g is None:
        return "–"
    if abs(g) < 0.005:
        return f"{attacker} level"
    return f"{attacker} {abs(g):.2f}s {'behind' if g > 0 else 'ahead'}"


def breakdown(r) -> schemas.Breakdown | None:
    parts = {"pit_loss": num(r.pit_loss_diff), "tyre_gain": num(r.expected_tyre_gain),
             "car_pace": num(getattr(r, "car_pace_gain", None)), "unexplained": num(r.unexplained)}
    if all(v is None for v in parts.values()):
        return None
    total = sum(parts.values()) if all(v is not None for v in parts.values()) else None
    return schemas.Breakdown(**parts, total=total)


def battles(t: dict, colors: dict, rnd: int, hide_sc: bool, hide_teammates: bool,
            hide_early: bool) -> schemas.Battles:
    b = queries.season_rows(t["battles"])
    b = b[b["round"] == rnd] if not b.empty else b
    total = len(b)
    if not b.empty:
        if hide_sc:
            b = b[~b["sc_vsc_in_window"].astype(bool)]
        if hide_teammates:
            b = b[~b["teammates"].astype(bool)]
        if hide_early and "early_stop" in b.columns:
            b = b[~b["early_stop"].astype(bool)]
    rows = []
    for r in b.itertuples():
        result = queries.RESULT_WORDS.get(r.result, queries.NOT_JUDGED)
        rows.append(schemas.Battle(
            attacker=r.a_driver, attacker_team=team(r.a_team, colors),
            defender=r.b_driver, defender_team=team(r.b_team, colors),
            attacker_pit_lap=int(r.a_in_lap), defender_pit_lap=whole(r.b_in_lap),
            type=queries.TYPE_WORDS.get(r.battle_type, r.battle_type), type_code=r.battle_type,
            result=result, result_code=text(r.result), tone=queries.result_tone(result),
            gap_before=num(r.gap_before), gap_after=num(r.gap_after),
            gap_before_text=gap_text(r.a_driver, r.gap_before), gap_after_text=gap_text(r.a_driver, r.gap_after),
            gained=num(r.gap_change), stuck=flag(r.stuck), gap_trend=text(r.gap_trend),
            sc_window=bool(r.sc_vsc_in_window), teammates=bool(r.teammates),
            early_stop=bool(getattr(r, "early_stop", False)), breakdown=breakdown(r),
            notes=[n for n in (text(getattr(r, c, None)) for c in ("expected_gain_note", "car_pace_note", "pit_note"))
                   if n]))
    return schemas.Battles(round=rnd, total=total, battles=rows)


# ---------------------------------------------------------------------------
# /api/races/{round}/traffic
# ---------------------------------------------------------------------------
CAVEAT = "Being ahead on time doesn't guarantee finishing ahead: rivals would have raced differently."


def traffic(tr: dict) -> schemas.Traffic:
    return schemas.Traffic(
        round=tr["round"], driver=tr["driver"], blocker=tr["blocker"], reliable=bool(tr["reliable"]),
        total_lost=tr["total_lost"], uncertainty=tr["uncertainty"],
        per_lap=[schemas.TrafficLap(**{**u, "ahead": text(u["ahead"]), "gap_ahead_s": num(u["gap_ahead_s"])})
                 for u in tr["per_lap"]],
        skipped=[schemas.SkippedLap(**s) for s in tr["skipped"]],
        actual_pos=tr["actual_pos"], new_pos=tr["new_pos"], car_ahead=tr["car_ahead"],
        gap_to_car_ahead=num(tr["gap_to_car_ahead"]), traffic_gap_s=config.TRAFFIC_GAP_S, caveat=CAVEAT)
