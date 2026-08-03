import streamlit as st

from streamlit_state import get_app_context
from scoring import ScoringFormat
from registry import Position
from presentation.adp_comparison_view import AdpComparisonView
from ui_helpers import adp_to_round_pick

ctx = get_app_context()

@st.cache_data(show_spinner="Comparing ADP across ESPN, Yahoo, and Sleeper...")
def get_comparison_data(fmt: ScoringFormat):
    return ctx.adp_comparison_service.compare(fmt)

st.title("ADP Platform Comparison")

with st.sidebar:
    st.header("Settings:")

    position_choices = ["All"] + [p.value for p in Position]
    position = st.selectbox("Position", position_choices, index = 0)

    format_labels = {"Half PPR": ScoringFormat.HALF_PPR, "Full PPR": ScoringFormat.FULL_PPR}
    format_label = st.selectbox("Scoring Format", list(format_labels.keys()), index=0)
    scoring_format = format_labels[format_label]


with st.container():
    col_teams, col_spacer, col_search = st.columns([3, 6, 3])

    with col_teams:
        # League size drives the ROUND.PICK math (picks per round).
        num_teams = st.number_input("# Teams", min_value=2, max_value=32, value=12, step=1)

    with col_search:
        search = st.text_input("Search Player", placeholder="Search by name...")

with st.container():
    comparison_df = get_comparison_data(scoring_format)
    display_df = AdpComparisonView.shape(comparison_df, position)

    # Replace each platform's raw ADP number with ROUND.PICK for the chosen
    # league size. shape() guarantees these columns exist even when empty.
    for adp_col in ["ESPN ADP", "Yahoo ADP", "Sleeper ADP"]:
        display_df[adp_col] = display_df[adp_col].map(
            lambda a: adp_to_round_pick(a, num_teams)
        )

    query = search.strip()
    if query:
        display_df = display_df[display_df["Player"].str.contains(query, case=False, na=False)]

    st.dataframe(
        display_df,
        height=500,
        hide_index=True,
        use_container_width=True,
        column_config={
            # Encoded floats -> "%.2f" shows ROUND.PICK, and sorting stays numeric.
            "ESPN ADP":    st.column_config.NumberColumn("ESPN ADP", format="%.2f"),
            "Yahoo ADP":   st.column_config.NumberColumn("Yahoo ADP", format="%.2f"),
            "Sleeper ADP": st.column_config.NumberColumn("Sleeper ADP", format="%.2f"),
        },
    )
