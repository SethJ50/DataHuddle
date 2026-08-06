"""Hands every page the one shared AppContext, built only once per app run.

Streamlit re-runs a page's whole script on every click, so anything built at the
top level would be rebuilt constantly. This module wraps the expensive context in
`@st.cache_resource`, which is how the app loads its data once and shares it.
"""

import streamlit as st
from app_context import AppContext

# Seasons of game-by-game stats to load. More seasons means more history in the
# game logs, but a slower first load.
SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]

# Seasons the Daily Fantasy pages load. DELIBERATELY SHORTER THAN `SEASONS`
# above, and deliberately a separate constant rather than a slice of it.
#
# Daily Fantasy leans on play-by-play data, which is 372 MB per season before it
# is trimmed down and 21 MB after -- so every extra season here has a real cost
# that an extra season of game logs does not. Three years is enough to give a
# defence a baseline and to compare a player against last year, which is as far
# back as week-to-week decisions usefully look.
#
# The two halves of the app want genuinely different history, so sharing one
# list would quietly make one of them wrong.
DFS_SEASONS = [2023, 2024, 2025]

@st.cache_resource(show_spinner="Loading player data (nflreadpy + MongoDB) -- this can take a minute...")
def get_app_context():
    """Get the shared AppContext, building it on the very first call only.

    Every page starts by calling this. The `@st.cache_resource` decorator above
    is what makes it cheap: it builds the context once and hands back that same
    object on every later call, for the life of the app process.

    `cache_resource` is used rather than `cache_data` because the context holds
    live objects such as database connections, which must be shared rather than
    copied.

    Steps:
        1. Build an AppContext, handing it BOTH season lists — the long one for
           season-long game logs and the short one for the Daily Fantasy pages.
           This is the slow part — it wires up every repository and service — and
           it happens once.

    Returns:
        AppContext: The shared context, carrying every repository and service the
            pages use. The SAME object each time, so anything stored on it is
            visible everywhere.
    """
    return AppContext(SEASONS, dfs_seasons=DFS_SEASONS)