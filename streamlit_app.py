"""The app's entry point: run `streamlit run streamlit_app.py` to start it.

Does three things and nothing else. Sets the browser tab title and page width,
loads the shared data context up front so the wait happens once at startup, and
declares which pages appear in the sidebar and how they are grouped.

Each page's real content lives in its own file under pages/.
"""

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
draft_runner_page = st.Page("pages/draft_runner.py", title = "Draft Runner")
sim_viewer_page = st.Page("pages/sim_viewer.py", title = "Sim Viewer")

# Daily Fantasy. "Player Profile" and "Team Profile" appear in both groups on
# purpose: they answer the same question for a different game, and the sidebar
# heading above them is what says which is which. The FILES are named apart
# (dfs_*.py), which is what Streamlit builds each page's URL from, so the
# repeated titles cannot collide.
dfs_basic_plots_page = st.Page("pages/dfs_basic_plots.py", title = "Basic Plots")
dfs_player_profile_page = st.Page("pages/dfs_player_profile.py", title = "Player Profile")
dfs_team_profile_page = st.Page("pages/dfs_team_profile.py", title = "Team Profile")
dfs_cheat_sheet_page = st.Page("pages/dfs_cheat_sheet.py", title = "Cheat Sheet")

pg = st.navigation({
    "": [home_page],
    "Pre-Draft": [draft_manager_page, player_profile_page, team_profile_page, draft_plan_page, adp_comparison_page, draft_runner_page, sim_viewer_page],
    "DFS": [dfs_basic_plots_page, dfs_player_profile_page, dfs_team_profile_page, dfs_cheat_sheet_page],
})
pg.run()