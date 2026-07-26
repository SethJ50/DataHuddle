import streamlit as st

st.set_page_config(page_title = "DataHuddle", layout = "wide")

home_page = st.Page("pages/home.py", title = "Home")
player_profile_page = st.Page("pages/player_profile.py", title = "Player Profile")
draft_plan_page = st.Page("pages/draft_plan.py", title = "Draft Plan")
adp_comparison_page = st.Page("pages/adp_comparison.py", title = "ADP Comparison")

pg = st.navigation({
    "": [home_page],
    "General": [player_profile_page],
    "Pre-Draft": [draft_plan_page, adp_comparison_page],
})
pg.run()