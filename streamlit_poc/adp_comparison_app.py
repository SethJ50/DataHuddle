"""
Streamlit proof-of-concept for the ADP Platform Comparison page.

Purpose: Demonstrates Streamlit's st.dataframe as a sortable, filterable
    alternative to shiny.render.DataGrid for this table.

Notes:
    This is a standalone script, not wired into app.py/panels/. It reuses
    the exact same backend objects (AppContext, AdpComparisonService,
    AdpComparisonView) that the Shiny page uses -- no reimplementation.
    Run with: streamlit run streamlit_poc/adp_comparison_app.py
"""

import sys
from pathlib import Path

# Streamlit only puts this script's own folder on sys.path, not the repo
# root, so we add the repo root ourselves before importing anything from it.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from app_context import AppContext
from scoring import ScoringFormat
from registry import Position
from presentation.adp_comparison_view import AdpComparisonView

# Same hardcoded season list app.py uses today -- kept identical so this POC
# builds the exact same player universe as the live Shiny page.
SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]


@st.cache_resource(show_spinner="Loading player data (nflreadpy + MongoDB) -- this can take a minute...")
def get_app_context() -> AppContext:
    """
    Purpose: Builds AppContext exactly once per Streamlit server process,
        no matter how many times the script reruns from widget interactions.
    Returns: AppContext, the same composition-root object app.py builds at
        module scope for the Shiny app.
    Notes:
        @st.cache_resource is for shared, un-copied singleton objects --
        the right fit here since AppContext holds live repositories/adapters,
        not a plain data value. Without this decorator, every widget
        interaction would rebuild the whole nflreadpy+Mongo-backed context
        from scratch.
    """
    return AppContext(SEASONS)


@st.cache_data(show_spinner="Comparing ADP across ESPN, Yahoo, and Sleeper...")
def get_comparison_data(fmt: ScoringFormat):
    """
    Purpose: Runs the expensive step -- loading and resolving all three ADP
        platforms -- only when the scoring-format choice actually changes.
    Parameters:
        fmt (ScoringFormat): HALF_PPR or FULL_PPR.
    Returns:
        pd.DataFrame, ctx.adp_comparison_service.compare(fmt)'s output.
    Notes:
        @st.cache_data keys its cache on this function's arguments (here,
        just fmt), so switching the scoring-format dropdown computes a
        fresh entry, but changing the position filter or search text (which
        don't affect this function's inputs) reuses the cached result
        instead of redoing the 3-platform join. This mirrors the Shiny
        page's comparison_data()/display_data() two-tier @reactive.calc
        split in panels/adp_comparison.py.
    """
    ctx = get_app_context()
    return ctx.adp_comparison_service.compare(fmt)


st.title("ADP Platform Comparison (Streamlit POC)")

# --- Controls: position filter, scoring format, name search ---
# Mirrors panels/adp_comparison.py's three controls (position dropdown,
# scoring-format dropdown, search box), laid out in one row.
col1, col2, col3 = st.columns(3)

with col1:
    position_choices = ["All"] + [p.value for p in Position]
    position = st.selectbox("Position", position_choices, index=0)

with col2:
    format_labels = {
        "Half PPR": ScoringFormat.HALF_PPR,
        "Full PPR": ScoringFormat.FULL_PPR,
    }
    format_label = st.selectbox("Scoring Format", list(format_labels.keys()), index=0)
    scoring_format = format_labels[format_label]

with col3:
    search = st.text_input("Search Player", placeholder="Search by name...")

# --- Data pipeline: expensive compare() (cached) -> cheap position filter
#     via AdpComparisonView.shape() -> cheap manual pandas search filter ---
comparison_df = get_comparison_data(scoring_format)
display_df = AdpComparisonView.shape(comparison_df, position)

query = search.strip()
if query:
    display_df = display_df[display_df["Player"].str.contains(query, case=False, na=False)]

st.dataframe(display_df, hide_index=True)
