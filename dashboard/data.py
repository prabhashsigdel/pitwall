"""Shared data, colours and layout helpers for every dashboard page.

The data loading itself lives in src/queries.py (shared with the FastAPI backend in
api/). This file adds Streamlit caching, error messages and the page layout.

DATABASE RULE: no connection is ever kept open (see queries.read_only). Results are
cached in memory; the sidebar "Reload data" button clears that.
"""

import sys
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # so we can reuse the pipeline code

import battles as battles_mod  # noqa: E402,F401  (used by the pages as data.battles_mod)
import config  # noqa: E402,F401
import queries  # noqa: E402
import sim  # noqa: E402,F401
import traffic as traffic_mod  # noqa: E402,F401

DB_PATH = queries.DB_PATH
SEASON = queries.SEASON
PROCESSIONAL = queries.PROCESSIONAL
SIM_MAX_MAE_S = queries.SIM_MAX_MAE_S
read_only = queries.read_only
season_rows = queries.season_rows
sim_reliability = queries.sim_reliability

COMPOUND_COLORS = {"SOFT": "#FF3333", "MEDIUM": "#FFD12E", "HARD": "#E6E6E6",
                   "INTERMEDIATE": "#43B02A", "WET": "#2F7DE1"}
GREY = queries.GREY
FALLBACK_TEAM_COLORS = queries.FALLBACK_TEAM_COLORS

# Design tokens (F1 TV broadcast style). Red is for active states, key numbers and
# section markers only.
BG, SURFACE, BORDER, TEXT, MUTED, ACCENT = "#0B0B0F", "#15151E", "#2A2A35", "#F2F2F2", "#9A9AA5", "#E10600"
FONT = "Titillium Web, sans-serif"

CSS = f"""
<style>
:root {{
    --bg:{BG}; --surface:{SURFACE}; --border:{BORDER}; --text:{TEXT}; --muted:{MUTED}; --accent:{ACCENT};
    /* Motion: one strong ease-out curve for everything (built-in easings are too weak). */
    --ease-out: cubic-bezier(0.23, 1, 0.32, 1);
}}

/* Type: Titillium Web comes from the THEME (.streamlit/config.toml, theme.font), so Streamlit
   applies it to its own text and leaves its Material Symbols icons alone. Our custom HTML
   (cards, tower, badges) inherits it from the app. No blanket font rule here: that is what
   turned icons into text like "arrow_upward". */

/* Safety net: Streamlit icons always keep the icon font. They render as
   <span data-testid="stIconMaterial" translate="no" aria-hidden="true">name</span>,
   sometimes with a different test id. */
[data-testid="stIconMaterial"], span[translate="no"][aria-hidden="true"],
[class*="material-symbols"] {{
    font-family: "Material Symbols Rounded" !important;
}}
.stApp {{background: var(--bg); color: var(--text); font-variant-numeric: tabular-nums;}}
h1, h2, h3 {{font-weight: 700 !important; letter-spacing: -0.01em;}}

/* Content max width, centred: nothing stretches across a wide monitor. */
.block-container {{max-width: 1300px; padding-top: 2rem; padding-bottom: 4rem;}}

/* Hide Streamlit chrome: header, Deploy button, main menu. */
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
#MainMenu, .stDeployButton, [data-testid="stAppDeployButton"] {{display: none !important;}}

/* Sidebar: same surface, nav labels fully visible (no clipped first letters). */
section[data-testid="stSidebar"] {{background: var(--surface); border-right: 1px solid var(--border);}}
[data-testid="stSidebarNav"] {{padding-left: 0.25rem;}}
[data-testid="stSidebarNavLink"] {{padding-left: 0.75rem !important;}}
[data-testid="stSidebarNavLink"] span {{overflow: visible !important; text-overflow: clip !important;
    white-space: nowrap; font-weight: 600;}}

/* Section header: small uppercase red kicker above a bold title. */
.kicker {{color: var(--accent); font-size: 0.75rem; font-weight: 700; letter-spacing: 0.12em;
    text-transform: uppercase; margin: 1.6rem 0 0.1rem;}}
.section-title {{font-size: 1.5rem; font-weight: 700; margin: 0 0 0.6rem;}}
.muted {{color: var(--muted);}}

/* Cards: label / big value / muted subtext. */
.cards {{display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; margin: 0.4rem 0 1rem;}}
.card {{background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px;}}
.card-label {{color: var(--muted); font-size: 0.72rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;}}
.card-value {{font-size: 1.75rem; font-weight: 700; line-height: 1.2; margin-top: 4px;}}
.card-value.accent {{color: var(--accent);}}
.card-sub {{color: var(--muted); font-size: 0.85rem; margin-top: 4px;}}

/* Badges. */
.badge {{display: inline-block; padding: 2px 10px; border-radius: 4px; font-size: 0.75rem;
    font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; margin-right: 6px;
    border: 1px solid transparent;}}
.badge-ok {{background: rgba(67,176,42,0.15); color: #7DDC66; border-color: rgba(67,176,42,0.4);}}
.badge-warn {{background: rgba(255,209,46,0.12); color: #FFD12E; border-color: rgba(255,209,46,0.4);}}
.badge-bad {{background: rgba(225,6,0,0.15); color: #FF6B66; border-color: rgba(225,6,0,0.5);}}
.badge-neutral {{background: transparent; color: var(--muted); border-color: var(--border);}}

/* Timing tower: one row per driver. */
.tower {{background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden;}}
.tower-row {{display: grid; grid-template-columns: 2.4rem 4px 3.6rem 1fr; align-items: center;
    gap: 10px; padding: 6px 14px; border-bottom: 1px solid var(--border);}}
.tower-row:last-child {{border-bottom: none;}}
.tower-pos {{font-weight: 700; text-align: right; color: var(--muted);}}
.tower-row:first-child .tower-pos {{color: var(--accent);}}
.tower-strip {{width: 4px; height: 1.3rem; border-radius: 2px;}}
.tower-code {{font-weight: 700; letter-spacing: 0.04em;}}
.chips {{display: flex; gap: 4px;}}
.chip {{width: 1.35rem; height: 1.35rem; border-radius: 50%; display: inline-flex; align-items: center;
    justify-content: center; font-size: 0.7rem; font-weight: 900; color: var(--bg);}}

.tower-row.hl {{background: rgba(225,6,0,0.16); box-shadow: inset 3px 0 0 var(--accent);}}
.tower-gap {{color: var(--muted); text-align: right;}}
.tower-row.wide {{grid-template-columns: 2.4rem 4px 1fr auto;}}

/* Big result card (strategy sim). */
.result {{background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 18px 22px; display: flex; gap: 48px; align-items: flex-end; flex-wrap: wrap; margin: 0.4rem 0 1rem;}}
.result-big {{font-size: 3rem; font-weight: 900; line-height: 1;}}
.result-big.good {{color: #43B02A;}}
.result-big.bad {{color: var(--accent);}}
.result-mid {{font-size: 2rem; font-weight: 700; line-height: 1;}}

/* Navigation tiles (st.page_link inside bordered containers). */
[data-testid="stPageLink"] a {{font-weight: 700; font-size: 1.05rem;}}

/* Press feedback: buttons confirm the click instantly. */
.stButton button {{transition: transform 160ms var(--ease-out);}}
.stButton button:active {{transform: scale(0.97);}}

/* Reruns happen on every widget change, so the "updating" dim must be light and quick. */
.stElementContainer {{transition: opacity 200ms var(--ease-out);}}
.stElementContainer[data-stale="true"] {{opacity: 0.6 !important;}}

/* Reduced motion: no movement; keep a short opacity fade so updates stay visible. */
@media (prefers-reduced-motion: reduce) {{
    .stButton button {{transition: none;}}
    .stButton button:active {{transform: none;}}
    .stElementContainer {{transition: opacity 120ms ease;}}
}}
</style>
"""


# ---------------------------------------------------------------------------
# Database access
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner="Loading data...")
def _load_all() -> dict[str, pd.DataFrame]:
    return queries.load_all()


def get() -> dict[str, pd.DataFrame]:
    """All tables. If a script is writing to the database right now, say so and stop."""
    try:
        return _load_all()
    except duckdb.IOException:
        st.error("The database is busy (a pipeline script is writing to it). "
                 "Wait for it to finish, then press **Reload data**.")
        st.stop()


@st.cache_resource(show_spinner="Fitting the race models...")
def models() -> dict:
    """Per race field models (same fit as deg.py), via sim.load_models."""
    return queries.load_models()


def run_whatif(rnd: int, driver: str, plan: list[tuple[str, int]],
               red_flag_change: bool = True, red_flag_compound: str | None = None) -> dict:
    """Run a what-if with sim.run_whatif. Raises sim.WhatIfError on rule breaks."""
    m = models()  # fitted first, so only one connection is open at a time
    return queries.run_whatif(m, rnd, driver, plan, red_flag_change, red_flag_compound)


def traffic(rnd: int, driver: str, blocker: str | None) -> dict:
    """Time lost in traffic (traffic.traffic_cost). Raises traffic_mod.TrafficError."""
    m = models()  # fitted first, so only one connection is open at a time
    return queries.traffic(m, rnd, driver, blocker)


def show_error(message: str, exc: BaseException | None = None) -> None:
    """A short readable message; the technical detail stays in a collapsed expander."""
    import traceback
    st.markdown(f'<div style="color:#FF6B66;font-weight:600;margin:0.4rem 0">⚠ {message}</div>',
                unsafe_allow_html=True)
    if exc is not None:
        with st.expander("Error detail", expanded=False):
            st.code("".join(traceback.format_exception(exc)), language="text")


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _fastf1_team_colors(teams: tuple[str, ...]) -> dict[str, str]:
    return queries.fastf1_team_colors(teams)


def team_colors(teams) -> dict[str, str]:
    """team -> colour (lightened where too dark to see on the card surface)."""
    teams = tuple(sorted({t for t in teams if isinstance(t, str)}))
    return queries.team_colors(teams, _fastf1_team_colors(teams))


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def setup_page(title: str) -> None:
    st.set_page_config(page_title=f"{title} · PITWALL", page_icon="🏁", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.caption(f"Season {SEASON}")
        if st.button("Reload data", width="stretch",
                     help="Re-read the database after running a pipeline script"):
            st.cache_data.clear()
            st.cache_resource.clear()
            st.rerun()


# ---------------------------------------------------------------------------
# HTML building blocks (shown with st.markdown(..., unsafe_allow_html=True))
# ---------------------------------------------------------------------------
def badge(text: str, kind: str = "warn") -> str:
    """Small label: kind = ok / warn / bad / neutral."""
    return f'<span class="badge badge-{kind}">{text}</span>'


def section(kicker: str, title: str = "") -> None:
    """Small uppercase red label above a bold title."""
    html = f'<div class="kicker">{kicker}</div>'
    if title:
        html += f'<div class="section-title">{title}</div>'
    st.markdown(html, unsafe_allow_html=True)


def card(label: str, value: str, sub: str | None = None, accent: bool = False) -> str:
    sub_html = f'<div class="card-sub">{sub}</div>' if sub else ""
    cls = "card-value accent" if accent else "card-value"
    return (f'<div class="card"><div class="card-label">{label}</div>'
            f'<div class="{cls}">{value}</div>{sub_html}</div>')


def cards(items: list[str]) -> None:
    """A responsive row of card() HTML strings."""
    st.markdown(f'<div class="cards">{"".join(items)}</div>', unsafe_allow_html=True)


def chips(sequence: str) -> str:
    """'M-H-S' -> small coloured compound circles."""
    letters = {"S": "SOFT", "M": "MEDIUM", "H": "HARD", "I": "INTERMEDIATE", "W": "WET"}
    out = "".join(f'<span class="chip" style="background:{COMPOUND_COLORS.get(letters.get(c, ""), GREY)}">'
                  f'{c}</span>' for c in str(sequence).split("-") if c)
    return f'<span class="chips">{out}</span>'


def race_label(races: pd.DataFrame, effects: pd.DataFrame, rnd: int) -> str:
    name = races.loc[races["round"] == rnd, "event_name"]
    eff = effects[effects["round"] == rnd]
    flag = "" if eff.empty or bool(eff["is_reliable"].iloc[0]) else "  (unreliable)"
    return f"R{rnd:02d} {name.iloc[0] if not name.empty else '?'}{flag}"


# ---------------------------------------------------------------------------
# One Plotly template for every chart
# ---------------------------------------------------------------------------
pio.templates["pitwall"] = go.layout.Template(layout=dict(
    font=dict(family=FONT, size=14, color=TEXT),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    colorway=[ACCENT, "#27F4D2", "#FFD12E", "#64C4FF", "#FF8000", "#43B02A", "#FF87BC"],
    xaxis=dict(gridcolor="rgba(255,255,255,0.06)", zerolinecolor=BORDER, linecolor=BORDER,
               tickfont=dict(size=13, color=MUTED), title=dict(font=dict(size=13, color=MUTED))),
    yaxis=dict(gridcolor="rgba(255,255,255,0.06)", zerolinecolor=BORDER, linecolor=BORDER,
               tickfont=dict(size=13, color=MUTED), title=dict(font=dict(size=13, color=MUTED))),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                font=dict(size=13), bgcolor="rgba(0,0,0,0)"),
    title=dict(font=dict(size=16, color=TEXT), x=0, xanchor="left", y=0.98, yanchor="top"),
    hoverlabel=dict(bgcolor=SURFACE, bordercolor=BORDER, font=dict(family=FONT, size=13, color=TEXT)),
    margin=dict(l=10, r=10, t=70, b=40),
))
pio.templates.default = "plotly_dark+pitwall"


def plotly_layout(fig, height: int = 450):
    """Apply the shared template and a height. Every chart goes through this."""
    fig.update_layout(template="plotly_dark+pitwall", height=height)
    return fig

