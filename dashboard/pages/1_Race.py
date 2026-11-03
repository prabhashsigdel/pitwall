"""Race page: stint timeline, deg chart and pit battles for one race."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import data  # noqa: E402

data.setup_page("Race")
t = data.get()
races, eff = data.season_rows(t["races"]), data.season_rows(t["race_effects"])
laps, strategies = t["laps"], data.season_rows(t["strategies"])
DRY = ["SOFT", "MEDIUM", "HARD"]

rounds = sorted(races["round"])
pick, _ = st.columns([2, 3])
rnd = pick.selectbox("Race", rounds, index=len(rounds) - 1, label_visibility="collapsed",
                     format_func=lambda r: data.race_label(races, eff, r))
race = races[races["round"] == rnd].iloc[0]
race_laps = laps[laps["round"] == rnd]
team_colors = data.team_colors(strategies["team"].fillna("UNKNOWN"))

# --- header: name, round, circuit, date, badges ------------------------------------
data.section(f"Round {rnd} · {race['circuit']} · {race['date']}", race["event_name"])
e = eff[eff["round"] == rnd]
if not e.empty:
    e = e.iloc[0]
    badges = (data.badge("reliable", "ok") if e["is_reliable"]
              else data.badge(f"unreliable · {e['unreliable_reason']}", "bad"))
    if e["fuel_source"] == "estimated":
        badges += data.badge("fuel estimated", "ok")
        fuel = (f"fuel + track {e['fuel_track_effect_per_lap']:.3f} s/lap · 95% CI "
                f"[{e['fuel_est_ci_low']:.3f}, {e['fuel_est_ci_high']:.3f}]")
    else:
        badges += data.badge("fuel fixed", "warn")
        own = f"{e['fuel_estimated']:.3f}" if pd.notna(e["fuel_estimated"]) else "n/a"
        fuel = f"fuel + track {e['fuel_track_effect_per_lap']:.3f} s/lap (season fallback; own estimate {own})"
    st.markdown(f'{badges} <span class="muted">{fuel} · R² {e["r_squared"]:.2f}</span>',
                unsafe_allow_html=True)


# --- stint timeline --------------------------------------------------------------
def stint_figure() -> go.Figure:
    """One row per driver in finishing order (DNFs at the bottom), one bar per stint.
    Small gaps between bars mark every stop; striped bars = tyres fitted during a red flag."""
    order = strategies[strategies["round"] == rnd].sort_values("finish_position", na_position="last")
    label = {r.driver: (f"P{int(r.finish_position)} {r.driver}" if r.finished else f"DNF {r.driver}")
             for r in order.itertuples()}
    tick = {r.driver: f"<span style='color:{team_colors.get(r.team or 'UNKNOWN', data.GREY)}'>▌</span> "
                      f"{label[r.driver]}" for r in order.itertuples()}

    st_df = data.queries.stints(race_laps)  # shared with the API
    st_df = st_df[st_df["driver"].isin(label)]

    fig = go.Figure()
    for comp in DRY + ["INTERMEDIATE", "WET", "UNKNOWN"]:
        g = st_df[st_df["compound"] == comp]
        if g.empty:
            continue
        fig.add_bar(
            y=g["driver"].map(label), x=g["length"] - 0.24, base=g["start"] - 1 + 0.12,  # gap = stop
            orientation="h", name=comp.title(), marker_color=data.COMPOUND_COLORS.get(comp, data.GREY),
            marker_pattern=dict(shape=["/" if r else "" for r in g["red_fitted"]],
                                fgcolor="#0B0B0F", solidity=0.35),
            customdata=np.c_[g["driver"], g["start"], g["end"], g["length"],
                             g["age"].fillna(-1).astype(int)],
            hovertemplate="<b>%{customdata[0]}</b> · " + comp.title()
                          + "<br>laps %{customdata[1]}–%{customdata[2]} (%{customdata[3]} laps)"
                          + "<br>tyre age at start %{customdata[4]}<extra></extra>")
    if st_df["red_fitted"].any():  # legend entry for the stripes
        fig.add_bar(y=[label[order["driver"].iloc[0]]], x=[0], base=[0], orientation="h",
                    name="red flag tyre change", marker_color=data.GREY,
                    marker_pattern=dict(shape="/", fgcolor="#0B0B0F", solidity=0.35), hoverinfo="skip")
    cats = [label[d] for d in order["driver"]][::-1]
    fig.update_yaxes(categoryorder="array", categoryarray=cats, tickvals=cats,
                     ticktext=[tick[d] for d in order["driver"]][::-1], gridcolor="rgba(0,0,0,0)")
    fig.update_xaxes(title="Lap", range=[0, race_laps["lap_number"].max() + 0.5])
    fig.update_layout(barmode="overlay", bargap=0.35)
    return data.plotly_layout(fig, height=110 + 30 * len(order))


data.section("Strategy", "Stints")
st.plotly_chart(stint_figure(), width="stretch")


# --- deg chart ------------------------------------------------------------------
def deg_figure() -> go.Figure | None:
    """Clean laps minus driver baseline, segment offset and the race's fuel effect,
    vs tyre age. Lines = the model's compound offset + deg slope."""
    m = data.models().get(rnd)
    if m is None:
        return None
    p = m.p
    clean = race_laps[race_laps["is_clean"].fillna(False).astype(bool)
                      & race_laps["compound"].isin(DRY)].copy()
    clean["base"] = clean["driver"].map(lambda d: p.get(f"driver_{d}", np.nan))
    clean = clean.dropna(subset=["base", "tyre_life"])
    clean["y"] = (clean["lap_time"] - clean["base"]
                  - clean["segment"].map(lambda k: p.get(f"segment:{k}", 0.0))
                  - m.fuel * clean["lap_number"])
    deg = data.season_rows(t["tyre_deg"])
    deg = deg[deg["round"] == rnd].set_index("compound")
    fig, subtitle = go.Figure(), []
    for comp in DRY:
        sub = clean[clean["compound"] == comp]
        if sub.empty:
            continue
        color = data.COMPOUND_COLORS[comp]
        fig.add_scatter(x=sub["tyre_life"], y=sub["y"], mode="markers", name=f"{comp.title()} laps",
                        marker=dict(color=color, size=5, opacity=0.4), text=sub["driver"],
                        hovertemplate="%{text} · age %{x}: %{y:+.2f}s<extra></extra>", showlegend=False)
        if comp in deg.index and pd.notna(deg.loc[comp, "deg_per_lap"]):
            d = deg.loc[comp]
            xs = np.array([sub["tyre_life"].min(), sub["tyre_life"].max()], dtype=float)
            fig.add_scatter(x=xs, y=p.get(f"offset:{comp}", 0.0) + d["deg_per_lap"] * xs, mode="lines",
                            line=dict(color=color, width=3), name=comp.title())
            low = " · LOW CONFIDENCE" if d["low_confidence"] else ""
            subtitle.append(f"<span style='color:{color}'>{comp.title()}</span> {d['deg_per_lap']:+.3f} s/lap "
                            f"[{d['ci_low']:+.3f}, {d['ci_high']:+.3f}] n={int(d['n_laps'])}{low}")
    fig.update_xaxes(title="Tyre age (laps)")
    fig.update_yaxes(title="vs driver baseline, fuel-corrected (s)")
    fig.update_layout(title=dict(text="<span style='font-size:13px;color:#9A9AA5'>"
                                      + "  ·  ".join(subtitle) + "  (95% CI)</span>"))
    return data.plotly_layout(fig, height=480)


data.section("Tyres", "Degradation")
fig = deg_figure()
if fig is None:
    st.info("No degradation model for this race.")
else:
    st.plotly_chart(fig, width="stretch")

# --- time lost in traffic ---------------------------------------------------------
data.section("Traffic", "Time lost in traffic")
finishers = strategies[(strategies["round"] == rnd) & strategies["finished"]].sort_values("finish_position")
c1, c2, _ = st.columns([1, 1, 2])
tr_driver = c1.selectbox("Driver", finishers["driver"], key=f"tr_drv_{rnd}",
                         format_func=lambda d: f"P{int(finishers.loc[finishers['driver'] == d, 'finish_position'].iloc[0])} {d}")
tr_blocker = c2.selectbox("Held up by", ["Any car"] + [d for d in strategies.loc[strategies["round"] == rnd, "driver"]
                                                       if d != tr_driver], key=f"tr_blk_{rnd}")


def traffic_section() -> None:
    try:
        tr = data.traffic(rnd, tr_driver, None if tr_blocker == "Any car" else tr_blocker)
    except data.traffic_mod.TrafficError as exc:
        data.show_error(str(exc))
        return
    except data.duckdb.IOException as exc:
        data.show_error("The database is busy (a pipeline script is writing to it). Try again in a moment.", exc)
        return
    if not tr["reliable"]:
        st.markdown(data.badge("race marked unreliable overall · wet laps excluded", "bad"),
                    unsafe_allow_html=True)
    if not tr["per_lap"]:
        st.markdown(f'<div class="muted">No usable laps with {tr_driver} directly behind '
                    f'{"another car" if tr_blocker == "Any car" else tr_blocker} '
                    f'(within {data.config.TRAFFIC_GAP_S}s at the start of the lap).</div>', unsafe_allow_html=True)
    else:
        per = pd.DataFrame(tr["per_lap"])
        fig = go.Figure(go.Bar(
            x=per["lap"], y=per["lost"], marker_color=[data.ACCENT if v > 0 else "#43B02A" for v in per["lost"]],
            customdata=np.c_[per["ahead"], per["gap_ahead_s"], per["actual"], per["clean_air"]],
            hovertemplate="lap %{x}: lost %{y:+.2f}s<br>behind %{customdata[0]} (%{customdata[1]:.2f}s)"
                          "<br>actual %{customdata[2]:.3f} · clean air %{customdata[3]:.3f}<extra></extra>"))
        fig.update_xaxes(title="Lap", dtick=1 if len(per) <= 25 else None)
        fig.update_yaxes(title="time lost vs clean air (s)")
        st.plotly_chart(data.plotly_layout(fig, height=280), width="stretch")
        ahead = (f"{tr['gap_to_car_ahead']:.1f}s behind {tr['car_ahead']} at the flag"
                 if tr["car_ahead"] else "no car ahead on the same lap")
        data.cards([
            data.card("Time lost", f"{tr['total_lost']:+.1f}s",
                      f"± {tr['uncertainty']:.1f}s (95%) · {len(tr['per_lap'])} laps", accent=True),
            data.card("Finish on time alone", f"P{tr['new_pos']}", f"was P{tr['actual_pos']}"),
            data.card("Gap at the flag", f"{tr['gap_to_car_ahead']:.1f}s" if tr["car_ahead"] else "–", ahead)])
        st.caption("Being ahead on time doesn't guarantee finishing ahead: rivals would have raced differently.")
    if tr["skipped"]:
        with st.expander(f"{len(tr['skipped'])} traffic lap(s) skipped"):
            st.markdown(", ".join(f"lap {s['lap']}: {s['reason']}" for s in tr["skipped"]))


try:
    traffic_section()
except Exception as exc:  # never a raw traceback
    data.show_error("Couldn't work out the time lost in traffic.", exc)

# --- battles --------------------------------------------------------------------
data.section("Pit stops", "Battles")
b = data.season_rows(t["battles"])
b = b[b["round"] == rnd] if not b.empty else b
if b.empty:
    st.info("No battles for this race (run src/battles.py).")
    st.stop()

TYPE_WORDS, RESULT_WORDS = data.queries.TYPE_WORDS, data.queries.RESULT_WORDS

c1, c2, c3, c4 = st.columns([4, 1.3, 1.3, 1.3], vertical_alignment="center")
present = [TYPE_WORDS[x] for x in TYPE_WORDS if x in set(b["battle_type"])]
types = c1.segmented_control("Type", present, selection_mode="multi", default=present,
                             label_visibility="collapsed")
hide_sc = c2.toggle("Hide SC/VSC", value=True)
hide_tm = c3.toggle("Hide teammates", value=False)
hide_early = c4.toggle(f"Hide stops < lap {data.config.MIN_STOP_LAP}", value=True)

f = b[b["battle_type"].map(TYPE_WORDS).isin(types or [])]
if hide_sc:
    f = f[~f["sc_vsc_in_window"].astype(bool)]
if hide_tm:
    f = f[~f["teammates"].astype(bool)]
if hide_early and "early_stop" in f.columns:
    f = f[~f["early_stop"].astype(bool)]
f = f.reset_index(drop=True)

view = pd.DataFrame({
    "Attacker": f["a_driver"], "Defender": f["b_driver"],
    "Pit laps (A / B)": f["a_in_lap"].astype(str) + " / "
                        + f["b_in_lap"].map(lambda v: "–" if pd.isna(v) else str(int(v))),
    "Type": f["battle_type"].map(TYPE_WORDS),
    "Result": f["result"].map(RESULT_WORDS).fillna("Not judged"),
    "Gap before": f["gap_before"], "Gap after": f["gap_after"], "Gained": f["gap_change"],
    "Stuck": f["stuck"].map(lambda v: "–" if v is None or pd.isna(v) else ("✓" if v else "✗")),
    "Trend": f["gap_trend"].fillna("–"),
})


def result_color(v: str) -> str:
    return {"good": "color: #7DDC66; font-weight: 600", "bad": "color: #FF6B66; font-weight: 600",
            "neutral": "color: #9A9AA5"}[data.queries.result_tone(v)]


styled = (view.style.map(result_color, subset=["Result"])
          .format({"Gap before": "{:+.2f}", "Gap after": "{:+.2f}", "Gained": "{:+.2f}"}, na_rep="–"))
st.caption("Gap > 0 = attacker behind. Gained = seconds the attacker gained. Select a row for its breakdown.")
event = st.dataframe(styled, hide_index=True, width="stretch", on_select="rerun",
                     selection_mode="single-row", key=f"battles_{rnd}")
rows = event.selection.rows if event and event.selection else []
if not rows:
    st.stop()

sel = f.iloc[rows[0]]
parts = [("Pit loss", sel["pit_loss_diff"]), ("Tyre gain", sel["expected_tyre_gain"]),
         ("Car pace", sel.get("car_pace_gain")), ("Unexplained", sel["unexplained"])]
st.markdown(f"**{sel['a_driver']}** attacking **{sel['b_driver']}** · "
            f"{TYPE_WORDS.get(sel['battle_type'], sel['battle_type'])} · "
            f"{RESULT_WORDS.get(sel['result'], 'Not judged')}")
if any(v is None or pd.isna(v) for _, v in parts):
    st.info("No breakdown for this battle (only undercut-window battles get one).")
else:
    names = [n for n, _ in parts] + ["Total gained"]
    vals = [float(v) for _, v in parts]
    fig = go.Figure(go.Waterfall(
        x=names, y=vals + [0], measure=["relative"] * 4 + ["total"],
        text=[f"{v:+.2f}s" for v in vals] + [f"{sum(vals):+.2f}s"], textposition="outside",
        increasing=dict(marker_color="#43B02A"), decreasing=dict(marker_color=data.ACCENT),
        totals=dict(marker_color="#F2F2F2"), connector=dict(line=dict(color=data.BORDER))))
    fig.update_yaxes(title="seconds in the attacker's favour")
    fig.update_layout(showlegend=False)
    st.plotly_chart(data.plotly_layout(fig, height=380), width="stretch")
notes = [sel.get(c) for c in ("expected_gain_note", "car_pace_note", "pit_note")]
for note in [n for n in notes if isinstance(n, str) and n]:
    st.caption(f"Note: {note}")
