"""Season page: team / teammate tyre wear, undercuts, fuel and sim accuracy."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402
from scipy import stats  # noqa: E402

import data  # noqa: E402

data.setup_page("Season")
t = data.get()
races, eff = data.season_rows(t["races"]), data.season_rows(t["race_effects"])
data.section(f"{data.SEASON}", "Season")
st.markdown(f'<div class="muted">Season numbers leave out unreliable races and anything based on fewer '
            f'than {data.config.MIN_RACES_SEASON} races. Whiskers are 95% confidence intervals.</div>',
            unsafe_allow_html=True)


def whiskers(df: pd.DataFrame, value: str) -> dict:
    return dict(type="data", symmetric=False, array=df["ci_high"] - df[value],
                arrayminus=df[value] - df["ci_low"], color="#6A6A75", thickness=1.5, width=0)


# --- team tyre wear: dot + whisker per team, easiest at the top ---------------------
data.section("Tyres", "Team tyre wear vs the field")
s = t["team_deg_season"]
colors = data.team_colors(s["team"]) if not s.empty else {}
for tab, comp in zip(st.tabs(["Soft", "Medium", "Hard"]), ["SOFT", "MEDIUM", "HARD"]):
    with tab:
        sub = s[s["compound"] == comp].sort_values("deg_vs_field") if not s.empty else s
        if sub.empty:
            st.markdown(data.badge("not enough races", "neutral"), unsafe_allow_html=True)
            continue
        fig = go.Figure(go.Scatter(
            x=sub["deg_vs_field"], y=sub["team"], mode="markers", error_x=whiskers(sub, "deg_vs_field"),
            marker=dict(size=14, color=[colors[x] for x in sub["team"]], line=dict(width=1, color="#0B0B0F")),
            customdata=sub[["ci_low", "ci_high", "n_races"]],
            hovertemplate="<b>%{y}</b>: %{x:+.3f} s/lap<br>95% CI [%{customdata[0]:+.3f}, "
                          "%{customdata[1]:+.3f}] · %{customdata[2]} races<extra></extra>"))
        fig.add_vline(x=0, line_color="#3A3A45")
        fig.update_yaxes(categoryorder="array", categoryarray=list(sub["team"])[::-1],
                         ticktext=[f"{tm}  <span style='color:#9A9AA5'>{n}</span>"
                                   for tm, n in zip(sub["team"], sub["n_races"])][::-1],
                         tickvals=list(sub["team"])[::-1], tickfont=dict(size=14, color="#F2F2F2"))
        fig.update_xaxes(title="← easier on tyres   ·   s/lap vs field   ·   harder on tyres →")
        st.plotly_chart(data.plotly_layout(fig, height=90 + 42 * len(sub)), width="stretch")
st.caption("Number after the team = races. A whisker crossing 0 means the team isn't clearly different "
           "from the field.")

# --- teammates: diverging bars -----------------------------------------------------
data.section("Drivers", "Teammates in the same car")
m = t["teammate_deg_season"]
m = m[m["compound"] == "ALL"].sort_values("team").reset_index(drop=True) if not m.empty else m
if m.empty:
    st.markdown(data.badge("no pairs with enough races", "neutral"), unsafe_allow_html=True)
else:
    tc = data.team_colors(m["team"])
    clear = (m["ci_low"] > 0) | (m["ci_high"] < 0)  # CI doesn't cross zero
    reach = float(max(m["ci_high"].abs().max(), m["ci_low"].abs().max())) * 1.25
    fig = go.Figure(go.Bar(
        y=m["team"], x=m["a_minus_b"], orientation="h", error_x=whiskers(m, "a_minus_b"),
        marker_color=[tc[r.team] if c else "#3A3A45" for r, c in zip(m.itertuples(), clear)],
        customdata=m[["driver_a", "driver_b", "ci_low", "ci_high", "n_races"]],
        hovertemplate="%{customdata[0]} − %{customdata[1]}: %{x:+.3f} s/lap<br>95% CI "
                      "[%{customdata[2]:+.3f}, %{customdata[3]:+.3f}] · %{customdata[4]} races<extra></extra>"))
    # Driver names on both ends; the side that is easier on tyres is highlighted.
    for r, c in zip(m.itertuples(), clear):
        a_better = r.a_minus_b < 0  # negative = A's tyres wear slower
        for name, x, anchor, better in ((r.driver_a, -reach, "right", a_better),
                                        (r.driver_b, reach, "left", not a_better)):
            hl = c and better
            fig.add_annotation(x=x, y=r.team, text=f"<b>{name}</b>" if hl else name, showarrow=False,
                               xanchor=anchor, font=dict(size=14, color=tc[r.team] if hl else "#9A9AA5"))
    fig.add_vline(x=0, line_color="#3A3A45")
    fig.update_xaxes(range=[-reach * 1.5, reach * 1.5],
                     title="← left driver easier on tyres   ·   s/lap   ·   right driver easier →")
    fig.update_yaxes(categoryorder="array", categoryarray=list(m["team"])[::-1])
    st.plotly_chart(data.plotly_layout(fig, height=90 + 44 * len(m)), width="stretch")
    st.caption("Highlighted name = easier on tyres. Grey bar: the CI crosses zero, so no clear difference.")

# --- undercuts ---------------------------------------------------------------------
data.section("Pit stops", "Undercuts")
b = data.season_rows(t["battles"])
if b.empty:
    st.markdown(data.badge("no battles yet: run src/battles.py", "neutral"), unsafe_allow_html=True)
else:
    bm = data.battles_mod
    att = bm.attempts(b)

    def rates(df: pd.DataFrame) -> dict:
        ok = df["result"] == "undercut_success"
        stuck = ok & bm.flag(df, "stuck", False)
        return {"n": len(df), "success": ok.mean() if len(df) else None,
                "stuck": stuck.mean() if len(df) else None}

    r = rates(att)
    data.cards([
        data.card("Undercut success", f"{r['success']:.0%}" if r["n"] else "n/a",
                  f"n = {r['n']} clean attempts", accent=True),
        data.card("Success that stuck", f"{r['stuck']:.0%}" if r["n"] else "n/a",
                  f"still ahead 3 laps later · n = {r['n']}")])
    st.caption("Clean = attacker started behind, reliable race, no SC/VSC/red flag, not teammates, "
               f"no stop before lap {data.config.MIN_STOP_LAP}, nobody retired.")

    trend = pd.DataFrame({k: rates(g) for k, g in att.groupby(att["gap_trend"].fillna("unknown"))}).T
    trend = trend.reindex([x for x in ["closing", "stable", "dropping", "unknown"] if x in trend.index])
    fig = go.Figure()
    for col, name, color in (("success", "Success", data.ACCENT), ("stuck", "Stuck", "#F2F2F2")):
        fig.add_bar(x=trend.index.str.title(), y=trend[col].astype(float), name=name, marker_color=color,
                    text=[f"{v:.0%} · n={int(n)}" for v, n in zip(trend[col].astype(float), trend["n"])],
                    textposition="outside", hovertemplate="%{x}: %{y:.0%}<extra>" + name + "</extra>")
    fig.update_yaxes(tickformat=".0%", range=[0, 1.1], title="by gap trend before the stop")
    fig.update_layout(barmode="group", bargap=0.35)
    st.plotly_chart(data.plotly_layout(fig, height=340), width="stretch")
    if (trend["n"] < 15).any():
        st.markdown(data.badge("small samples: rates under n=15 are mostly noise"), unsafe_allow_html=True)

    items = []
    for exclude_sc in (True, False):
        off = b[bm.counts(b, exclude_sc) & (b["battle_type"] == "offset")]
        passable = off[off["later_stopper_passed"].notna()]
        g = bm.flag(off, "later_stopper_gained", False).mean()
        pz = bm.flag(passable, "later_stopper_passed", False).mean()
        items.append(data.card(f"Offsets {'without' if exclude_sc else 'including'} SC/VSC",
                               f"{g:.0%} gained", f"passed {pz:.0%} of {len(passable)} that started behind "
                                                  f"· n = {len(off)}"))
    data.cards(items)

# --- fuel ------------------------------------------------------------------------------
data.section("Model", "Fuel + track effect per race")
if eff.empty:
    st.markdown(data.badge("no race_effects yet: run src/deg.py", "neutral"), unsafe_allow_html=True)
else:
    e = eff.merge(races[["round", "event_name"]], on="round").sort_values("round")
    e["label"] = "R" + e["round"].astype(str).str.zfill(2)
    fig = go.Figure()
    lo, hi = data.config.FUEL_SANE_RANGE
    fig.add_hrect(y0=lo, y1=hi, fillcolor="#43B02A", opacity=0.06, line_width=0)
    median = e["fuel_fallback_value"].iloc[0]
    fig.add_hline(y=median, line_dash="dot", line_color="#6A6A75",
                  annotation_text=f"season median {median:.3f}", annotation_font_color="#9A9AA5")
    for src, name, symbol in (("estimated", "own estimate", "circle"), ("fixed", "fallback race", "circle-open")):
        for reliable in (True, False):
            g = e[(e["fuel_source"] == src) & (e["is_reliable"] == reliable)]
            if g.empty:
                continue
            fig.add_scatter(x=g["label"], y=g["fuel_estimated"], mode="markers",
                            name=name + ("" if reliable else " (unreliable)"),
                            opacity=1.0 if reliable else 0.35, error_y=dict(
                                type="data", symmetric=False, array=g["fuel_est_ci_high"] - g["fuel_estimated"],
                                arrayminus=g["fuel_estimated"] - g["fuel_est_ci_low"], color="#6A6A75",
                                thickness=1.2, width=0),
                            marker=dict(symbol=symbol, size=12, color="#F2F2F2", line=dict(width=2, color="#F2F2F2")),
                            text=g["event_name"], hovertemplate="%{text}<br>own estimate %{y:.3f}<extra></extra>")
    fig.update_yaxes(title="s gained per lap")
    st.plotly_chart(data.plotly_layout(fig, height=400), width="stretch")
    st.caption(f"Shaded = accepted range [{lo}, {hi}]. Hollow = the race's own estimate was out of range or too "
               "uncertain, so it uses the season median. Faded = unreliable race.")

# --- sim accuracy -------------------------------------------------------------------------
data.section("Simulator", "Replay accuracy per race")
sr = data.season_rows(t["sim_replay"])
if sr.empty:
    st.markdown(data.badge("no replay yet: run python src/sim.py replay", "neutral"), unsafe_allow_html=True)
else:
    acc = sr.groupby("round").apply(lambda g: pd.Series({
        "drivers": len(g), "MAE (s)": g["error_s"].abs().mean(), "bias (s)": g["error_s"].mean(),
        "rank correlation": stats.spearmanr(g["actual_pos"], g["sim_pos"]).statistic}),
        include_groups=False).reset_index()
    acc = acc.merge(races[["round", "event_name", "circuit"]], on="round")
    reasons = data.sim_reliability(sr, races)
    acc["sim reliable"] = acc["round"].map(lambda r: reasons.get(r) is None)
    acc = acc.sort_values("MAE (s)", ascending=False)
    fig = go.Figure(go.Bar(
        y=acc["event_name"], x=acc["MAE (s)"], orientation="h",
        marker_color=[("#4A4A55" if ok else data.ACCENT) for ok in acc["sim reliable"]],
        text=[f"{v:.1f}s" for v in acc["MAE (s)"]], textposition="outside",
        hovertemplate="%{y}<br>MAE %{x:.1f}s<extra></extra>"))
    fig.update_yaxes(categoryorder="array", categoryarray=list(acc["event_name"])[::-1])
    fig.update_xaxes(title="mean absolute error per driver (s)")
    st.plotly_chart(data.plotly_layout(fig, height=90 + 30 * len(acc)), width="stretch")
    st.caption(f"Season average {acc['MAE (s)'].mean():.1f}s. Red = sim unreliable (replay error over "
               f"{data.SIM_MAX_MAE_S:.0f}s, or a processional track like Monaco).")
    with st.expander("Table: bias and rank correlation"):
        st.dataframe(acc[["round", "event_name", "drivers", "MAE (s)", "bias (s)", "rank correlation",
                          "sim reliable"]].sort_values("round"), hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.2f")
                                    for c in ["MAE (s)", "bias (s)", "rank correlation"]})
