"""Widgets and formatting shared by more than one Streamlit page.

Anything a single page needs lives in that page. This file is for the pieces
several pages need to agree on: the sidebar draft picker, the cached loader for
simulation results, and the ROUND.PICK number formatting.

The simulation loader matters most. Keeping it here rather than in a page means
the Draft Plan and Sim Viewer share ONE cache entry for a picks matrix that runs
to a few million numbers.
"""

import streamlit as st
import pandas as pd
from draft_model.config import normalize_keepers
from scoring import ScoringFormat
from services.draft_runner_service import resimulate

# Pretty labels for the read-only display of a draft's saved settings.
PLATFORM_LABELS = {"espn": "ESPN", "yahoo": "Yahoo", "sleeper": "Sleeper"}
FORMAT_LABELS = {
    ScoringFormat.REGULAR.value:  "Standard",
    ScoringFormat.HALF_PPR.value: "Half PPR",
    ScoringFormat.FULL_PPR.value: "Full PPR",
}


def _draft_settings_panel(draft):
    """Show a draft's saved settings read-only, beneath the sidebar picker.

    A reminder of which league you are looking at, so you notice before reading
    numbers computed for a different one. Changing any of these happens on the
    Draft Manager page, not here.

    Steps:
        1. Draw a divider and a caption to separate this from the picker above.
        2. Write out the league shape: teams, your slot, and rounds.
        3. Write the platform and scoring format through their label maps, so
           stored values such as "half_ppr" display as "Half PPR". An unknown
           value falls back to itself rather than raising.
        4. If the draft has a starting lineup saved, show only the slots actually
           started, keeping the line short.
        5. If it has keepers, show how many.

    Args:
        draft: One draft document from DraftService.

    Returns:
        None: Everything is written straight into the sidebar.

    Note:
        Steps 4 and 5 are conditional so drafts saved before starting_slots and
        keepers existed simply omit those lines rather than raising, and keep
        working untouched.
    """
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

    if draft.get("has_keepers") or draft.get("keepers"):
        assigned, unassigned = normalize_keepers(draft.get("keepers"))
        st.write(f"**Keepers:** {len(assigned)} assigned"
                 + (f", {len(unassigned)} unassigned" if unassigned else ""))


@st.cache_resource(show_spinner="Loading draft simulation...")
def _cached_board(_ctx, draft_id: str, year: int, signature: str):
    """Load one draft's simulation board, and remember it for later calls.

    The cached half of `load_sim_board` below, split out so the caching decorator
    sits on a function whose arguments are exactly the cache key. Do not call
    this directly.

    Steps:
        1. Look the draft document up by id, then ask the sim service to load its
           board.
        2. Catch the two expected failures and return them as a message instead
           of letting them escape. A missing simulation is a normal state the
           page wants to explain, not a crash.

    Args:
        _ctx: The shared AppContext. The LEADING UNDERSCORE tells Streamlit not
            to hash it, which is required: it holds live database handles that
            cannot be hashed, and it is a process-wide singleton anyway.
        draft_id: Which draft to load.
        year: Which season.
        signature: The cache-busting key. NEVER READ inside this function, and it
            must NOT gain a leading underscore — an underscore would exclude it
            from the cache key and defeat its entire purpose. See
            `load_sim_board` below.

    Returns:
        tuple: `(board, error)`, where exactly one is None. On success the board
            is a DraftBoard; on failure the error is a readable message.
    """
    try:
        return _ctx.draft_sim_service.load_board(_ctx.draft_service.get_draft(draft_id), year=year), None
    except (FileNotFoundError, ValueError) as exc:
        return None, str(exc)


def load_sim_board(ctx, draft, year=2026):
    """Get one draft's simulation results, cached and shared across pages.

    The function pages should call. It computes the cache key first, then hands
    everything to the cached loader above.

    Steps:
        1. Ask the sim service for this draft's board signature — a string that
           changes whenever the loaded board would differ.
        2. If building that signature fails because the draft's own settings are
           unusable — most often a keeper that still needs a team and a round —
           return the message rather than letting it escape. The signature is
           computed before the cached loader is reached, so its errors would
           otherwise bypass the loader's own error handling and crash the page.
        3. Call `_cached_board` above with that signature, so a changed signature
           forces a genuine reload while an unchanged one reuses the loaded
           matrix.

    Args:
        ctx: The shared AppContext.
        draft: A draft document from DraftService.
        year: The season.

    Returns:
        tuple: `(board, error)` — a DraftBoard and None, or None and a message
            explaining why there is not one. A missing simulation is RETURNED
            rather than raised because it is a normal state — settings changed,
            or this draft was never simulated — and the page wants to print the
            command that fixes it.

    Note:
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
    try:
        signature = ctx.draft_sim_service.board_signature(draft, year=year)
    except ValueError as exc:
        return None, str(exc)
    return _cached_board(ctx, draft["draft_id"], year, signature)


def draft_selector(ctx, page_key):
    """Draw the sidebar draft picker, and return whichever draft is selected.

    Nearly every page starts with this, since almost everything the app shows is
    scoped to one league. Picking only — creating and editing drafts lives on the
    Draft Manager page.

    Steps:
        1. Load every saved draft. If there are none, show a pointer to the Draft
           Manager page and return None, which callers check before continuing.
        2. Build a name-to-draft mapping for the dropdown.
        3. Look for a pre-selected draft id in session state and find its
           position in the list, falling back to the first draft if that id is no
           longer present.
        4. Draw the dropdown, keyed per page so each page remembers its own
           selection independently.
        5. Show the chosen draft's settings underneath with
           `_draft_settings_panel` above.

    Args:
        ctx: The shared AppContext, used here for its draft service.
        page_key: A prefix unique to this page, so each page keeps its own
            selection rather than sharing one widget's state.

    Returns:
        dict | None: The selected draft document, or None when no drafts exist
            yet. Pages typically call `st.stop()` on None, since there is nothing
            to show without a league.
    """
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
    """Convert an overall ADP number into fantasy football's ROUND.PICK notation.

    "Pick 28" means little on its own; "3.04" tells you immediately which round to
    plan around. The clever part is that the result is a NUMBER rather than text,
    so a table can both sort it correctly and display it as ROUND.PICK.

    Steps:
        1. Return NaN for a missing ADP, which tables render as a blank cell.
        2. Round the decimal ADP to the nearest whole pick and never let it fall
           below 1.
        3. Work out which round that pick falls in. Subtracting 1 first makes the
           division count from zero, which is what integer division needs.
        4. Work out the slot within that round the same way, using the remainder.
        5. Combine them as round plus pick divided by 100.

    Args:
        adp: Average draft position as a plain overall pick number, such as 13.4.
            May be NaN or None when a player has no ADP on a platform.
        num_teams: The number of teams, which is also the number of picks per
            round.

    Returns:
        float: The draft slot encoded as round + pick/100 — round 2 pick 1 gives
            2.01, round 10 pick 12 gives 10.12. NaN when `adp` is missing.

    Note:
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

@st.cache_data(show_spinner=False)
def cached_resim(_state, _board, session_id: str, state_key: tuple, n_sims: int = 1000):
    """Re-simulate the rest of a draft, reusing the answer between reruns.

    Streamlit reruns the whole page on ANY interaction -- typing a letter in the
    search box, sorting a column. Re-simulating on each of those would make the
    console feel sluggish for no benefit, since none of them change the board.

    Steps:
        1. Call `resimulate` from services/draft_runner_service.py.

    Args:
        _state: The draft in progress. The LEADING UNDERSCORE tells Streamlit not
            to hash it -- it holds a list and cannot be hashed cheaply.
        _board: The DraftBoard, likewise not hashed; it holds numpy arrays.
        session_id: Which session, so two sessions never share an entry.
        state_key: `DraftState.state_key`. THIS is the cache key that matters:
            it changes exactly when the draft's contents change, including after
            a rewind. Never key on the number of picks -- rewinding and taking a
            different player gets you back to the same count with a different
            draft.
        n_sims: How many drafts to simulate.

    Returns:
        np.ndarray: The picks matrix, as `resimulate` describes.
    """
    return resimulate(_state, _board, n_sims=n_sims)
