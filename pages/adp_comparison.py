import streamlit as st

from streamlit_state import get_app_context
from scoring import ScoringFormat
from registry import Position
from presentation.adp_comparison_view import AdpComparisonView

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
    col1, col2 = st.columns([9, 3])

    with col2:    
        search = st.text_input("Search Player", placeholder="Search by name...")

with st.container():
    comparison_df = get_comparison_data(scoring_format)
    display_df = AdpComparisonView.shape(comparison_df, position)

    query = search.strip()
    if query:
        display_df = display_df[display_df["Player"].str.contains(query, case=False, na=False)]

    st.dataframe(display_df, height=500, hide_index=True, use_container_width=True)