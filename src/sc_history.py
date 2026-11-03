"""Safety car / VSC / red flag history per circuit (light ingest, no laps).

Usage:
    python src/sc_history.py --seasons 2022 2023 2024 2025 2026

Per race we load only:
  * session info (circuit key) + race control messages:
        session.load(laps=False, telemetry=False, weather=False, messages=True)
  * track status + total lap count, straight from fastf1.api. With load(),
    these only come with laps=True, which also parses all the timing data.
Events come from race control messages because those carry LAP numbers.
Track status (session clock, no laps) is used as a cross-check on the counts.

Resumable: a race is done once it's in sc_race_summary. Future races are skipped.
"""

import argparse
import logging
import re
import sys
import warnings

import fastf1
import pandas as pd

import db
import ingest  # reuse cache dir, race start lookup and the "has it happened yet" delay

with warnings.catch_warnings():  # fastf1.api warns it may become private; we accept that
    warnings.simplefilter("ignore", UserWarning)
    import fastf1.api as f1api

log = logging.getLogger("sc_history")

# Circuits are matched by FastF1's numeric circuit key (session_info Meeting.Circuit.Key),
# never by event name. Aliases map an old key to the key we treat as the same track.
# Filled in after checking the data (see print_circuit_names): currently none needed.
CIRCUIT_ALIASES: dict[int, int] = {}

# Message patterns: (kind, deployed, ended). Old seasons spell out "VIRTUAL SAFETY CAR".
PATTERNS = [
    ("SC", r"^SAFETY CAR DEPLOYED", r"^SAFETY CAR IN THIS LAP"),
    ("VSC", r"^(?:VSC|VIRTUAL SAFETY CAR) DEPLOYED", r"^(?:VSC|VIRTUAL SAFETY CAR) ENDING"),
    # A red flag "ends" at the restart (standing / rolling start, or pit exit open).
    ("RED", r"^RED FLAG", r"^(?:STANDING START|ROLLING START|GREEN LIGHT - PIT EXIT OPEN)"),
]
# e.g. "FORMATION LAP WILL BE STARTED BEHIND THE SAFETY CAR", "... BEHIND SAFETY CAR",
# "SAFETY CAR WILL ENTER PITS: ROLLING START" (Monaco 2022)
BEHIND_SC_RE = r"BEHIND (?:THE )?SAFETY CAR|ROLLING START"
TRACK_STATUS_CODES = {"SC": "4", "RED": "5", "VSC": "6"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sc_events (
    season INTEGER, round INTEGER, circuit_key INTEGER, circuit_name VARCHAR,
    event_name VARCHAR, kind VARCHAR, start_lap INTEGER, end_lap INTEGER, total_laps INTEGER
);
CREATE TABLE IF NOT EXISTS sc_race_summary (
    season INTEGER, round INTEGER, circuit_key INTEGER, circuit_name VARCHAR,
    event_name VARCHAR, total_laps INTEGER,
    n_sc INTEGER, n_vsc INTEGER, n_red INTEGER,
    had_sc BOOLEAN, had_vsc BOOLEAN, had_red BOOLEAN, had_lap1_sc BOOLEAN,
    started_behind_sc BOOLEAN,
    track_status_mismatch VARCHAR,  -- counts that disagree with track status, else NULL
    PRIMARY KEY (season, round)
);
"""

ODDS_VIEW = """
CREATE OR REPLACE VIEW sc_circuit_odds AS
SELECT circuit_key,
       arg_max(circuit_name, season)                       AS circuit_name,
       count(*)                                            AS races,
       avg(had_sc::INT)                                    AS pct_sc,
       avg(had_vsc::INT)                                   AS pct_vsc,
       avg(had_red::INT)                                   AS pct_red,
       avg((had_sc OR had_vsc OR had_red)::INT)            AS pct_any,
       avg(n_sc)                                           AS avg_sc,
       avg(n_vsc)                                          AS avg_vsc,
       count(*) < 3                                        AS low_data
FROM sc_race_summary
GROUP BY circuit_key
"""


# ---------------------------------------------------------------------------
# Parsing one race
# ---------------------------------------------------------------------------
def parse_events(msgs: pd.DataFrame, start_utc: pd.Timestamp) -> tuple[list[dict], bool]:
    """Turn race control messages into deployments. Returns (events, started_behind_sc).

    Not counted: deployments before the scheduled race start (formation lap
    delays, a start behind the SC) and, if the race started behind the SC,
    that first SC on lap 1."""
    events, open_ = [], {}  # open_: kind -> index of the deployment not yet ended
    # Only laps 1-2 (a rolling start shows up on lap 2): a "ROLLING START"
    # after a mid-race red flag isn't the race start.
    lap1 = msgs[msgs["Lap"].fillna(0) <= 2]
    started_behind = bool(lap1["Message"].str.upper().str.contains(BEHIND_SC_RE).any())
    for m in msgs.sort_values("Time").itertuples():
        text, lap = str(m.Message).upper(), None if pd.isna(m.Lap) else int(m.Lap)
        if re.match(r"^RED FLAG", text):  # an SC / VSC that turns into a red flag ends here
            for k in ("SC", "VSC"):
                if k in open_:
                    events[open_.pop(k)]["end_lap"] = lap
        for kind, deployed, ended in PATTERNS:
            if kind in open_ and re.match(ended, text):
                events[open_.pop(kind)]["end_lap"] = lap
            elif re.match(deployed, text) and kind not in open_:  # repeats = same deployment
                pre_race = pd.notna(m.Time) and m.Time < start_utc
                started_behind |= pre_race and kind == "SC"
                open_[kind] = len(events)
                events.append({"kind": kind, "start_lap": lap, "end_lap": None, "pre_race": pre_race})

    kept = [e for e in events if not e["pre_race"]]
    if started_behind:
        # The SC that led the start isn't a deployment...
        first_sc = next((e for e in kept if e["kind"] == "SC"), None)
        if first_sc is not None and (first_sc["start_lap"] or 0) <= 1:
            kept.remove(first_sc)
        # ...and a lap-1 red flag in such a race is a pre-start delay (rain),
        # not a race red flag. In a normal standing start it would be kept.
        kept = [e for e in kept if not (e["kind"] == "RED" and (e["start_lap"] or 0) <= 1)]
    return kept, started_behind


def track_status_counts(api_path: str) -> dict[str, int]:
    """How many times each of SC / VSC / red started, from the track status feed."""
    status = f1api.track_status_data(api_path)["Status"]
    counts = {k: 0 for k in TRACK_STATUS_CODES}
    prev = None
    for s in status:
        for kind, code in TRACK_STATUS_CODES.items():
            if s == code and prev != code:
                counts[kind] += 1
        prev = s
    return counts


def total_laps(api_path: str, msgs: pd.DataFrame) -> int | None:
    """Scheduled race laps (last non-null TotalLaps), else the highest message lap."""
    try:
        counts = [c for c in f1api.lap_count(api_path)["TotalLaps"] if c is not None]
        if counts:
            return int(counts[-1])
    except Exception as exc:  # lap count feed missing for some races
        log.warning("  no lap count feed: %s", exc)
    return int(msgs["Lap"].max()) if msgs["Lap"].notna().any() else None


def load_race(season: int, event: pd.Series) -> tuple[dict, list[dict]]:
    session = fastf1.get_session(season, int(event["RoundNumber"]), "R")
    session.load(laps=False, telemetry=False, weather=False, messages=True)
    info = session.session_info
    circuit = info["Meeting"]["Circuit"]
    key = CIRCUIT_ALIASES.get(int(circuit["Key"]), int(circuit["Key"]))
    # Scheduled start in UTC: local start date minus the GMT offset.
    start_utc = pd.Timestamp(info["StartDate"]) - pd.Timedelta(info["GmtOffset"])

    msgs = session.race_control_messages
    events, behind = parse_events(msgs, start_utc)
    laps = total_laps(session.api_path, msgs)
    n = {k: sum(e["kind"] == k for e in events) for k in TRACK_STATUS_CODES}

    # Cross-check against track status. It also counts pre-race periods and
    # can split one deployment in two, so a mismatch is a hint, not an error.
    ts = track_status_counts(session.api_path)
    mismatch = ", ".join(f"{k}: msgs {n[k]} vs track {ts[k]}" for k in n if n[k] != ts[k]) or None

    base = {"season": season, "round": int(event["RoundNumber"]), "circuit_key": key,
            "circuit_name": circuit["ShortName"], "event_name": event["EventName"]}
    summary = base | {
        "total_laps": laps, "n_sc": n["SC"], "n_vsc": n["VSC"], "n_red": n["RED"],
        "had_sc": n["SC"] > 0, "had_vsc": n["VSC"] > 0, "had_red": n["RED"] > 0,
        "had_lap1_sc": any(e["kind"] == "SC" and e["start_lap"] == 1 for e in events),
        "started_behind_sc": behind, "track_status_mismatch": mismatch,
    }
    rows = [base | {"kind": e["kind"], "start_lap": e["start_lap"], "end_lap": e["end_lap"],
                    "total_laps": laps} for e in events]
    return summary, rows


# ---------------------------------------------------------------------------
# Season loop
# ---------------------------------------------------------------------------
def save_race(con, summary: dict, rows: list[dict]) -> None:
    """Both tables in one transaction; the summary row marks the race as done."""
    con.begin()
    try:
        if rows:
            ingest.insert_df(con, "sc_events", pd.DataFrame(rows))
        ingest.insert_df(con, "sc_race_summary", pd.DataFrame([summary]))
        con.commit()
    except Exception:
        con.rollback()
        raise


def ingest_season(con, season: int) -> tuple[int, int, list[str]]:
    """Returns (loaded, skipped, failures)."""
    done = {r for (r,) in con.execute(
        "SELECT round FROM sc_race_summary WHERE season = ?", [season]).fetchall()}
    cutoff = (pd.Timestamp.now(tz="UTC").tz_localize(None)
              - pd.Timedelta(hours=ingest.RACE_DATA_DELAY_HOURS))
    loaded, skipped, failed = 0, 0, []
    for _, event in fastf1.get_event_schedule(season, include_testing=False).iterrows():
        rnd, start = int(event["RoundNumber"]), ingest.race_start_utc(event)
        if rnd in done or start is None or start > cutoff:
            skipped += 1
            continue
        try:
            save_race(con, *load_race(season, event))
            loaded += 1
        except Exception as exc:
            log.exception("%d R%02d %s failed", season, rnd, event["EventName"])
            failed.append(f"{season} R{rnd:02d} {event['EventName']}: {type(exc).__name__}: {exc}")
    log.info("%d: loaded %d, skipped %d, failed %d", season, loaded, skipped, len(failed))
    return loaded, skipped, failed


# ---------------------------------------------------------------------------
# Checks and printing
# ---------------------------------------------------------------------------
def check_2026(con) -> None:
    """Every SC / VSC deployment message in the phase 1 race_control table
    should be an sc_events row (same round, kind and lap)."""
    missing = con.execute(r"""
        WITH msgs AS (
            SELECT round, lap,
                   CASE WHEN regexp_matches(upper(message), '^(VSC|VIRTUAL SAFETY CAR) DEPLOYED') THEN 'VSC'
                        WHEN regexp_matches(upper(message), '^SAFETY CAR DEPLOYED') THEN 'SC' END AS kind
            FROM race_control WHERE season = 2026
        )
        SELECT m.round, m.kind, m.lap FROM msgs m
        WHERE m.kind IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM sc_events e WHERE e.season = 2026 AND e.round = m.round
              AND e.kind = m.kind AND e.start_lap = m.lap)
        ORDER BY 1, 3
    """).fetchall()
    total = con.execute("SELECT count(*) FROM race_control WHERE season = 2026 AND "
                        "regexp_matches(upper(message), '^((VSC|VIRTUAL SAFETY CAR|SAFETY CAR) DEPLOYED)')").fetchall()[0][0]
    print(f"\n2026 check: {total - len(missing)}/{total} SC/VSC messages in race_control found in sc_events")
    for rnd, kind, lap in missing:
        print(f"  missing: R{rnd:02d} {kind} lap {lap} (pre-race or start behind SC?)")


def print_circuit_names(con) -> None:
    """Keys with more than one name, and names/locations with more than one key."""
    multi = con.execute("""
        SELECT circuit_key, string_agg(DISTINCT circuit_name, ' / '), string_agg(DISTINCT event_name, ' / ')
        FROM sc_race_summary GROUP BY 1 HAVING count(DISTINCT circuit_name) > 1
    """).fetchall()
    dupes = con.execute("""
        SELECT circuit_name, string_agg(DISTINCT circuit_key::VARCHAR, ', ')
        FROM sc_race_summary GROUP BY 1 HAVING count(DISTINCT circuit_key) > 1
    """).fetchall()
    print(f"\nAliases in code: {CIRCUIT_ALIASES or 'none'}")
    for key, names, events in multi:
        print(f"  key {key} has several names: {names} ({events})")
    for name, keys in dupes:
        print(f"  name {name} has several keys: {keys}  <- needs an alias?")
    if not multi and not dupes:
        print("  every circuit key has one name and every name one key")


def print_odds(con) -> None:
    df = con.execute("SELECT * FROM sc_circuit_odds ORDER BY pct_any DESC, races DESC").df()
    print("\nCircuit odds (sorted by % any neutralisation):")
    print(f"  {'circuit':<20}{'key':>5}{'races':>6}{'SC%':>6}{'VSC%':>6}{'RED%':>6}"
          f"{'any%':>6}{'SC/race':>8}{'VSC/race':>9}")
    for r in df.itertuples():
        low = "  low_data" if r.low_data else ""
        print(f"  {r.circuit_name:<20}{r.circuit_key:>5}{r.races:>6}{r.pct_sc:>6.0%}{r.pct_vsc:>6.0%}"
              f"{r.pct_red:>6.0%}{r.pct_any:>6.0%}{r.avg_sc:>8.2f}{r.avg_vsc:>9.2f}{low}")


def print_sanity(con) -> None:
    print("\nSanity check, race by race:")
    rows = con.execute("""
        SELECT circuit_name, season, n_sc, n_vsc, n_red, had_lap1_sc, started_behind_sc,
               track_status_mismatch
        FROM sc_race_summary
        WHERE circuit_name IN ('Monte Carlo', 'Singapore', 'Monza')
        ORDER BY circuit_name, season
    """).fetchall()
    for name, season, sc, vsc, red, lap1, behind, mism in rows:
        extra = (" lap1 SC" if lap1 else "") + (" started behind SC" if behind else "")
        extra += f"  [track status: {mism}]" if mism else ""
        print(f"  {name:<12}{season}  SC {sc}  VSC {vsc}  RED {red}{extra}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load SC/VSC/red flag history.")
    parser.add_argument("--seasons", type=int, nargs="+", required=True)
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    ingest.CACHE_DIR.mkdir(exist_ok=True)
    fastf1.Cache.configure(cache_dir=str(ingest.CACHE_DIR))
    fastf1.set_log_level("WARNING")
    logging.getLogger("fastf1").propagate = False

    con = db.connect()
    con.execute(SCHEMA)
    results = {s: ingest_season(con, s) for s in args.seasons}
    con.execute(ODDS_VIEW)

    print("\nRaces loaded per season (in DB / loaded this run):")
    in_db = dict(con.execute("SELECT season, count(*) FROM sc_race_summary GROUP BY 1").fetchall())
    for s, (loaded, _, failed) in results.items():
        print(f"  {s}: {in_db.get(s, 0)} in DB, {loaded} this run, {len(failed)} failed")
        for f in failed:
            print(f"    FAILED {f}")
    print_odds(con)
    print_sanity(con)
    if 2026 in args.seasons:
        check_2026(con)
    print_circuit_names(con)
    con.close()


if __name__ == "__main__":
    main()
