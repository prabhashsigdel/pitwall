"""Battle outliers: battles whose result says more about something else than about strategy.

A battle is an outlier when, in its window, any of these happened:
  * the gap changed by more than config.OUTLIER_GAIN_S (a stop gone badly wrong, damage, ...)
  * either car retired
  * ONE of the two cars got lapped by the leader and the other didn't (blue flags cost
    the lapped car time; when both get lapped the losses roughly cancel)
  * either car served a penalty (penalty served at a stop, drive through, stop and go)
  * either car ran a stint deg.py flagged as probable_damage

Window = from the first of the two pit laps to the judged lap + config.STICK_LAPS
(the last lap the result is looked at). Nothing here changes battles.py's numbers;
it only adds an outlier flag and a readable reason.
"""

import numpy as np
import pandas as pd

import config

# Race control messages that mean a penalty was served on track / in the pits.
SERVED_PATTERN = r"PENALTY SERVED|DRIVE THROUGH PENALTY FOR|STOP AND GO PENALTY FOR|STOP/GO PENALTY FOR"


def window(b: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(first, last) lap of each battle's window."""
    b_in = b["b_in_lap"].astype(float)
    a_in = b["a_in_lap"].astype(float)
    first = np.fmin(a_in, b_in)  # fmin ignores a missing defender stop
    judged = b["judged_lap"].astype(float).fillna(np.fmax(a_in, b_in))
    return first, judged + config.STICK_LAPS


def laps_down(race_laps: pd.DataFrame) -> pd.DataFrame:
    """driver, lap_number, down = how many laps behind the leader at the start of that lap
    (the leader had already started lap N + k when this car started lap N)."""
    leader = race_laps.groupby("lap_number")["lap_start_time"].min().sort_index()
    starts = leader.to_numpy()
    lap_of = leader.index.to_numpy()
    d = race_laps[["driver", "lap_number", "lap_start_time"]].dropna()
    # number of leader lap starts before this car's start = the leader's lap at that moment
    leader_lap = lap_of[np.clip(np.searchsorted(starts, d["lap_start_time"].to_numpy(), side="left") - 1,
                                0, len(lap_of) - 1)]
    return d.assign(down=np.maximum(0, leader_lap - d["lap_number"].to_numpy()))


def _retired(results: pd.DataFrame, last_lap: dict, driver: str, last: float) -> bool:
    r = results[results["driver"] == driver]
    if r.empty or r["status"].iloc[0] in ("Finished", "Lapped"):
        return False
    return last_lap.get(driver, 0) <= last


def _lapped(down: pd.DataFrame, driver: str, first: float, last: float) -> bool:
    d = down[(down["driver"] == driver) & (down["lap_number"] >= first) & (down["lap_number"] <= last)]
    return not d.empty and d["down"].max() > d["down"].min()


def _served_penalty(penalties: pd.DataFrame, driver: str, first: float, last: float) -> bool:
    m = penalties[(penalties["lap"] >= first) & (penalties["lap"] <= last)]
    return bool(m["message"].str.contains(f"({driver})", regex=False).any())


def _damaged(damage: pd.DataFrame, stints: pd.DataFrame, driver: str, first: float, last: float) -> bool:
    bad = set(damage.loc[damage["driver"] == driver, "stint"])
    s = stints[(stints["driver"] == driver) & stints["stint"].isin(bad)]
    return bool(((s["start"] <= last) & (s["end"] >= first)).any())


def other(row, driver: str) -> str:
    return row["b_driver"] if driver == row["a_driver"] else row["a_driver"]


def flag_outliers(battles: pd.DataFrame, race_laps: pd.DataFrame, results: pd.DataFrame,
                  race_control: pd.DataFrame, cliffs: pd.DataFrame) -> pd.DataFrame:
    """A copy of one race's battles with `outlier` (bool) and `outlier_reason` (str | None).

    race_laps: sim.LAPS_SQL rows for the race. results / race_control / cliffs: that race's
    race_results, race_control and cliff_flags rows (may be empty)."""
    out = battles.copy()
    if out.empty:
        return out.assign(outlier=pd.Series(dtype=bool), outlier_reason=pd.Series(dtype=object))
    first, last = window(out)
    down = laps_down(race_laps)
    last_lap = race_laps.groupby("driver")["lap_number"].max().to_dict()
    stints = (race_laps.groupby(["driver", "stint"])["lap_number"].agg(start="min", end="max").reset_index())
    penalties = (race_control[race_control["message"].str.contains(SERVED_PATTERN, case=False, regex=True, na=False)]
                 if not race_control.empty else pd.DataFrame(columns=["lap", "message"]))
    damage = (cliffs[cliffs["kind"] == "probable_damage"] if not cliffs.empty
              else pd.DataFrame(columns=["driver", "stint"]))

    reasons = []
    for (_, row), f, la in zip(out.iterrows(), first, last):
        why = []
        gained = row["gap_change"]
        if pd.notna(gained) and abs(gained) > config.OUTLIER_GAIN_S:
            why.append(f"gap changed {abs(gained):.1f}s (over {config.OUTLIER_GAIN_S:.0f}s)")
        for drv in (row["a_driver"], row["b_driver"]):
            if _retired(results, last_lap, drv, la):
                why.append(f"{drv} retired")
            if _lapped(down, drv, f, la) and not _lapped(down, other(row, drv), f, la):
                why.append(f"{drv} got lapped")
            if _served_penalty(penalties, drv, f, la):
                why.append(f"{drv} served a penalty")
            if _damaged(damage, stints, drv, f, la):
                why.append(f"{drv} probable damage")
        # battles.py's own flag: a car retired before the result could be judged.
        if pd.notna(row.get("dnf_in_window")) and bool(row.get("dnf_in_window")) \
                and not any("retired" in w for w in why):
            why.append("a car retired before the result")
        reasons.append("; ".join(dict.fromkeys(why)) or None)
    return out.assign(outlier=[r is not None for r in reasons], outlier_reason=reasons)
