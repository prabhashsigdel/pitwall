"""Home: season overview, headline numbers, latest race and navigation. Run with dash.bat."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

import data  # noqa: E402
from data import card  # noqa: E402

data.setup_page("Home")
t = data.get()
races = data.season_rows(t["races"])
eff = data.season_rows(t["race_effects"])
strategies = data.season_rows(t["strategies"])

st.markdown('<div style="font-size:3rem;font-weight:900;letter-spacing:0.04em;line-height:1">'
            'PIT<span style="color:var(--accent)">WALL</span></div>'
            f'<div class="muted" style="font-size:1.05rem;margin-top:6px">{data.SEASON} race strategy: '
            'tyre degradation, pit battles, safety cars and a strategy simulator.</div>',
            unsafe_allow_html=True)

# --- season + headline cards ----------------------------------------------------
data.section("Season", "Overview")
items = [card("Races loaded", str(len(races)))]
if not races.empty:
    last = races.sort_values("round").iloc[-1]
    items.append(card("Last race loaded", f"R{last['round']:02d}", f"{last['event_name']} · {last['date']}"))
if not eff.empty:
    items.append(card("Reliable races", f"{int(eff['is_reliable'].sum())} / {len(eff)}",
                      "used in season stats"))
data.cards(items)

items = []
teams = t["team_deg_season"]
if not teams.empty:
    allc = teams[teams["compound"] == "ALL"].sort_values("deg_vs_field")
    for row, label in ((allc.iloc[0], "Easiest on tyres"), (allc.iloc[-1], "Hardest on tyres")):
        items.append(card(label, row["team"],
                          f"{row['deg_vs_field']:+.3f} s/lap vs field · 95% CI "
                          f"[{row['ci_low']:+.3f}, {row['ci_high']:+.3f}] · {row['n_races']} races"))
b = t["battles"]
if not b.empty:
    att = data.battles_mod.attempts(b)
    ok = att["result"] == "undercut_success"
    stuck = ok & data.battles_mod.flag(att, "stuck", False)
    n = len(att)
    items.append(card("Clean undercut success", f"{ok.mean():.0%}" if n else "n/a",
                      (f"stuck after 3 laps: {stuck.mean():.0%} · " if n else "") + f"n = {n} clean attempts",
                      accent=True))
if not eff.empty and "fuel_fallback_value" in eff.columns:
    n_fuel = int((eff["is_reliable"] & (eff["fuel_source"] == "estimated")).sum())
    items.append(card("Season fuel + track", f"{eff['fuel_fallback_value'].iloc[0]:.3f} s/lap",
                      f"median of {n_fuel} reliable races that estimated their own"))
data.section("Headlines")
data.cards(items)

# --- latest race: timing tower ----------------------------------------------------
if not races.empty:
    data.section("Latest race", f"R{last['round']:02d} {last['event_name']}")
    top = (strategies[(strategies["round"] == last["round"]) & strategies["finished"]]
           .sort_values("finish_position").head(10))
    colors = data.team_colors(strategies["team"].fillna("UNKNOWN"))
    rows = "".join(
        f'<div class="tower-row"><span class="tower-pos">P{int(r.finish_position)}</span>'
        f'<span class="tower-strip" style="background:{colors.get(r.team or "UNKNOWN", data.GREY)}"></span>'
        f'<span class="tower-code">{r.driver}</span>{data.chips(r.compound_sequence)}</div>'
        for r in top.itertuples())
    left, _ = st.columns([1, 1])
    with left:
        st.markdown(f'<div class="tower">{rows}</div>', unsafe_allow_html=True)
        st.page_link("pages/1_Race.py", label="Open the race →")

# --- navigation tiles -------------------------------------------------------------
data.section("Explore")
tiles = [("pages/1_Race.py", "Race", "Stints, tyre degradation and every pit battle for one race."),
         ("pages/2_Season.py", "Season", "Team and teammate tyre wear, undercut rates, fuel, sim accuracy."),
         ("pages/3_Strategy_Sim.py", "Strategy Sim", "Build another strategy for any driver and see the result."),
         ("pages/4_Circuits.py", "Circuits", "Safety car, VSC and red flag odds per circuit, 2022–2026.")]
for col, (page, label, text) in zip(st.columns(4), tiles):
    with col.container(border=True):
        st.page_link(page, label=label)
        st.markdown(f'<div class="muted">{text}</div>', unsafe_allow_html=True)

st.caption("Numbers with a confidence interval show it. Low confidence, unreliable races and "
           "low-data circuits are labelled.")
