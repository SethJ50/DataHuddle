import streamlit as st

from streamlit_state import get_app_context
from scoring import ScoringFormat
from draft_model.config import DEFAULT_STARTING_SLOTS
from draft_model.mechanics import picks_for_slot

ctx = get_app_context()

# Choices shared by the edit and create forms.
PLATFORMS = ["espn", "yahoo", "sleeper"]
FORMATS = list(ScoringFormat)  # REGULAR, HALF_PPR, FULL_PPR

# Order the lineup slots are shown in. FLEX sits after the positions it draws
# from, since that's how it reads on a real league settings page.
LINEUP_SLOTS = ["QB", "RB", "WR", "TE", "FLEX", "K", "DST"]


@st.cache_data(ttl="1h", show_spinner=False)
def player_name_options():
    """
    Purpose: {canonical_id: display_name} for the keeper picker.

    Returns:
        dict: canonical_id -> display name, for every player in the app's universe.

    Notes:
        Cached because RosterService.player_names() costs ~0.35s per call, and
        Streamlit reruns this whole script on ANY widget interaction. Without the
        cache, every keystroke in the draft name field and every click on a
        lineup stepper would pay that cost, which is very noticeable.

        ctx is deliberately not a parameter -- it's an unhashable shared resource,
        and closing over it keeps the cache key on nothing at all, which is right
        since the roster only changes when load_data runs.
    """
    return ctx.roster_service.player_names()


def lineup_editor(key_prefix, current):
    """
    Purpose: Render one number input per starting lineup slot.

    Parameters:
        key_prefix (str): Unique per form, so the edit and create tabs don't
            share widget state.
        current (dict): Existing slot counts to seed the inputs.

    Returns:
        dict: Position -> starter count.

    Notes:
        This is what sets the VORP replacement level. A 12-team league starting
        2 RBs has its replacement RB at roughly RB30 once the flex is accounted
        for; change the league size or the flex count and that moves. Deriving it
        from these numbers is why the model stays correct for any league shape.
    """
    slots = {}
    with st.container(horizontal=True):
        for position in LINEUP_SLOTS:
            slots[position] = st.number_input(
                position, min_value=0, max_value=6,
                value=int(current.get(position, 0)),
                key=f"{key_prefix}_slot_{position}",
            )
    return slots


def keeper_picker(key_prefix, current):
    """
    Purpose: Choose players kept before the draft.

    Parameters:
        key_prefix (str): Unique per form.
        current (list): Previously saved canonical_ids.

    Returns:
        list: Selected canonical_ids.

    Notes:
        Keepers are removed from the pool before simulating, so the remaining
        players all shift earlier. Getting this wrong makes every availability
        number optimistic.

        Only players in the app's universe can be selected, so a kept team
        defense can't be represented -- defenses have no canonical_id. Accepted:
        keeping a defense is vanishingly rare and costs one pick of accuracy.
    """
    names = player_name_options()   # {canonical_id: display_name}
    return st.multiselect(
        "Keepers", options=list(names), format_func=lambda cid: names.get(cid, cid),
        default=[cid for cid in current if cid in names],
        key=f"{key_prefix}_keepers",
        help="Removed from the player pool before simulating.",
    )


st.title("Draft Manager")
st.caption("Create a new draft, or edit the settings of an existing one.")

edit_tab, new_tab = st.tabs(["Edit existing draft", "Create new draft"])

# ---------------------------------------------------------------------------
# Edit existing draft
# ---------------------------------------------------------------------------
with edit_tab:
    # list_drafts(): every saved draft doc -- draft_id, name, num_teams,
    # draft_position, num_rounds, platform, scoring_format (.value string).
    drafts = ctx.draft_service.list_drafts()

    if not drafts:
        st.info("No drafts yet. Use the 'Create new draft' tab to make one.")
    else:
        # Keyed by draft_id, not name: the id is stable across renames, so
        # renaming a draft can't orphan the picker's stored selection. Keying by
        # name meant a rename had to be patched up by writing to session_state
        # after the widget already existed, which Streamlit forbids.
        by_id = {d["draft_id"]: d for d in drafts}
        did = st.selectbox(
            "Draft to edit", list(by_id),
            format_func=lambda draft_id: by_id[draft_id]["name"],
            key="dm_edit_pick",
        )
        d = by_id[did]

        # Each widget is keyed by draft_id so switching drafts reseeds it from
        # that draft's saved values (value= only applies when a key is new).
        name = st.text_input("Draft name", value=d["name"], key=f"dm_name_{did}")
        num_teams = st.number_input("# Teams", 2, 32, value=d["num_teams"], key=f"dm_teams_{did}")
        # Draft position can't exceed the (live) team count; clamp the seed too.
        draft_position = st.number_input(
            "Draft position", 1, num_teams,
            value=min(d["draft_position"], num_teams), key=f"dm_pos_{did}",
        )
        num_rounds = st.number_input("Rounds", 1, 40, value=d["num_rounds"], key=f"dm_rounds_{did}")
        platform = st.selectbox(
            "Platform", PLATFORMS,
            index=PLATFORMS.index(d["platform"]) if d["platform"] in PLATFORMS else 0,
            key=f"dm_plat_{did}",
        )
        fmt = st.selectbox(
            "Scoring format", FORMATS,
            index=FORMATS.index(ScoringFormat(d["scoring_format"])),
            format_func=lambda f: f.value, key=f"dm_fmt_{did}",
        )

        st.divider()
        st.caption("Starting lineup")
        starting_slots = lineup_editor(f"dm_edit_{did}", d.get("starting_slots") or DEFAULT_STARTING_SLOTS)

        roster_size = st.number_input(
            "Roster size", 1, 40,
            value=int(d.get("roster_size") or num_rounds),
            key=f"dm_roster_{did}",
            help="Total slots including bench. Can exceed the number of rounds drafted.",
        )
        keepers = keeper_picker(f"dm_edit_{did}", d.get("keepers") or [])

        # Immediate feedback that the league shape is what you meant.
        st.caption(
            "Your picks: "
            + ", ".join(str(p) for p in picks_for_slot(draft_position, num_teams, num_rounds))
        )        

        if st.button("Save changes", type="primary", icon=":material/save:", key="dm_save"):
            ctx.draft_service.update_draft(
                did, name, num_teams, draft_position, num_rounds, platform, fmt.value,
                starting_slots=starting_slots, keepers=keepers, roster_size=roster_size,
            )
            # No need to fix up the picker after a rename -- it stores draft_id,
            # which doesn't change.
            st.toast("Draft settings saved.", icon=":material/check_circle:")
            st.rerun()

        # Danger zone: deleting is two-step so it can't be a fat-finger.
        with st.expander("Delete this draft"):
            st.warning(
                "Deleting a draft is permanent. Its saved markings, notes, and "
                "draft plan are left behind (not cascade-deleted)."
            )
            confirm = st.checkbox(
                "Yes, permanently delete this draft", key=f"dm_confirm_{did}"
            )
            if st.button(
                "Delete draft", type="primary", icon=":material/delete:",
                disabled=not confirm, key="dm_delete",
            ):
                deleted_name = d["name"]
                ctx.draft_service.delete_draft(did)
                # Drop the picker's stored selection so it doesn't point at a
                # now-missing draft_id on the next run.
                st.session_state.pop("dm_edit_pick", None)
                st.toast(f"Deleted draft '{deleted_name}'.", icon=":material/check_circle:")
                st.rerun()

# ---------------------------------------------------------------------------
# Create new draft
# ---------------------------------------------------------------------------
with new_tab:
    name = st.text_input(
        "Draft name", placeholder="ESPN Friends League 2026", key="dm_new_name"
    )
    num_teams = st.number_input("# Teams", 2, 32, 12, key="dm_new_teams")
    draft_position = st.number_input("Draft position", 1, num_teams, 1, key="dm_new_pos")
    num_rounds = st.number_input("Rounds", 1, 40, 15, key="dm_new_rounds")
    platform = st.selectbox("Platform", PLATFORMS, key="dm_new_plat")
    fmt = st.selectbox("Scoring format", FORMATS, format_func=lambda f: f.value, key="dm_new_fmt")

    st.divider()
    st.caption("Starting lineup")
    starting_slots = lineup_editor("dm_new", DEFAULT_STARTING_SLOTS)

    roster_size = st.number_input(
        "Roster size", 1, 40, value=int(num_rounds), key="dm_new_roster",
        help="Total slots including bench. Can exceed the number of rounds drafted.",
    )
    keepers = keeper_picker("dm_new", [])

    st.caption(
        "Your picks: "
        + ", ".join(str(p) for p in picks_for_slot(draft_position, num_teams, num_rounds))
    )

    # A name is required; keep the button disabled until one is entered.
    if st.button(
        "Create draft", type="primary", icon=":material/add:",
        disabled=not name.strip(), key="dm_create",
    ):
        ctx.draft_service.create_draft(
            name.strip(), num_teams, draft_position, num_rounds, platform, fmt.value,
            starting_slots=starting_slots, keepers=keepers, roster_size=roster_size,
        )

        st.toast(f"Created draft '{name.strip()}'.", icon=":material/check_circle:")
        st.rerun()
