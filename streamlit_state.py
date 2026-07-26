import streamlit as st
from app_context import AppContext

SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]

@st.cache_resource(show_spinner="Loading player data (nflreadpy + MongoDB) -- this can take a minute...")
def get_app_context():
    return AppContext(SEASONS)