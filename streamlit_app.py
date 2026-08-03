import streamlit as st

from streamlit_state import get_app_context

st.set_page_config(page_title = "DataHuddle", layout = "wide")

# Warm the shared AppContext at startup so the heavy nflreadpy + MongoDB load
# (and its @st.cache_resource loading spinner) happens on INITIAL app load,
# not lazily the first time you open a data page. The Home page touches no
# data, so without this the spinner wouldn't appear until you navigated away.
get_app_context()

home_page = st.Page("pages/home.py", title = "Home")
draft_manager_page = st.Page("pages/draft_manager.py", title = "Draft Manager")
player_profile_page = st.Page("pages/player_profile.py", title = "Player Profile")
team_profile_page = st.Page("pages/team_profile.py", title = "Team Profile")
draft_plan_page = st.Page("pages/draft_plan.py", title = "Draft Plan")
adp_comparison_page = st.Page("pages/adp_comparison.py", title = "ADP Comparison")
sim_viewer_page = st.Page("pages/sim_viewer.py", title = "Sim Viewer")

pg = st.navigation({
    "": [home_page],
    "Pre-Draft": [draft_manager_page, player_profile_page, team_profile_page, draft_plan_page, adp_comparison_page, sim_viewer_page],
})
pg.run()