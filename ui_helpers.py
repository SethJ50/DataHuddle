import streamlit as st
import pandas as pd
from scoring import ScoringFormat

# Pretty labels for the read-only display of a draft's saved settings.
PLATFORM_LABELS = {"espn": "ESPN", "yahoo": "Yahoo", "sleeper": "Sleeper"}
FORMAT_LABELS = {
    ScoringFormat.REGULAR.value:  "Standard",
    ScoringFormat.HALF_PPR.value: "Half PPR",
    ScoringFormat.FULL_PPR.value: "Full PPR",
}


def _draft_settings_panel(draft):
    """Purpose: show a draft's saved settings read-only, under the sidebar
        picker. Creating/editing these values lives on the Draft Manager page.
    Parameters: draft (dict) -- one draft doc from DraftService.
    Returns: None (writes directly into the sidebar).
    Notes: Older drafts saved before starting_slots/keepers existed show a
        dash rather than raising, so they keep working untouched."""
    st.divider()
    st.caption("Settings (from this draft)")
    st.write(f"**Teams:** {draft['num_teams']}")
    st.write(f"**Draft Position:** {draft['draft_position']}")
    st.write(f"**Rounds:** {draft['num_rounds']}")
    st.write(f"**Platform:** {PLATFORM_LABELS.get(draft['platform'], draft['platform'])}")
    fmt = draft["scoring_format"]  # stored as the ScoringFormat .value string
    st.write(f"**Scoring:** {FORMAT_LABELS.get(fmt, fmt)}")

    slots = draft.get("starting_slots")
    if slots:
        # Only show slots that are actually started, so the line stays readable.
        lineup = " ".join(f"{n}{pos}" for pos, n in slots.items() if n)
        st.write(f"**Lineup:** {lineup}")

    keepers = draft.get("keepers") or []
    if keepers:
        st.write(f"**Keepers:** {len(keepers)}")


@st.cache_resource(show_spinner="Loading draft simulation...")
def _cached_board(_ctx, draft_id: str, year: int, signature: str):
    """
    Purpose: The cached half of load_sim_board -- see that function's notes.

    Parameters:
        _ctx: The shared AppContext. The LEADING UNDERSCORE tells Streamlit not
            to hash it, which is required: it holds live database handles that
            cannot be hashed, and it is a process-wide singleton anyway.
        draft_id (str), year (int): Which draft, which season.
        signature (str): The cache-busting key. NEVER READ inside this function,
            and it must NOT gain a leading underscore -- see load_sim_board.

    Returns:
        tuple (board, error): exactly one is None.
    """
    try:
        return _ctx.draft_sim_service.load_board(_ctx.draft_service.get_draft(draft_id), year=year), None
    except (FileNotFoundError, ValueError) as exc:
        return None, str(exc)


def load_sim_board(ctx, draft, year=2026):
    """
    Purpose: One draft's simulation results, cached and shared across pages.

    Parameters:
        ctx: The shared AppContext.
        draft (dict): A draft doc from DraftService.
        year (int): Season.

    Returns:
        tuple (board, error): a DraftBoard and None, or None and a message
        explaining why there isn't one. A missing simulation is RETURNED rather
        than raised because it is a normal state -- settings changed, or this
        draft was never simulated -- and the page wants to print the command
        that fixes it.

    Notes:
        WHY THE SIGNATURE ARGUMENT EXISTS. Streamlit decides "same call, reuse
        the answer" by looking at a function's arguments. Keyed on draft_id
        alone, editing a draft's settings would not change the key, so the cache
        would keep serving the board built under the OLD settings -- silently
        wrong numbers. board_signature returns a string that changes whenever
        anything the board depends on does, including the artifact file's
        timestamp, so re-running the simulation refreshes the page too.

        Lives here rather than in a page so both the Draft Plan and Sim Viewer
        share ONE cache entry. The picks matrix is a few million numbers; loading
        it once per page would be pure waste, and cache_resource (not cache_data)
        is what lets them share the object instead of each getting a copy.
    """
    signature = ctx.draft_sim_service.board_signature(draft, year=year)
    return _cached_board(ctx, draft["draft_id"], year, signature)


def draft_selector(ctx, page_key):
    """Purpose: render a read-only draft picker in the sidebar -- a dropdown to
        choose an existing draft, plus its settings shown read-only underneath.
        Draft creation and editing now live on the Draft Manager page.
    Parameters:
        ctx: the shared AppContext (for draft_service).
        page_key (str): unique per page, so each page keeps its own selection
            widget state (and honors an auto-selected id if one was set).
    Returns: the selected draft doc, or None if no drafts exist yet."""
    drafts = ctx.draft_service.list_drafts()
    if not drafts:
        st.info("No drafts yet. Create one on the Draft Manager page.")
        return None

    by_name = {d["name"]: d for d in drafts}
    names = list(by_name)

    # honor an auto-selected id (e.g. one set elsewhere) if it's still present
    preselect = st.session_state.get(f"{page_key}_draft_id")
    index = next((i for i, d in enumerate(drafts) if d["draft_id"] == preselect), 0)

    chosen = st.selectbox("Draft", names, index=index, key=f"{page_key}_sel")
    draft = by_name[chosen]

    _draft_settings_panel(draft)
    return draft

def adp_to_round_pick(adp, num_teams):
    """
    Purpose:
        Convert an overall ADP number (e.g. 13.4) into a value that BOTH sorts
        in true draft order AND displays as fantasy football's ROUND.PICK
        notation (e.g. "2.01") when shown with a 2-decimal number format.

    Parameters:
        adp (float | int | None): Average draft position as a plain overall
            pick number. May be NaN/None when a player has no ADP on a platform.
        num_teams (int): Number of teams = picks per round.

    Returns:
        float: The draft slot encoded as round + pick/100 (round 2, pick 1
            -> 2.01; round 10, pick 12 -> 10.12). Returns NaN when adp is
            missing, which tables render as a blank cell.

    Notes:
        - Why a float instead of a string? A string like "2.01" sorts
          alphabetically, so "10.01" would sort BEFORE "2.01". Encoding the slot
          as the number (round + pick/100) keeps normal numeric sorting correct,
          while a "%.2f" column format renders it as the ROUND.PICK text.
        - The decimal ADP is rounded to the NEAREST whole pick first
          (12.6 -> pick 13). Python's round() uses banker's rounding, so an
          exact .5 goes to the nearest even pick; only affects exact halves.
        - pick/100 is always < 1 (num_teams is small), so it never carries into
          the round part, and picks are always shown as two digits (.01, .12).
    """
    # Missing ADP -> NaN so the number column shows an empty cell.
    if adp is None or pd.isna(adp):
        return float("nan")

    # Nearest whole overall pick, never below 1.
    overall_pick = max(int(round(adp)), 1)

    # Split into round number and slot-within-round.
    round_number = (overall_pick - 1) // num_teams + 1
    pick_in_round = (overall_pick - 1) % num_teams + 1

    # Encode as round.pick: sorts numerically, reads as ROUND.PICK when formatted.
    return round_number + pick_in_round / 100
