"""Strategy sim page: build a what-if strategy for one driver (sim.run_whatif)."""

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import data  # noqa: E402

data.setup_page("Strategy Sim")
t = data.get()
races, eff = data.season_rows(t["races"]), data.season_rows(t["race_effects"])
strategies, laps = data.season_rows(t["strategies"]), t["laps"]
DRY = ["SOFT", "MEDIUM", "HARD"]
reliable = dict(zip(eff["round"], eff["is_reliable"]))
sim_warn = data.sim_reliability(data.season_rows(t["sim_replay"]), races)



def show_error(message: str, exc: BaseException | None = None) -> None:
    """A short readable message; the technical detail stays in a collapsed expander."""
    st.markdown(f'<div style="color:#FF6B66;font-weight:600;margin:0.4rem 0">⚠ {message}</div>',
                unsafe_allow_html=True)
    if exc is not None:
        with st.expander("Error detail", expanded=False):
            st.code("".join(traceback.format_exception(exc)), language="text")


data.section("What if", "Strategy simulator")
left, right = st.columns([1, 2.4], gap="large")


def label(r: int) -> str:
    base = data.race_label(races, eff, r).split("  (")[0]
    if not reliable.get(r, False):
        return base + " · disabled"
    return base + (" · sim unreliable" if sim_warn.get(r) else "")


# --- left: inputs ----------------------------------------------------------------
with left:
    rounds = sorted(races["round"])
    rnd = st.selectbox("Race", rounds, index=len(rounds) - 1, format_func=label)
    disabled = not reliable.get(rnd, False)
    fin = strategies[(strategies["round"] == rnd) & strategies["finished"]].sort_values("finish_position")
    driver = st.selectbox("Driver", fin["driver"],
                          format_func=lambda d: f"P{int(fin.loc[fin['driver'] == d, 'finish_position'].iloc[0])} {d}")
    race_laps = laps[laps["round"] == rnd]
    total = int((race_laps["driver"] == driver).sum())
    # Only compounds used in this race AND with a fitted wear slope in its model.
    try:
        params = data.models()[rnd].p.index if rnd in data.models() else []
    except Exception:  # model fitting failed: the run will report it
        params = []
    used = [c for c in DRY if c in set(race_laps["compound"].dropna()) and f"slope:{c}" in params] or DRY

    n = st.segmented_control("Stints", [2, 3, 4], default=2) or 2
    plan = []
    for i in range(n):
        c1, c2 = st.columns([1.6, 1])
        comp = c1.selectbox(f"Stint {i + 1}", used, key=f"c{i}", index=min(i, len(used) - 1),
                            format_func=str.title)
        if i < n - 1:
            laps_i = c2.number_input("Laps", 1, total, value=total // n, key=f"l{i}")
        else:  # the last stint fills whatever is left
            laps_i = total - sum(p[1] for p in plan)
            c2.markdown(f'<div class="card-label" style="margin-top:0.4rem">Laps</div>'
                        f'<div style="font-size:1.4rem;font-weight:700">{laps_i}</div>'
                        f'<div class="muted" style="font-size:0.75rem">auto</div>', unsafe_allow_html=True)
        plan.append((comp, int(laps_i)))
    bad_laps = plan[-1][1] < 1
    run = st.button("Run what-if", type="primary", key="run", width="stretch",
                    disabled=disabled or bad_laps)

    # Warnings as compact badges.
    real = fin[fin["driver"] == driver].iloc[0] if not fin.empty else None
    badges = ""
    if disabled:
        reason = eff.loc[eff["round"] == rnd, "unreliable_reason"]
        badges += data.badge(f"race unreliable · {reason.iloc[0] if not reason.empty else 'no model'}", "bad")
    elif sim_warn.get(rnd):
        badges += data.badge(f"sim unreliable · {sim_warn[rnd]}", "warn")
    if bad_laps:
        badges += data.badge(f"first stints use more than {total - 1} laps", "bad")
    if real is not None and {c[0] for c, _ in plan} != set(real["compound_sequence"].split("-")):
        badges += data.badge("compound swap · lower confidence", "warn")
    red_race = bool(race_laps["red"].any())
    if red_race:
        badges += data.badge("red flag race", "neutral")
    if badges:
        st.markdown(badges, unsafe_allow_html=True)
    if red_race:
        st.caption("Red flag race: the field restarts bunched up, so only the ORDER at the stoppage "
                   "carries over. Time gained before the red flag can move the driver up or down the "
                   "restart order, but the seconds don't carry; only time gained after the last "
                   "restart counts toward the finish.")
        red_change = st.toggle("Free tyre change at the red flag", value=True,
                               help="Like the real drivers, change tyres for free during the stoppage.")
        red_pick = st.selectbox("Fresh set after the red flag", ["Same compound"] + used,
                                format_func=str.title, disabled=not red_change)
        red_comp = None if red_pick == "Same compound" else red_pick
    else:
        red_change, red_comp = True, None
    with st.expander("Limitations", expanded=False):
        st.markdown("- Cars run independently: no traffic and no overtaking.\n"
                    "- Tyre wear is a straight line, which underestimates the end of long stints "
                    "(about +0.09 s/lap over the last 5 laps).\n"
                    "- Rivals don't react to the new strategy.\n"
                    "- New tyre sets after every stop.\n"
                    "- Compound swaps are lower confidence: the model's gaps between compounds are compressed.")


# --- right: real vs your strategy on one lap axis ---------------------------------------
def strategy_figure() -> go.Figure:
    d = race_laps[race_laps["driver"] == driver].sort_values("lap_number")
    real_stints = [(g["compound"].mode().iloc[0], int(g["lap_number"].min()), int(g["lap_number"].max()))
                   for _, g in d.groupby("stint")]
    mine, lap = [], 1
    for comp, n_laps in plan:
        if n_laps > 0:
            mine.append((comp, lap, lap + n_laps - 1))
        lap += n_laps
    # Free tyre change at the red flag: split the stint running at the red flag lap.
    lap_red = data.sim.red_flag_lap(d) if red_change else None
    if lap_red is not None:
        split = []
        for comp, start, end in mine:
            if start <= lap_red < end:
                split += [(comp, start, lap_red), ((red_comp or comp) + "*", lap_red + 1, end)]
            else:
                split.append((comp, start, end))
        mine = split
    fig, shown = go.Figure(), set()
    for row, stints in (("Real", real_stints), ("Your strategy", mine)):
        for comp, start, end in stints:
            fresh_at_red = comp.endswith("*")  # set fitted during the red flag: striped
            comp = comp.rstrip("*")
            fig.add_bar(y=[row], x=[end - start + 1 - 0.3], base=[start - 1 + 0.15], orientation="h",
                        marker_color=data.COMPOUND_COLORS.get(comp, data.GREY), name=comp.title(),
                        marker_pattern=dict(shape="/" if fresh_at_red else "", fgcolor="#0B0B0F", solidity=0.35),
                        showlegend=comp not in shown, legendgroup=comp,
                        text=[f"{end - start + 1}"], textposition="inside", insidetextanchor="middle",
                        textfont=dict(color="#0B0B0F", size=13),
                        hovertemplate=f"{row}: {comp.title()} laps {start}–{end}<extra></extra>")
            shown.add(comp)
    fig.update_yaxes(categoryorder="array", categoryarray=["Your strategy", "Real"],
                     tickfont=dict(size=14, color="#F2F2F2"), gridcolor="rgba(0,0,0,0)")
    fig.update_xaxes(title="Lap", range=[0, total + 0.5])
    fig.update_layout(barmode="overlay", bargap=0.45)
    return data.plotly_layout(fig, height=230)


def render_result(r: dict) -> None:
    """Result card + predicted finishing order."""
    # --- result card -----------------------------------------------------------
    faster = r["delta"] < 0
    delta = f"{r['delta']:+.1f}s".replace("-", "−")
    st.markdown(
        f'<div class="result">'
        f'<div><div class="card-label">Time vs real</div>'
        f'<div class="result-big {"good" if faster else "bad"}">{delta}</div></div>'
        f'<div><div class="card-label">Predicted finish</div>'
        f'<div class="result-mid">P{r["pred_pos"]}<span class="muted" style="font-size:1.1rem">, '
        f'was P{r["actual_pos"]}</span></div></div>'
        f'<div><div class="card-label">Green pit loss</div>'
        f'<div class="result-mid" style="font-size:1.3rem">{r["loss"]["green"]:.1f}s</div>'
        f'<div class="card-sub">median of {r["n_green_stops"]} stops</div></div>'
        f'</div>', unsafe_allow_html=True)
    for note in r["notes"]:
        st.caption(note)
    if r.get("red"):
        rf = r["red"]
        st.caption(f"Red flag on lap {rf['lap']}: {rf['pre_delta']:+.1f}s before the stoppage moved the "
                   f"restart slot P{rf['slot_actual']} → P{rf['slot_sim']} (gap reset); "
                   f"{rf['post_delta']:+.1f}s after the restart counts toward the finish.")

    # --- predicted finishing order as a timing tower --------------------------------
    o = r["others"].copy()
    o = pd.concat([o, pd.DataFrame([{"driver": driver, "laps": r["laps"], "time": r["whatif_time"],
                                     "whatif": True}])], ignore_index=True)
    o["whatif"] = o["whatif"].fillna(False).astype(bool)
    o = o.sort_values(["laps", "time"], ascending=[False, True]).reset_index(drop=True)
    lead_t, lead_n = o.loc[0, "time"], o.loc[0, "laps"]
    teams = dict(zip(strategies.loc[strategies["round"] == rnd, "driver"],
                     strategies.loc[strategies["round"] == rnd, "team"]))
    colors = data.team_colors(strategies["team"].fillna("UNKNOWN"))

    def gap(row) -> str:
        if np.isinf(row.time):
            return "DNF"
        if row.laps < lead_n:
            return f"+{lead_n - row.laps} lap(s)"
        return "leader" if row.Index == 0 else f"+{row.time - lead_t:.1f}s"

    rows = "".join(
        f'<div class="tower-row wide{" hl" if row.whatif else ""}">'
        f'<span class="tower-pos">P{row.Index + 1}</span>'
        f'<span class="tower-strip" style="background:{colors.get(teams.get(row.driver) or "UNKNOWN", data.GREY)}"></span>'
        f'<span class="tower-code">{row.driver}{" · what-if" if row.whatif else ""}</span>'
        f'<span class="tower-gap">{gap(row)}</span></div>'
        for row in o.itertuples())
    data.section("Predicted finishing order")
    st.markdown(f'<div class="tower">{rows}</div>', unsafe_allow_html=True)


with right:
    if real is not None:
        st.markdown(f'<div class="card-label">{driver} · real</div>'
                    f'<div style="display:flex;gap:12px;align-items:center;margin:4px 0 2px">'
                    f'{data.chips(real["compound_sequence"])}<span class="muted">stints {real["stint_lengths"]} '
                    f'· {real["n_stops"]} stop(s) · finished P{int(real["finish_position"])} · {total} laps'
                    f'</span></div>', unsafe_allow_html=True)
    try:
        st.plotly_chart(strategy_figure(), width="stretch")
    except Exception as exc:
        show_error("Couldn't draw the strategy chart.", exc)

    # Keep the last result while its inputs are unchanged (survives other reruns).
    key = (rnd, driver, tuple(plan), red_change, red_comp)
    if run:
        try:
            with st.spinner("Simulating..."):
                st.session_state["whatif"] = (key, data.run_whatif(rnd, driver, plan, red_change, red_comp))
        except data.sim.WhatIfError as exc:  # broke a rule / can't be simulated: readable reason
            st.session_state.pop("whatif", None)
            show_error(str(exc))
        except data.duckdb.IOException as exc:
            st.session_state.pop("whatif", None)
            show_error("The database is busy (a pipeline script is writing to it). Try again in a moment.", exc)
        except Exception as exc:  # anything unexpected: never a raw traceback
            st.session_state.pop("whatif", None)
            show_error("Something went wrong running this what-if.", exc)
    saved = st.session_state.get("whatif")
    if not saved or saved[0] != key:
        st.markdown('<div class="muted">Build a strategy on the left and press <b>Run what-if</b>.</div>',
                    unsafe_allow_html=True)
        st.stop()
    r = saved[1]

    try:
        render_result(r)
    except Exception as exc:
        show_error("Couldn't display the result.", exc)
