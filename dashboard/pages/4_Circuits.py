"""Circuits page: SC / VSC / red flag odds per circuit (2022 onwards), as a heatmap table."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import data  # noqa: E402

data.setup_page("Circuits")
t = data.get()
odds = t["sc_odds"]

data.section("Neutralisations", "Safety car odds per circuit")
if odds.empty:
    st.markdown(data.badge("no history yet: run python src/sc_history.py --seasons 2022 2023 2024 2025 2026",
                           "neutral"), unsafe_allow_html=True)
    st.stop()

o = odds.sort_values(["pct_any", "races"], ascending=[False, False])
view = pd.DataFrame({
    "Circuit": o["circuit_name"] + o["low_data"].map({True: "  · low data", False: ""}),
    "Any": o["pct_any"], "SC": o["pct_sc"], "VSC": o["pct_vsc"], "Red flag": o["pct_red"],
    "Races": o["races"], "SC / race": o["avg_sc"], "VSC / race": o["avg_vsc"],
}).reset_index(drop=True)
low = o["low_data"].to_numpy()

PCT = ["Any", "SC", "VSC", "Red flag"]


def heat(v: float) -> str:
    """Cell colour: transparent at 0%, F1 red at 100%; dark text on strong cells."""
    if pd.isna(v):
        return ""
    text = "#0B0B0F" if v >= 0.7 else "#F2F2F2"
    return f"background-color: rgba(225,6,0,{0.08 + 0.82 * v:.2f}); color: {text}; font-weight: 600"


def dim_low(row: pd.Series) -> list[str]:
    """Low data circuits (under 3 races) dimmed."""
    return ["opacity: 0.45" if low[row.name] else "" for _ in row]


styled = (view.style.map(heat, subset=PCT).apply(dim_low, axis=1)
          .format({c: "{:.0%}" for c in PCT} | {"SC / race": "{:.2f}", "VSC / race": "{:.2f}"}))
st.dataframe(styled, hide_index=True, width="stretch", height=38 * (len(view) + 1),
             column_config={"Circuit": st.column_config.TextColumn(width="medium")})
st.markdown('<div class="muted" style="font-size:0.85rem">Click a column header to sort. Share of races '
            'with at least one SC / VSC / red flag during the race (a start behind the SC doesn\'t count). '
            'Tracks are matched by circuit, not event name: the 2026 "Bahrain GP" at Sepang is its own '
            'circuit, and Madrid is separate from Barcelona. Low data = fewer than 3 races.</div>',
            unsafe_allow_html=True)
