"""Daily Fantasy page: one player, his price, his form, and his game log.

The Daily Fantasy counterpart to pages/player_profile.py. That page is built for
drafting a player for a season; this one is built for buying him for a single
week, so it leads with salary and recent form rather than notes and tags.

Currently a placeholder. Phase 7 of docs/DFS_PLAN.md builds the player-week data
service and then this page.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import streamlit as st

st.title("Player Profile")
st.caption("Daily Fantasy")

st.info(
    "Not built yet. This page will show a player's headshot and salaries "
    "alongside a rolling form summary and game logs split into Fantasy, Usage, "
    "Efficiency, Expected and Advanced views.",
    icon=":material/construction:",
)