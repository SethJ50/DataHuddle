"""The app's landing page: a title and a pointer to the sidebar.

Deliberately almost empty. Streamlit shows whichever page is selected in the
sidebar, and this is what greets you before you pick one.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown.
"""

import streamlit as st

st.title("DataHuddle")
st.write("Select a page from the sidebar to get started!")