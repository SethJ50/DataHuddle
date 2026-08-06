"""Daily Fantasy page: every player on a slate, one row each, columns you choose.

The widest view in the app: one row per player and a checklist of statistics to
show or hide. It exists because no fixed set of columns suits every question --
the numbers you want when picking a cheap tight end are not the ones you want
when deciding between two expensive backs.

Currently a placeholder. Phase 9 of docs/DFS_PLAN.md builds this page, once the
player-week service from phase 7 exists to feed it.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import streamlit as st

st.title("Cheat Sheet")
st.caption("Daily Fantasy")

st.info(
    "Not built yet. This page will list every player for a chosen season and "
    "week, with a checklist controlling which statistics appear as columns.",
    icon=":material/construction:",
)
