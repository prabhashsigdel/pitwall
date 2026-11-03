"""API tests: every endpoint for rounds 11 and 19, numbers checked against the database.

Usage:
    .venv\\Scripts\\python.exe -m pytest tests/test_api.py -q

Reads data/f1.duckdb read-only (needs the pipeline to have been run).
"""

import sys
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import queries  # noqa: E402
import traffic as traffic_mod  # noqa: E402
from api import cache  # noqa: E402
from api.main import app  # noqa: E402

ROUNDS = [11, 19]
SEASON = queries.SEASON
LETTER = {"SOFT": "S", "MEDIUM": "M", "HARD": "H", "INTERMEDIATE": "I", "WET": "W"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def sql(query: str, *params):
    """Rows straight from the database (independent of the API code)."""
    with queries.read_only() as con:
        return con.execute(query, list(params)).fetchall()


# ---------------------------------------------------------------------------
# meta / races
# ---------------------------------------------------------------------------
def test_meta_matches_races_table(client):
    body = client.get("/api/meta").json()
    rounds = [r for (r,) in sql("SELECT round FROM races WHERE season = ? ORDER BY round", SEASON)]
    assert body["season"] == SEASON
    assert body["races_loaded"] == len(rounds)
    assert body["last_race"]["round"] == rounds[-1]


def test_races_list_matches_race_effects(client):
    body = client.get("/api/races").json()
    eff = {r: (rel, src) for r, rel, src in sql(
        "SELECT round, is_reliable, fuel_source FROM race_effects WHERE season = ?", SEASON)}
    assert [r["round"] for r in body] == sorted(eff)
    for r in body:
        assert (r["is_reliable"], r["fuel_source"]) == eff[r["round"]]
        assert len(r["date"]) == 10  # YYYY-MM-DD
    by_round = {r["round"]: r for r in body}
    assert by_round[19]["red_flag_laps"] == [45]
    assert by_round[11]["red_flag_laps"] == []


def test_unknown_round_is_404(client):
    r = client.get("/api/races/99")
    assert r.status_code == 404
    assert "isn't loaded" in r.json()["detail"]


# ---------------------------------------------------------------------------
# race header
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rnd", ROUNDS)
def test_header_matches_database(client, rnd):
    h = client.get(f"/api/races/{rnd}").json()
    fuel, src, own, r2, n_laps, reliable = sql(
        "SELECT fuel_track_effect_per_lap, fuel_source, fuel_estimated, r_squared, n_laps, is_reliable "
        "FROM race_effects WHERE season = ? AND round = ?", SEASON, rnd)[0]
    winner, seq = sql("SELECT driver, compound_sequence FROM strategies "
                      "WHERE season = ? AND round = ? AND finish_position = 1", SEASON, rnd)[0]
    (n_drivers,) = sql("SELECT count(*) FROM driver_baselines WHERE season = ? AND round = ?", SEASON, rnd)[0]
    t_min, t_max = sql("SELECT min(track_temp), max(track_temp) FROM weather "
                       "WHERE season = ? AND round = ?", SEASON, rnd)[0]

    assert h["fuel"]["value"] == pytest.approx(fuel)
    assert h["fuel"]["source"] == src
    if src == "fixed":
        assert h["fuel"]["own_estimate"] == pytest.approx(own)
    assert h["r_squared"] == pytest.approx(r2)
    assert h["clean_laps"] == n_laps
    assert h["is_reliable"] == reliable
    assert h["winner"]["code"] == winner
    assert h["winner"]["compound_sequence"] == seq
    assert h["drivers_in_model"] == n_drivers
    for temp in h["track_temp"].values():
        assert t_min <= temp <= t_max


# ---------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rnd", ROUNDS)
def test_strategies_match_database(client, rnd):
    body = client.get(f"/api/races/{rnd}/strategies").json()
    db_rows = {d: (pos, stops, seq, lengths, fin) for d, pos, stops, seq, lengths, fin in sql(
        "SELECT driver, finish_position, n_stops, compound_sequence, stint_lengths, finished "
        "FROM strategies WHERE season = ? AND round = ?", SEASON, rnd)}
    drivers = body["drivers"]
    assert {d["code"] for d in drivers} == set(db_rows)

    # Official order: finishers by position, then every DNF.
    finished = [d for d in drivers if d["status"] == "finished"]
    assert [d["position"] for d in finished] == sorted(db_rows[d["code"]][0] for d in finished)
    assert all(d["status"] == "DNF" for d in drivers[len(finished):])

    for d in drivers:
        pos, stops, seq, lengths, fin = db_rows[d["code"]]
        assert d["stops"] == stops
        assert (d["status"] == "finished") == fin
        assert "-".join(LETTER.get(s["compound"], "?") for s in d["stints"]) == seq
        assert "-".join(str(s["end_lap"] - s["start_lap"] + 1) for s in d["stints"]) == lengths


def test_round19_red_flag_tyre_change_is_marked(client):
    ver = next(d for d in client.get("/api/races/19/strategies").json()["drivers"] if d["code"] == "VER")
    assert ver["stops"] == 1
    assert [s["red_flag_change"] for s in ver["stints"]] == [False, False, True]
    assert ver["stints"][1]["end_lap"] == 45


# ---------------------------------------------------------------------------
# deg
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rnd", ROUNDS)
def test_deg_matches_tyre_deg_table(client, rnd):
    body = client.get(f"/api/races/{rnd}/deg").json()
    rows = {c: (d, lo, hi, n) for c, d, lo, hi, n in sql(
        "SELECT compound, deg_per_lap, ci_low, ci_high, n_laps FROM tyre_deg "
        "WHERE season = ? AND round = ?", SEASON, rnd)}
    for c in body["compounds"]:
        if c["compound"] not in rows:
            assert c["deg_per_lap"] is None
            continue
        d, lo, hi, n = rows[c["compound"]]
        if d is None:
            assert c["deg_per_lap"] is None
            continue
        assert c["deg_per_lap"] == pytest.approx(d)
        assert (c["ci_low"], c["ci_high"]) == (pytest.approx(lo), pytest.approx(hi))
        assert c["n_laps"] == n
        # the fitted line's slope is the stored deg
        line = c["line"]
        slope = (line["y_end"] - line["y_start"]) / (line["x_end"] - line["x_start"])
        assert slope == pytest.approx(d)
    # one point per clean lap the field model used
    (n_clean,) = sql("SELECT n_laps FROM race_effects WHERE season = ? AND round = ?", SEASON, rnd)[0]
    assert len(body["points"]) == n_clean


# ---------------------------------------------------------------------------
# battles
# ---------------------------------------------------------------------------
ALL = "hide_sc=false&hide_teammates=false&hide_early=false&hide_outliers=false"


@pytest.mark.parametrize("rnd", ROUNDS)
def test_battles_counts_and_filters(client, rnd):
    (n_all,) = sql("SELECT count(*) FROM battles WHERE season = ? AND round = ?", SEASON, rnd)[0]
    (n_filtered,) = sql("SELECT count(*) FROM battles WHERE season = ? AND round = ? AND NOT sc_vsc_in_window "
                        "AND NOT teammates AND NOT early_stop", SEASON, rnd)[0]
    everything = client.get(f"/api/races/{rnd}/battles?{ALL}").json()
    keep_outliers = client.get(f"/api/races/{rnd}/battles?hide_outliers=false").json()
    default = client.get(f"/api/races/{rnd}/battles").json()
    assert everything["total"] == n_all and len(everything["battles"]) == n_all
    assert len(keep_outliers["battles"]) == n_filtered
    assert len(default["battles"]) == sum(not b["outlier"] for b in keep_outliers["battles"])
    assert not any(b["sc_window"] or b["teammates"] or b["early_stop"] or b["outlier"] for b in default["battles"])


@pytest.mark.parametrize("rnd", ROUNDS)
def test_outlier_flags(client, rnd):
    battles = client.get(f"/api/races/{rnd}/battles?{ALL}").json()["battles"]
    retired = {d for (d,) in sql("SELECT driver FROM race_results WHERE season = ? AND round = ? "
                                 "AND status NOT IN ('Finished', 'Lapped')", SEASON, rnd)}
    for b in battles:
        assert b["outlier"] == (b["outlier_reason"] is not None)
        if b["gained"] is not None and abs(b["gained"]) > 30:
            assert b["outlier"] and "over 30s" in b["outlier_reason"]
        for drv in (b["attacker"], b["defender"]):
            if f"{drv} retired" in (b["outlier_reason"] or ""):
                assert drv in retired


def test_round19_has_retirement_outliers(client):
    battles = client.get(f"/api/races/19/battles?{ALL}").json()["battles"]
    assert any("retired" in (b["outlier_reason"] or "") for b in battles)


@pytest.mark.parametrize("rnd", ROUNDS)
def test_battle_numbers_match_database(client, rnd):
    body = client.get(f"/api/races/{rnd}/battles?{ALL}").json()
    db_rows = sql("SELECT a_driver, b_driver, a_in_lap, gap_change, pit_loss_diff, expected_tyre_gain, "
                  "car_pace_gain, unexplained FROM battles WHERE season = ? AND round = ?", SEASON, rnd)
    by_key = {(a, b, lap): rest for a, b, lap, *rest in db_rows}
    for b in body["battles"]:
        gained, pit, tyre, pace, unexp = by_key[(b["attacker"], b["defender"], b["attacker_pit_lap"])]
        assert b["gained"] == pytest.approx(gained, nan_ok=True) or (b["gained"] is None and gained is None)
        assert b["tone"] in {"good", "bad", "neutral"}
        if all(v is not None for v in (pit, tyre, pace, unexp)):
            bd = b["breakdown"]
            assert (bd["pit_loss"], bd["tyre_gain"], bd["car_pace"], bd["unexplained"]) == (
                pytest.approx(pit), pytest.approx(tyre), pytest.approx(pace), pytest.approx(unexp))
            assert bd["total"] == pytest.approx(pit + tyre + pace + unexp)


# ---------------------------------------------------------------------------
# traffic
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rnd", ROUNDS)
def test_traffic_matches_traffic_module(client, rnd):
    (driver,) = sql("SELECT driver FROM strategies WHERE season = ? AND round = ? AND finish_position = 3",
                    SEASON, rnd)[0]
    body = client.get(f"/api/races/{rnd}/traffic?driver={driver}").json()
    direct = traffic_mod.traffic_cost(rnd, driver)
    assert body["total_lost"] == pytest.approx(direct["total_lost"])
    assert body["uncertainty"] == pytest.approx(direct["uncertainty"])
    assert [u["lap"] for u in body["per_lap"]] == direct["laps_used"]
    assert (body["actual_pos"], body["new_pos"]) == (direct["actual_pos"], direct["new_pos"])


def test_traffic_unknown_driver_is_readable_422(client):
    r = client.get("/api/races/11/traffic?driver=ZZZ")
    assert r.status_code == 422
    assert "ZZZ" in r.json()["detail"]


# ---------------------------------------------------------------------------
# what-if
# ---------------------------------------------------------------------------
def test_whatif_round11_ham_is_minus_10_5(client):
    r = client.post("/api/sim/whatif", json={"round": 11, "driver": "HAM", "stints": [
        {"compound": "MEDIUM", "laps": 25}, {"compound": "HARD", "laps": 25}, {"compound": "SOFT", "laps": 20}]})
    assert r.status_code == 200
    body = r.json()
    assert round(body["delta"], 1) == -10.5
    order = body["finishing_order"]
    assert [o["position"] for o in order] == list(range(1, len(order) + 1))
    assert next(o for o in order if o["whatif"])["position"] == body["pred_pos"]


def test_whatif_round16_is_a_readable_refusal(client):
    r = client.post("/api/sim/whatif", json={"round": 16, "driver": "HAM", "stints": [
        {"compound": "M", "laps": 20}, {"compound": "H", "laps": 35}]})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str) and "unreliable" in detail


def test_whatif_rule_break_is_readable_422(client):
    r = client.post("/api/sim/whatif", json={"round": 11, "driver": "HAM", "stints": [
        {"compound": "M", "laps": 10}, {"compound": "H", "laps": 10}]})
    assert r.status_code == 422
    assert "laps" in r.json()["detail"]


def test_bad_body_is_readable_422(client):
    r = client.post("/api/sim/whatif", json={"round": 11, "driver": "HAM", "stints": [{"compound": "M"}]})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)


# ---------------------------------------------------------------------------
# colours, CORS, busy database
# ---------------------------------------------------------------------------
def test_every_team_colour_is_readable_on_the_surface(client):
    for d in client.get("/api/races/19/strategies").json()["drivers"]:
        assert queries.contrast(d["team"]["colour"], queries.SURFACE) >= 3.0


def test_cors_allows_the_vite_dev_server(client):
    r = client.get("/api/meta", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_busy_database_is_a_readable_503(client, monkeypatch):
    def busy():
        raise duckdb.IOException("Could not set lock on file")
    monkeypatch.setattr(queries, "load_all", busy)
    monkeypatch.setitem(cache._state, "sig", None)  # force a reload
    r = client.get("/api/meta")
    assert r.status_code == 503
    assert "busy" in r.json()["detail"]
