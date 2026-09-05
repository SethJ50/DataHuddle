"""The app's entry point: run `streamlit run streamlit_app.py` to start it.

Does four things and nothing else. Sets the browser tab title and page width,
puts the logo at the top of the sidebar, loads the shared data context up front
so the wait happens once at startup, and declares which pages appear in the
sidebar and how they are grouped.

Each page's real content lives in its own file under pages/.
"""

from pathlib import Path

import streamlit as st

from deployment import pre_draft_enabled
from streamlit_state import get_app_context

st.set_page_config(page_title = "DataHuddle", layout = "wide")

# Absolute, so the logo still resolves if the app is ever launched from another
# working directory (a systemd unit, or a container).
WWW = Path(__file__).parent / "www"

# Named for the THEME each belongs to: the "dark" file is the white-lettered
# wordmark for dark mode, the "light" file is the navy-lettered one for light
# mode. Same artwork, palette swapped.
LOGOS = {
    "dark": WWW / "datahuddle_logo_dark.png",
    "light": WWW / "datahuddle_logo_light.png",
}

# `st.context.theme.type` is "light" or "dark", inferred from the app's
# background. Streamlit warns it is unreliable in two cases -- the first run of
# a session, and immediately after the viewer flips the theme in the settings
# menu -- because neither triggers a rerun, so this line does not re-evaluate.
# See streamlit/streamlit#11920. The next rerun (any widget or page change)
# corrects it. None falls back to light, which is Streamlit's own default.
st.logo(str(LOGOS.get(st.context.theme.type, LOGOS["light"])), size = "large")

LOGO_WIDTH = "78%"

# Streamlit builds the sidebar header as a flex row holding two things: the logo
# and the collapse button. Centring the logo therefore needs the button taken
# OUT of the flow -- left in it, the logo would centre on the space the button
# happens to leave over, which sits visibly left of the real middle.
#
# !important throughout because these fight Streamlit's own emotion classes,
# which carry equal specificity; without it, which rule wins is a coin toss that
# could flip on any version bump. The data-testid attributes are internal to
# Streamlit and are the thing to re-check if the logo ever looks wrong after an
# upgrade.
st.html(f"""
<style>
    [data-testid="stSidebarHeader"] {{
        position: relative;
        justify-content: center;
        padding-top: 1.25rem !important;
        padding-bottom: 0.75rem !important;
    }}
    [data-testid="stSidebarLogo"] {{
        display: block;
        width: {LOGO_WIDTH} !important;
        max-width: 100% !important;
        /* A wordmark roughly 5:1, so let the aspect ratio set the height
           rather than pinning both and risking a squashed logo. */
        height: auto !important;
        max-height: none !important;
        margin: 0 auto !important;
    }}
    [data-testid="stSidebarCollapseButton"] {{
        position: absolute;
        top: 0.75rem;
        right: 0.5rem;
    }}
    /* st.html still emits a block element for the style tag above. Left alone
       it adds an empty gap at the top of every page. */
    [data-testid="stElementContainer"]:has(> [data-testid="stHtml"]) {{
        display: none;
    }}
</style>
""")

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
path_to_wr1_page = st.Page("pages/path_to_wr1.py", title = "Path to WR1")
adp_comparison_page = st.Page("pages/adp_comparison.py", title = "ADP Comparison")
adp_analysis_page = st.Page("pages/adp_analysis.py", title = "ADP Analysis")
draft_runner_page = st.Page("pages/draft_runner.py", title = "Draft Runner")
team_comparison_page = st.Page("pages/team_comparison.py", title = "Team Comparison")
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

# Every Page object above is built unconditionally, even when it will not be
# shown. Building one is free -- it only records a path and a title -- and it
# keeps tests/test_navigation.py able to see the full list of pages by reading
# this file, which is how it catches a mis-typed filename.
#
# The gate is applied HERE instead, by choosing what goes in the dictionary.
# `st.navigation` will not route to a page it was not handed, so a Pre-Draft
# page left out below has no URL on a public deployment -- not merely a hidden
# sidebar link. See deployment.py for the environment variable that decides.
sections = {
    "": [home_page],
    "DFS": [dfs_basic_plots_page, dfs_player_profile_page, dfs_team_profile_page, dfs_cheat_sheet_page],
}

if pre_draft_enabled():
    # Inserted BEFORE "DFS" rather than appended, because Streamlit draws the
    # sidebar in dictionary order and Pre-Draft has always sat in the middle.
    sections = {
        "": sections[""],
        "Pre-Draft": [draft_manager_page, player_profile_page, team_profile_page, draft_plan_page, path_to_wr1_page, adp_comparison_page, adp_analysis_page, draft_runner_page, team_comparison_page, sim_viewer_page],
        "DFS": sections["DFS"],
    }

pg = st.navigation(sections)
pg.run()