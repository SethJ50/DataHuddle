import streamlit as st
from scoring import ScoringFormat

@st.dialog("New Draft")
def _create_draft_dialog(ctx, page_key):
    name = st.text_input("Draft name", placeholder="ESPN Friends League 2026")
    num_teams = st.number_input("# Teams", 2, 32, 12)
    draft_position = st.number_input("Draft Position", 1, num_teams, 1)
    num_rounds = st.number_input("Rounds", 1, 40, 15)
    platform = st.selectbox("Platform", ["espn", "yahoo", "sleeper"])
    fmt = st.selectbox("Scoring Format", list(ScoringFormat), format_func=lambda f: f.value)
    if st.button("Create") and name:
        new_id = ctx.draft_service.create_draft(
            name, num_teams, draft_position, num_rounds, platform, fmt.value)
        st.session_state[f"{page_key}_draft_id"] = new_id  # auto-select it
        st.rerun()

def draft_selector(ctx, page_key):
    """Renders a draft dropdown + New-draft button; returns the selected
    draft doc (or None if no drafts exist yet)."""
    drafts = ctx.draft_service.list_drafts()
    if st.button("＋ New draft", key=f"{page_key}_new"):
        _create_draft_dialog(ctx, page_key)
    if not drafts:
        st.info("Create a draft to get started.")
        return None
    by_name = {d["name"]: d for d in drafts}
    names = list(by_name)
    # honor an auto-selected id from a just-created draft
    preselect = st.session_state.get(f"{page_key}_draft_id")
    index = next((i for i, d in enumerate(drafts) if d["draft_id"] == preselect), 0)
    chosen = st.selectbox("Draft", names, index=index, key=f"{page_key}_sel")
    return by_name[chosen]