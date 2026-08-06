"""Daily Fantasy page: one team's offence, and what their defence gives up.

Two tabs, because there are two reasons to look a team up. You check their
offence when you are deciding whether to stack it, and you check their defence
when you are deciding whether to play the players facing it.

Currently a placeholder. Phase 8 of docs/DFS_PLAN.md builds this page, on top of
the team aggregates from phases 5 and 6.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import streamlit as st

st.title("Team Profile")
st.caption("Daily Fantasy")

st.info(
    "Not built yet. This page will show a team's pace and pass tendency, who "
    "gets the ball and how often, and -- on a second tab -- the fantasy points "
    "and efficiency their defence allows, ranked against the league.",
    icon=":material/construction:",
)
