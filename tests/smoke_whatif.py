"""Smoke test: a simple valid what-if for every finisher in every reliable race.

Usage:
    python tests/smoke_whatif.py

Plan per driver: their real compounds and stint lengths, with the first stop
moved 3 laps later (taken from the second stint), clamped so every stint keeps
at least 1 lap. Single-compound strategies get a second dry compound used in
that race, since the rules require two.

Two kinds of problems are reported:
  * refused: run_whatif raised WhatIfError with a readable reason (expected for
    some drivers, e.g. no pace baseline). Fine, but listed so you can see them.
  * CRASHED: any other exception. That's a bug; the script exits 1.
"""

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import duckdb  # noqa: E402

import sim  # noqa: E402

SHIFT = 3


def smoke_plan(real: list[tuple], used: set[str]) -> list[tuple[str, int]]:
    """Real compounds + lengths, first stop SHIFT laps later, clamped to >= 1 lap."""
    plan = [[c, n] for c, n, *_ in real]
    if len(plan) >= 2:
        move = min(SHIFT, plan[1][1] - 1)  # the second stint keeps at least 1 lap
        plan[0][1] += move
        plan[1][1] -= move
    if len({c for c, _ in plan}) < 2:  # rules need 2 dry compounds: swap the last stint
        other = next(c for c in sim.DRY if c in used and c != plan[0][0])
        if len(plan) == 1:
            half = plan[0][1] // 2
            plan = [[plan[0][0], plan[0][1] - half], [other, half]]
        else:
            plan[-1][0] = other
    return [(c, n) for c, n in plan]


def main() -> int:
    con = duckdb.connect(str(ROOT / "data" / "f1.duckdb"), read_only=True)
    try:
        models = sim.load_models(con, sim.SEASON)
        laps = con.execute(sim.LAPS_SQL, [sim.SEASON]).df()
        finishers = con.execute("SELECT round, driver FROM strategies WHERE season = ? AND finished "
                                "ORDER BY round, finish_position", [sim.SEASON]).fetchall()
        ok, refused, failed = 0, [], []
        for rnd, driver in finishers:
            m = models.get(rnd)
            if m is None or not m.reliable:
                continue
            race = laps[laps["round"] == rnd]
            d = race[race["driver"] == driver].sort_values("lap_number")
            used = set(race["compound"].dropna()) & sim.DRY
            if not set(d["compound"].dropna()) <= sim.DRY:
                continue  # wet tyres in the real race: not a dry what-if
            plan = None
            try:
                plan = smoke_plan(sim.real_plan(d), used)
                sim.run_whatif(con, models, rnd, driver, plan)
                ok += 1
            except sim.WhatIfError as exc:  # a readable refusal, not a crash
                refused.append((rnd, driver, plan, str(exc)))
            except Exception as exc:  # a real crash: report it, keep going
                failed.append((rnd, driver, plan, f"{type(exc).__name__}: {exc}",
                               traceback.format_exc(limit=4)))
    finally:
        con.close()

    print(f"what-ifs run: {ok + len(refused) + len(failed)}, ok: {ok}, "
          f"refused: {len(refused)}, CRASHED: {len(failed)}")
    for rnd, driver, plan, reason in refused:
        print(f"  refused R{rnd:02d} {driver}: {reason}")
    for rnd, driver, plan, err, tb in failed:
        print(f"  CRASHED R{rnd:02d} {driver} {plan}: {err}")
        print(tb)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
