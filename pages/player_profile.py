"""Page showing one player: his photo, your notes about him, and his game log.

Two halves. The narrow left column identifies the player and holds the tags and
notes you have written about him, which are saved per draft so the same player
can be a target in one league and a fade in another. The wide right column shows
his week-by-week stats, with the columns chosen to suit his position.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import streamlit as st

from streamlit_state import get_app_context
from presentation.gamelog_view import GameLogView
from ui_helpers import draft_selector
from registry import MARKING_CATEGORIES

ctx = get_app_context()

# The dropdown shows names but the rest of the app works in ids, so both
# directions of the mapping are needed.
names_by_id = ctx.roster_service.player_names() # {canonical_id: display_name}
id_by_name = {name: cid for cid, name in names_by_id.items()}
sorted_names = sorted(id_by_name)

with st.sidebar:
    st.header("Draft")
    # Markings are scoped to a draft, so this page needs a draft selected too.
    draft = draft_selector(ctx, "player_profile")


left, right = st.columns([3, 9])

with left:
    image_slot = st.empty()

    selected_name = st.selectbox(
        "Player", sorted_names, index=0, label_visibility="collapsed"
    )
    canonical_id = id_by_name[selected_name]

    headshot_url = ctx.player_directory.get_headshot(canonical_id)

    with image_slot.container(horizontal_alignment="center"):
        st.image(headshot_url, width=300)

    with st.expander("Tags + Notes:", expanded=False):
        if draft is None:
            st.info("Select a draft in the sidebar to add markings.")
        else:
            saved = ctx.player_markings_service.get(draft["draft_id"], canonical_id)

            # A saved tag whose category has since been removed from the registry
            # would make the multiselect raise, so only still-valid ones are
            # pre-checked.
            valid_defaults = [c for c in saved["categories"] if c in MARKING_CATEGORIES]
            dropped = [c for c in saved["categories"] if c not in MARKING_CATEGORIES]

            chosen = st.multiselect(
                "Markings",
                MARKING_CATEGORIES,
                default=valid_defaults,
                key=f"marks_{draft['draft_id']}_{canonical_id}",
            )
            notes = st.text_area(
                "Notes",
                value=saved["notes"],
                key=f"notes_{draft['draft_id']}_{canonical_id}",
            )

            if st.button("Save"):
                ctx.player_markings_service.save(
                    draft["draft_id"], canonical_id, chosen, notes
                )
                st.toast("Saved")

with right:
    st.subheader("Box Scores")
    gamelog_df = GameLogView.shape(
        ctx.player_directory.get_gamelog(canonical_id),
        ctx.player_directory.get_position(canonical_id),
    )
    st.dataframe(gamelog_df, hide_index=True, width="stretch")