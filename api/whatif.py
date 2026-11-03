"""POST /api/sim/whatif: checks the request, runs sim.run_whatif, shapes the result.

Same rules as the Strategy Sim page: races marked unreliable are switched off,
and every rule break in sim.run_whatif comes back as its readable message.
"""

import numpy as np
import pandas as pd

import queries
from api import schemas
from api.race import effects_row, team

COMPOUNDS = {"S": "SOFT", "M": "MEDIUM", "H": "HARD", "SOFT": "SOFT", "MEDIUM": "MEDIUM", "HARD": "HARD"}


class Refused(ValueError):
    """A what-if that can't be run; the message is shown to the user as is."""


def compound(name: str) -> str:
    c = COMPOUNDS.get(name.strip().upper())
    if c is None:
        raise Refused(f"Unknown compound '{name}'. Use SOFT, MEDIUM or HARD (or S, M, H).")
    return c


def check(t: dict, req: schemas.WhatIfRequest) -> tuple[str, list[tuple[str, int]], str | None]:
    """Normalised (driver, plan, red_flag_compound), or Refused with a readable reason."""
    e = effects_row(t, req.round)
    if e is None:
        raise Refused(f"Round {req.round} has no race model.")
    if not bool(e["is_reliable"]):
        raise Refused(f"Round {req.round} is marked unreliable ({e['unreliable_reason']}), "
                      "so the strategy simulator is switched off for it.")
    if not req.stints:
        raise Refused("Add at least one stint.")
    plan = [(compound(s.compound), s.laps) for s in req.stints]
    red_comp = compound(req.red_flag_compound) if req.red_flag_compound else None
    return req.driver.strip().upper(), plan, red_comp


def plan_rows(plan) -> list[schemas.PlanStint]:
    return [schemas.PlanStint(compound=c, laps=int(n), start_age=int(age), red_flag_change=bool(free))
            for c, n, age, free in plan]


def finishing_order(r: dict, t: dict, colors: dict) -> list[schemas.OrderRow]:
    """Everyone's official time plus the what-if driver's new time, ordered like the
    Strategy Sim page tower (more laps first, then time)."""
    o = pd.concat([r["others"].assign(whatif=False),
                   pd.DataFrame([{"driver": r["driver"], "laps": r["laps"], "time": r["whatif_time"],
                                  "whatif": True}])], ignore_index=True)
    o = o.sort_values(["laps", "time"], ascending=[False, True]).reset_index(drop=True)
    lead_t, lead_n = o.loc[0, "time"], o.loc[0, "laps"]
    s = queries.season_rows(t["strategies"])
    s = s[s["round"] == r["round"]]
    teams = dict(zip(s["driver"], s["team"]))

    def gap(i: int, row) -> str:
        if np.isinf(row.time):
            return "DNF"
        if row.laps < lead_n:
            return f"+{int(lead_n - row.laps)} lap(s)"
        return "leader" if i == 0 else f"+{row.time - lead_t:.1f}s"

    return [schemas.OrderRow(position=i + 1, driver=row.driver, team=team(teams.get(row.driver), colors),
                             gap=gap(i, row), whatif=bool(row.whatif))
            for i, row in enumerate(o.itertuples())]


def response(r: dict, t: dict, colors: dict) -> schemas.WhatIf:
    races = queries.season_rows(t["races"])
    warn = queries.sim_reliability(queries.season_rows(t["sim_replay"]), races).get(r["round"])
    red = r["red"]
    return schemas.WhatIf(
        round=r["round"], driver=r["driver"], reliable=bool(r["reliable"]), sim_warning=warn,
        delta=r["delta"], actual_time=r["actual_time"], whatif_time=r["whatif_time"],
        actual_pos=r["actual_pos"], pred_pos=r["pred_pos"], laps=r["laps"],
        real_plan=plan_rows(r["real_plan"]), whatif_plan=plan_rows(r["whatif_plan"]),
        pit_loss=schemas.PitLoss(**r["loss"], n_green_stops=r["n_green_stops"]),
        red=None if red is None else schemas.RedFlag(**red),
        notes=list(r["notes"]), finishing_order=finishing_order(r, t, colors))
