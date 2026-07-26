import sys
from pathlib import Path

# Streamlit only puts this script's own folder (streamlit_poc/) on sys.path,
# not the repo root, so imports like `scoring` and `app_context` below would
# fail without this -- same fix as streamlit_poc/adp_comparison_app.py.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import streamlit as st

from app_context import AppContext
from scoring import ScoringFormat

st.set_page_config(layout="wide")

SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]

@st.cache_resource(show_spinner="Loading player data (nflreadpy + MongoDB) -- this can take a minute...")
def get_app_context() -> AppContext:
    """
    Purpose: Builds AppContext exactly once per Streamlit server process,
        no matter how many times the script reruns from widget interactions.
    Returns: AppContext, the same composition-root object app.py and
        adp_comparison_app.py use.
    Notes:
        @st.cache_resource is for shared, un-copied singleton objects --
        the right fit here since AppContext holds live repositories/adapters,
        not a plain data value. Without this decorator, every widget
        interaction would rebuild the whole nflreadpy+Mongo-backed context
        from scratch.
    """
    return AppContext(SEASONS)


ctx = get_app_context()

MARKING_OPTIONS = ["Safe", "Upside", "Late", "Early"]
POSITIONS = ["QB", "RB", "WR", "TE"]
BOARD_COLUMNS = ["Pick", "Player", "ADP", "True Value Rank", "Diff", "Marking", "Locked In"]

# Setting Inputs
with st.container(border=True):
    col1, col2, col3 = st.columns(3)

    with col1:
        num_teams = st.number_input("Teams in League", min_value=2, max_value=32, value=12, step=1)
        draft_position = st.number_input("Draft Position", min_value=1, max_value=num_teams, value=1, step=1)

    with col2:
        num_rounds = st.number_input("Number of Rounds", min_value=1, max_value=40, value=15, step=1)

        platform_labels = {"ESPN": "espn", "Yahoo": "yahoo", "Sleeper": "sleeper"}
        platform_label = st.selectbox("Platform", list(platform_labels.keys()))
        platform = platform_labels[platform_label]

    with col3:
        format_labels = {"Half PPR": ScoringFormat.HALF_PPR, "Full PPR": ScoringFormat.FULL_PPR}
        format_label = st.selectbox("Scoring Format", list(format_labels.keys()))
        scoring_format = format_labels[format_label]

pick_labels_list = ctx.draft_plan_service.pick_labels(num_teams, draft_position, num_rounds)
pick_options = [pick["label"] for pick in pick_labels_list]

by_name_by_position = {
    position: ctx.draft_plan_service.rank_candidates(position, platform, scoring_format).set_index("display_name")
    for position in POSITIONS
}

tabs = st.tabs(POSITIONS)

for tab, position in zip(tabs, POSITIONS):
    with tab:
        by_name = by_name_by_position[position]

        state_key = f"draft_board_{position}"
        if state_key not in st.session_state:
            st.session_state[state_key] = pd.DataFrame(columns=BOARD_COLUMNS)

        edited = st.data_editor(
            st.session_state[state_key],
            column_config={
                "Pick": st.column_config.SelectboxColumn(options=pick_options, required=True),
                "Player": st.column_config.SelectboxColumn(options=list(by_name.index), required=True),
                "ADP": st.column_config.NumberColumn(disabled=True),
                "True Value Rank": st.column_config.NumberColumn(disabled=True),
                "Diff": st.column_config.NumberColumn(disabled=True),
                "Marking": st.column_config.SelectboxColumn(options=MARKING_OPTIONS),
                "Locked In": st.column_config.CheckboxColumn(),
            },
            num_rows="dynamic",
            hide_index=True,
            key=f"editor_{position}",
        )

        edited["ADP"] = edited["Player"].map(by_name["adp"])
        edited["True Value Rank"] = edited["Player"].map(by_name["true_value_rank"])
        edited["Diff"] = edited["Player"].map(by_name["diff"])

        st.session_state[state_key] = edited