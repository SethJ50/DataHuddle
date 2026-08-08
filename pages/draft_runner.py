"""Run a draft: either a practice simulation or a live one you enter yourself.

Two modes over the same machinery. In Draft Sim the eleven other teams pick
themselves; in Live Draft you enter every pick as it happens on whatever
platform your league actually drafts on. Either way the page re-simulates the
rest of the draft after every pick, so the availability numbers describe the
board in front of you rather than a pre-draft guess.

Two views: a read-only board grid, and a console pairing the available players
with one team's roster.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom on every interaction.
"""

import pandas as pd
import streamlit as st

from presentation.charts import cost_of_waiting_chart
from presentation.colors import position_legend_html
from presentation.marks import mark_column_config
from draft_model.queries import positional_cliffs
from presentation.draft_board_view import (
    build_board_grid, build_position_grid, cliff_frame, entries_from_pick_log,
    equal_column_widths, tint_by_position, tint_positions_column,
)
from presentation.roster_view import roster_frame, slot_roster
from presentation.team_strengths import (
    category_frame, category_label, category_options, highlight_my_team,
    my_team_frame, shade_ranks,
)
from draft_model.config import POSITIONS
from registry import MARKING_CATEGORIES
from repositories.draft_session_repo import DraftSessionRepo
from services.draft_runner_service import (
    BEST_N_UPSIDE, LOWER_IS_BETTER, advance_until_your_turn, auto_pick, avail_target_pick, live_columns,
    planned_canonical_ids, positional_costs_for_team, remaining_picks,
    round_of_pick, session_board, state_from_session, team_picks_from,
    team_players, team_strength_table,
)
from streamlit_state import get_app_context
from ui_helpers import (draft_selector, adp_to_round_pick, load_sim_board,
                        load_platform_adp, cached_resim)


ctx = get_app_context()
YEAR = 2026

# The roster panel. `Player` is the one column left flexible -- names run from
# "Bo Nix" to "Marvin Harrison Jr." and a fixed width either truncates the long
# ones or wastes the column on the short ones.
ROSTER_COLUMNS = {
    "Slot":   st.column_config.TextColumn("Slot", width=40),
    "Player": st.column_config.TextColumn("Player", width=130),
    "Pos":    st.column_config.TextColumn("Pos", width=40),
    "Proj":   st.column_config.NumberColumn("Proj", width=60, format="%.0f"),
}

# The drop-off table beside the cost chart. Short headers on purpose -- it sits
# in a narrow column and every word competes with the numbers.
STRENGTH_COLUMNS = {
    "Category": st.column_config.TextColumn("Category", width=120),
    "Team": st.column_config.TextColumn("Team", width=100),
    "Value": st.column_config.NumberColumn(
        "Value", width=60, format="%.1f",
        help="Projected fantasy points, except for Replacement, which is a gap "
             "in points between two players.",
    ),
    "Rank": st.column_config.NumberColumn(
        "Rank", width=50, format="%.0f",
        help="Where this team places on this category, 1 being best. Blank "
             "means it could not be measured -- not that the team is last.",
    ),
    "Best": st.column_config.NumberColumn(
        "Best", width=60, format="%.1f",
        help="The best any team in the league manages on this category.",
    ),
    "Worst": st.column_config.NumberColumn(
        "Worst", width=60, format="%.1f",
        help="The worst any team in the league manages on this category.",
    ),
}

CLIFF_COLUMNS = {
    "Pos": st.column_config.TextColumn("Pos", width=55),
    "Left": st.column_config.NumberColumn(
        "Left", width=60, format="%d",
        help="How many players are left at this position before the next big "
             "drop in projected points. Weigh it against how many picks happen "
             "before your turn.",
    ),
    "Drop": st.column_config.NumberColumn(
        "Drop", width=65, format="%.0f",
        help="How far projected points fall once those players are gone.",
    ),
    "Steep": st.column_config.NumberColumn(
        "Steep", width=65, format="%.1fx",
        help="The drop compared with the position's usual step between players. "
             "Around 1x is no cliff at all -- just the normal decline. 2x or "
             "more is a genuine shelf worth drafting around.",
    ),
}

st.title("Draft Runner")

# ---------------------------------------------------------------------------
# Which draft, which session
# ---------------------------------------------------------------------------
repo = DraftSessionRepo()

NEW_SIM = "__new__"


def session_summary(session):
    """Describe a session in one line for the picker.

    Steps:
        1. Count the picks stored on it.
        2. Append that to the name, so two similarly-named practice runs can be
           told apart by how far each got.

    Args:
        session: A session document from DraftSessionRepo.

    Returns:
        str: Something like "zero RB (34 picks)", or just the name when the
            session has not been started yet.
    """
    made = len(session.get("picks") or [])
    return f"{session['name']} ({made} picks)" if made else session["name"]


def pick_sim_session(draft_id):
    """Choose which practice session to run, creating one if there are none.

    A league has exactly one live draft but any number of practice sims, so this
    is where the two modes stop sharing a record. Keeping them separate is the
    whole point: practising must never touch the draft you will actually run.

    Steps:
        1. List this league's sim sessions.
        2. If there are none, create the first automatically so the page is
           immediately usable rather than a dead end.
        3. Offer them in a dropdown, with a "New sim..." entry at the end.
        4. If that entry is chosen, take a name and create it on submit.
        5. Otherwise return whichever session was picked.

    Args:
        draft_id: Which league's practice sessions to offer.

    Returns:
        dict | None: The chosen session, or None while the user is still filling
            in the new-session form -- in which case the caller should stop.
    """
    sims = [s for s in repo.list_for_draft(draft_id) if s["mode"] == "sim"]

    if not sims:
        sims = [repo.create(draft_id, "sim", "Practice 1")]

    options = [s["session_id"] for s in sims] + [NEW_SIM]
    by_id = {s["session_id"]: s for s in sims}

    chosen = st.selectbox(
        "Practice session", options,
        format_func=lambda sid: ("+ New sim..." if sid == NEW_SIM
                                 else session_summary(by_id[sid])),
        key="dr_sim_pick",
    )

    if chosen == NEW_SIM:
        name = st.text_input("Name", placeholder=f"Practice {len(sims) + 1}",
                             key="dr_new_sim_name")
        if st.button("Create", type="primary", width="stretch",
                     disabled=not name.strip()):
            # A fresh seed means genuinely different opponents. Reusing one
            # would replay the same draft and teach you nothing new.
            created = repo.create(draft_id, "sim", name.strip())
            st.session_state["dr_sim_pick"] = created["session_id"]
            st.rerun()
        return None

    return by_id[chosen]


with st.sidebar:
    st.header("Draft")
    draft = draft_selector(ctx, "draft_runner")
    if draft is None:
        st.stop()

    # required=True matters. Without it a segmented control lets you DESELECT by
    # clicking the active option, and then returns None -- which would read as
    # "not Draft Sim", hiding every simulation control and quietly falling back
    # to the live session. Worse, the None is stored under the key, so `default`
    # never reapplies and it stays that way until you click something else.
    mode = st.segmented_control("Mode", ["Live Draft", "Draft Sim"],
                                default="Live Draft", key="dr_mode",
                                required=True)

    # Belt and braces, and it has to happen BEFORE the branch below: a value
    # stored under this key by an older build of the page could still be None,
    # and that would pick the live session while every simulation control
    # quietly disappeared.
    if mode not in ("Live Draft", "Draft Sim"):
        mode = "Live Draft"

    st.divider()
    if mode == "Draft Sim":
        session = pick_sim_session(draft["draft_id"])
        if session is None:
            st.stop()
    else:
        # Exactly one live session per league, always resumable. It is the record
        # of a real draft, so it is never created a second time and never offered
        # for deletion.
        session = repo.get_or_create_live(draft["draft_id"])
        st.caption(f":material/radio_button_checked: {session_summary(session)}")

board, board_error = load_sim_board(ctx, draft, year=YEAR)

if board_error:
    st.warning(
        f"**No simulation for this draft's current settings.** The runner needs "
        f"one for its calibrated numbers.\n\n```\n{board_error}\n```",
        icon=":material/warning:",
    )
    st.stop()

# The draft is held in memory between reruns and written through to Mongo on
# every change. Reloading from the database each rerun would also be correct,
# just slower; this keeps clicking through a live draft snappy.
if st.session_state.get("dr_session_id") != session["session_id"]:
    st.session_state["dr_session_id"] = session["session_id"]
    st.session_state["dr_state"] = state_from_session(session, board.config)

state = st.session_state["dr_state"]


def persist():
    """Write the whole pick log back to Mongo after any change."""
    repo.save_picks(session["session_id"], state.picks)


def forget_session():
    """Drop the in-memory copy so the next run reloads from the database.

    Called after anything that changes which session is being shown, or empties
    the one that is. Without it the page would keep serving the DraftState it
    already had, and a reset would appear to do nothing.

    Steps:
        1. Remove the cached session id and state from Streamlit's session state.
    """
    st.session_state.pop("dr_session_id", None)
    st.session_state.pop("dr_state", None)


# Managing practice sessions. Deliberately sidebar-only and sim-only: the live
# session is the record of a real draft, cannot be rebuilt, and so is never
# offered for renaming away or deletion.
if mode == "Draft Sim":
    with st.sidebar:
        with st.expander("Manage this sim"):
            st.caption(f"Seed {session['seed']} — the opponents' opinions come "
                       f"from this, so the same seed always drafts the same way.")

            new_name = st.text_input("Rename", value=session["name"],
                                     key=f"dr_rename_{session['session_id']}")
            if new_name.strip() and new_name.strip() != session["name"]:
                if st.button("Save name", width="stretch"):
                    repo.rename(session["session_id"], new_name)
                    st.rerun()

            if st.button("Reset to pick 1", icon=":material/restart_alt:",
                         width="stretch", disabled=not state.picks):
                repo.save_picks(session["session_id"], [])
                forget_session()
                st.toast("Sim reset.", icon=":material/check_circle:")
                st.rerun()

            confirm = st.checkbox("Yes, delete this sim",
                                  key=f"dr_del_{session['session_id']}")
            if st.button("Delete sim", icon=":material/delete:", type="primary",
                         width="stretch", disabled=not confirm):
                repo.delete(session["session_id"])
                st.session_state.pop("dr_sim_pick", None)
                forget_session()
                st.toast(f"Deleted '{session['name']}'.",
                         icon=":material/check_circle:")
                st.rerun()


# A keeper spends its team's pick, so the draft should roll straight past it in
# either mode. Looped, because two teams can keep on consecutive picks.
if state.apply_keeper_if_due():
    while state.apply_keeper_if_due():
        pass
    persist()

# ---------------------------------------------------------------------------
# Draft Sim: the other teams pick themselves
# ---------------------------------------------------------------------------
sim_mode = mode == "Draft Sim"

# Whether autoplay is running survives reruns in session_state, keyed per
# session so switching sessions never leaves a previous draft ticking.
play_key = f"dr_playing_{session['session_id']}"
playing = st.session_state.setdefault(play_key, False)

# One board per simulated draft, recomputed from the seed rather than stored.
boards = session_board(state, board) if sim_mode else None


def advance_one():
    """Take a single AI pick, plus any keeper due before it."""
    if state.apply_keeper_if_due():
        persist()
        return
    if not state.is_complete and state.on_the_clock != board.config.draft_position:
        auto_pick(state, board, boards)
        persist()

# ---------------------------------------------------------------------------
# The numbers, recomputed whenever the board actually moves
# ---------------------------------------------------------------------------
picks = cached_resim(state, board, session["session_id"], state.state_key)
available = live_columns(state, board, picks)

# Tier is not in the model table -- it comes from UDK's rankings -- so it gets
# joined on here by canonical_id. display_name comes along for the draft plan
# below, which stores names rather than ids.
roster = ctx.roster_service.roster()[
    ["canonical_id", "display_name", "tier", "risk", "upside"]
]
available = available.merge(roster, on="canonical_id", how="left")

# Players you already picked out for this round, on the Draft Plan page.
# Matched through canonical_id, NOT by name: the plan stores nflreadpy display
# names while the console shows FFC's, and those disagree on suffixes and accents
# ("Kenneth Walker III" against "Kenneth Walker"). Matching on names would
# silently miss people, and a highlight that is sometimes missing is worse than
# none at all.
planned_ids, planned_unresolved, planned_round = set(), [], None
_next_pick = remaining_picks(state)
if _next_pick:
    planned_round = round_of_pick(_next_pick[0], board.config.num_teams)
    planned_ids, planned_unresolved = planned_canonical_ids(
        ctx.draft_plan_service.get_plan(draft["draft_id"]), planned_round, roster,
    )

marks = ctx.player_markings_service.all_for_draft(draft["draft_id"])
cats_by_id = {m["canonical_id"]: set(m.get("categories", [])) for m in marks}
notes_by_id = {m["canonical_id"]: m.get("notes", "") for m in marks}

# Board labels, keyed under BOTH ids. A keeper is recorded by canonical id
# alone, while everything else carries a model-table id -- and team defenses
# have no canonical id at all, so one key would not cover every pick.
label_by_id, position_by_id = {}, {}
for _, table_row in board.table.iterrows():
    label = f"{table_row['name']} ({table_row['position']})"
    label_by_id[str(table_row["ffc_player_id"])] = label
    position_by_id[str(table_row["ffc_player_id"])] = table_row["position"]
    if pd.notna(table_row["canonical_id"]):
        label_by_id[table_row["canonical_id"]] = label
        position_by_id[table_row["canonical_id"]] = table_row["position"]

# ---------------------------------------------------------------------------
# Header: whose pick, and the controls
# ---------------------------------------------------------------------------
mine = remaining_picks(state)
your_turn = state.on_the_clock == board.config.draft_position

if state.is_complete:
    st.success("Draft complete.", icon=":material/check_circle:")
else:
    header = (f"**Pick {state.current_pick}** — "
              f"Team {state.on_the_clock}" + (" (you)" if your_turn else ""))
    if mine:
        header += f" · your next pick: {mine[0]}"
    st.markdown(header)

# The cost-of-waiting panel follows whoever is on the clock rather than always
# describing you. Watching each manager's urgency in turn is how a positional run
# becomes visible before it arrives at your pick.
#
# Computed BEFORE the container so the three columns below can be laid out
# unconditionally -- an empty middle column is better than a container whose
# shape changes when the draft ends.
costs = pd.DataFrame()
clock_picks, whose = None, None
if not state.is_complete:
    on_clock = state.on_the_clock
    costs = positional_costs_for_team(state, board, picks, on_clock)
    if not costs.empty:
        clock_picks = team_picks_from(state, on_clock, state.current_pick)
        whose = "Your" if on_clock == board.config.draft_position else f"Team {on_clock}'s"

# ---------------------------------------------------------------------------
# Control panel: what you can do, the price of waiting, and the countdown
# ---------------------------------------------------------------------------
# Cost and cliff answer the same question from two directions. Cost is the PRICE
# of waiting; the cliff is the COUNTDOWN and the reason for it. Beside the
# controls, "RB is urgent" arrives with "because 3 are left before a 46-point
# drop" AND the button you would press about it.
with st.expander("Controls + Metrics"):
    settings_col, price, countdown = st.columns([3, 4, 3])

    # --- left: the runner's own controls ------------------------------------
    with settings_col:
        st.caption("Controls")

        # A horizontal container rather than nested st.columns: it sizes each
        # child to its content, so the buttons stay legible in a narrow column
        # instead of being squeezed by a fixed ratio.
        with st.container(horizontal=True):
            if st.button("Undo", icon=":material/undo:", disabled=not state.picks):
                state.rewind_to(state.current_pick - 1)
                persist()
                st.rerun()

            with st.popover("Rewind"):
                target = st.number_input("Rewind to pick", 1, board.config.total_picks,
                                         value=max(1, state.current_pick - 1))
                if st.button("Rewind", type="primary"):
                    discarded = state.rewind_to(int(target))
                    persist()
                    st.toast(f"Discarded {discarded} pick(s).")
                    st.rerun()

            # Live drafts only. Opponents take players FFC has no ADP for --
            # kickers, defenses, a name you did not catch -- and that pick still
            # has to consume a pick number or every later one is off by one.
            # Recording a position keeps their roster count right too, so the
            # simulator keeps modelling their needs. In a sim the AI picks, so
            # this cannot arise.
            if not sim_mode and not state.is_complete:
                with st.popover("Pick not listed"):
                    st.caption(f"Team {state.on_the_clock} took someone not in the pool.")
                    unlisted_position = st.selectbox(
                        "Position", POSITIONS, key="dr_unlisted_pos",
                        help="Recorded so this team's roster count stays right.",
                    )
                    if st.button("Record pick", type="primary", key="dr_unlisted_go"):
                        state.make_pick(source="unknown", position=unlisted_position)
                        while state.apply_keeper_if_due():
                            pass
                        persist()
                        st.rerun()

        if sim_mode and not state.is_complete:
            with st.container(horizontal=True):
                if st.button("Pause" if playing else "Play",
                             icon=":material/pause:" if playing else ":material/play_arrow:",
                             type="primary", disabled=your_turn):
                    st.session_state[play_key] = not playing
                    st.rerun()

                if st.button("Step", icon=":material/skip_next:", disabled=your_turn):
                    advance_one()
                    st.rerun()

                if st.button("To my pick", icon=":material/fast_forward:",
                             disabled=your_turn):
                    advance_until_your_turn(state, board, boards)
                    persist()
                    st.rerun()

            if your_turn and playing:
                # Reached your turn: stop the clock rather than picking for you.
                st.session_state[play_key] = False
                playing = False

            # A FRAGMENT is a piece of the page Streamlit can rerun on its own,
            # and `run_every` makes it tick. Passing None when paused stops the
            # timer entirely rather than firing every three seconds to do nothing.
            @st.fragment(run_every="3s" if playing else None)
            def autoplay_tick():
                """Take one AI pick per tick while autoplay is running."""
                if not st.session_state.get(play_key):
                    return
                if state.is_complete or state.on_the_clock == board.config.draft_position:
                    st.session_state[play_key] = False
                    st.rerun(scope="app")
                    return
                advance_one()
                # scope="app" is essential. Without it only this fragment
                # refreshes, and the board, console and rosters keep showing the
                # previous pick.
                st.rerun(scope="app")

            autoplay_tick()

            if playing:
                st.caption(":material/autoplay: Simulating — one pick every 3 seconds.")
            elif your_turn:
                st.caption(":material/pan_tool: You are on the clock. Draft below.")

    # --- middle: what waiting costs -----------------------------------------
    with price:
        if clock_picks is not None:
            st.caption(f"{whose} cost of waiting — pick {clock_picks[0]} to their "
                       f"next at {clock_picks[1]}. Longest bar is the most urgent "
                       f"position; hover for the number.")
            st.altair_chart(cost_of_waiting_chart(costs),
                            width="stretch", height=200)
        else:
            st.caption("Cost of waiting")
            st.caption("Nothing left to wait on.")

    # --- right: how many are left before the drop ---------------------------
    with countdown:
        st.caption("Next drop-off — how many are left at each position before "
                   "value falls away, most urgent first.")
        cliffs = positional_cliffs(available["position"], available["projection"])
        if cliffs:
            st.dataframe(
                cliff_frame(cliffs).style.apply(
                    tint_positions_column(cliffs), axis=None),
                hide_index=True, width="stretch", height = 180,
                column_config=CLIFF_COLUMNS,
            )
        else:
            st.caption("Not enough players left to measure a drop-off.")

# "Both" first and default: while a sim runs you want to watch the board fill
# AND work the console, which is the whole reason this page has two views.
# The single-view options stay, because the full board is worth the screen when
# reviewing and the console is worth it when you are actually on the clock.
# required=True for the same reason as Mode above, and it matters even more
# here: a deselected view is neither board nor console, so the page would render
# the header and then stop dead with nothing underneath it.
view = st.segmented_control("View", ["Both", "Draft Console", "Draft Board"],
                            default="Both", key="dr_view", required=True)

# Belt and braces. `required=True` stops a deselect, but a value stored under
# this key by an OLDER version of the page could still be None or an option that
# no longer exists, and neither should blank the page.
if view not in ("Both", "Draft Console", "Draft Board"):
    view = "Both"

show_board = view in ("Both", "Draft Board")
show_console = view in ("Both", "Draft Console")

# ---------------------------------------------------------------------------
# Draft Board
# ---------------------------------------------------------------------------
if show_board:
    # Materialised into a list because BOTH grids are built from it. Left as a
    # generator, the first call would consume it and the second would silently
    # produce an empty grid -- and a board with no colour looks like a styling
    # bug rather than an exhausted iterator.
    entries = list(entries_from_pick_log(state.picks, label_by_id, position_by_id))

    grid = build_board_grid(entries, board.config,
                            my_slot=board.config.draft_position)
    positions_grid = build_position_grid(entries, board.config)

    # Sharing the screen means the board gets a shorter window and scrolls
    # inside it, rather than pushing the console below the fold -- a console you
    # have to scroll to reach is not much better than one you switched away from.
    full_height = min(80 + 35 * board.config.num_rounds, 700)
    st.dataframe(
        grid.style.apply(tint_by_position(positions_grid), axis=None),
        width="stretch",
        height=min(full_height, 320) if show_console else full_height,
        column_config=equal_column_widths(grid),
    )

    st.markdown(position_legend_html(), unsafe_allow_html=True)
    st.caption("Columns are team slots, so even rounds read right-to-left — "
               "that is the snake. Keepers are marked (K).")

    if show_console:
        st.divider()

# ---------------------------------------------------------------------------
# Draft Console
# ---------------------------------------------------------------------------
if not show_console:
    # Board-only view: nothing below, and the page ends here rather than falling
    # through into the console.
    st.stop()

# Sharing the screen with the board costs vertical room, so the console's two
# tables give some back. They scroll internally either way -- this only decides
# how much you see without scrolling the page itself.
console_height = 520 if show_board else 660

console, rosters = st.columns([3, 1])

with console:
    filters = st.columns([2, 3])
    with filters[0]:
        wanted_positions = st.pills(
            "Position", POSITIONS, selection_mode="multi",
            label_visibility="collapsed", key="dr_pos_filter",
        )
    with filters[1]:
        search = st.text_input("Search", placeholder="Filter by name...",
                               label_visibility="collapsed")

    # Both filters narrow the DATA, not just the display. The click handler
    # indexes into the frame passed to st.data_editor, so what is passed has to
    # be exactly what is shown -- filtering visually would draft the wrong man.
    shown = available
    if wanted_positions:
        shown = shown[shown["position"].isin(wanted_positions)]
    if search.strip():
        shown = shown[shown["name"].str.contains(search.strip(), case=False, na=False)]

    if shown.empty:
        st.info("No players match those filters.", icon=":material/filter_alt_off:")

    # Said out loud rather than swallowed. Silently starring fewer players than
    # you planned would look like the plan was wrong, when the real cause is a
    # name the roster no longer carries.
    if planned_unresolved:
        st.caption(
            f":material/warning: Not matched from your round {planned_round} plan: "
            f"{', '.join(planned_unresolved)}. They may have left the player pool "
            f"since you saved it."
        )

    # Which pick availability is measured at. On your own turn this looks one
    # pick FURTHER ahead than you might expect -- see avail_target_pick. None
    # means there is nothing useful to show, and the column is dropped entirely.
    avail_pick = avail_target_pick(state)
    next_pick_column = f"P@{avail_pick}" if avail_pick else None

    # Where the market has each player going. Keyed by canonical_id, which is
    # what the ADP comparison is built on.
    plat_adp_by_id = load_platform_adp(ctx, draft["platform"], draft["scoring_format"])

    # The simulation's own ADP, from the SAVED pre-draft run rather than the live
    # re-simulation -- an ADP is where a player goes in a draft that has not
    # started, so it must not move as this one unfolds.
    #
    # Keyed by ffc_player_id, NOT canonical_id: team defenses never resolve to a
    # canonical id, and they are in this pool.
    sim_adp_by_ffc = dict(zip(board.table["ffc_player_id"], board.simulated_adp))

    grid = pd.DataFrame({
        # A star for anyone already on your plan for this round. Made a real
        # column rather than a cell tint so it can be SORTED on, which groups
        # your targets together -- and because st.data_editor applies a Styler
        # only to non-editable columns anyway.
        "Plan": shown["canonical_id"].map(
            lambda cid: "★" if cid in planned_ids else ""
        ),
        # The button's LABEL is the player's name, which makes the handler robust
        # to sorting -- see draft_player below. It is also the only place the
        # name appears, so this column identifies the row.
        "Draft": shown["name"],
        "Tier": shown["tier"],
        "Proj": shown["projection"],
        # Both ADPs in the same ROUND.PICK encoding, so they read against each
        # other: the float 1.04 means round 1 pick 4 and still sorts numerically.
        "PlatAdp": shown["canonical_id"].map(plat_adp_by_id).map(
            lambda a: adp_to_round_pick(a, board.config.num_teams)
        ),
        "SimAdp": shown["ffc_player_id"].map(sim_adp_by_ffc).map(
            lambda a: adp_to_round_pick(a, board.config.num_teams)
        ),
    })
    if next_pick_column:
        grid["Avail"] = shown[next_pick_column]
        grid["Cost"] = shown["cost_of_waiting"]
    for category in MARKING_CATEGORIES:
        grid[category] = shown["canonical_id"].map(
            lambda cid, c=category: c in cats_by_id.get(cid, set())
        )

    # The frame the click handler will index into. Stored because the handler
    # runs BEFORE the next rerun rebuilds `shown`.
    st.session_state["dr_rows"] = shown.reset_index(drop=True)
    grid = grid.reset_index(drop=True)

    def draft_player():
        """Record the pick for whichever row's Draft button was clicked."""
        # In Draft Sim the other eleven teams pick themselves, so a click while
        # somebody else is on the clock would be you drafting for them.
        if sim_mode and state.on_the_clock != board.config.draft_position:
            st.toast("It is not your pick — press Play or Step to advance.",
                     icon=":material/info:")
            return

        click = st.session_state["dr_draft_click"]
        rows = st.session_state["dr_rows"]

        row = rows.iloc[click["row"]]

        # Cross-check: the button's label is the player's name. If the row index
        # and the label disagree -- which is what sorting the table would cause --
        # believe the label, because that is the button the user actually clicked.
        if row["name"] != click["label"]:
            matching = rows.index[rows["name"] == click["label"]]
            if len(matching) != 1:
                st.toast("Could not identify that player; try clearing the sort.",
                         icon=":material/error:")
                return
            row = rows.loc[matching[0]]

        state.make_pick(player_id=row["ffc_player_id"],
                        canonical_id=row["canonical_id"], source="user")
        while state.apply_keeper_if_due():
            pass
        persist()

    column_config = {
        "Plan": st.column_config.TextColumn(
            "Plan", width=40, pinned=True,
            help=(f"★ marks players you planned for round {planned_round} on the "
                  f"Draft Plan page. Sort by this column to group them."
                  if planned_round else "Your saved draft plan for this round."),
        ),
        "Draft": st.column_config.ButtonColumn(
            "Draft", key="dr_draft_click", on_click=draft_player,
            width="medium", pinned=True,
            help="Click a player to record him as this pick.",
        ),
        "Tier": st.column_config.NumberColumn("Tier", width=55, format="%d"),
        "Proj": st.column_config.NumberColumn("Proj", width=70, format="%.0f"),
        # "%.2f" turns the encoded float 1.04 into the text "1.04".
        "PlatAdp": st.column_config.NumberColumn(
            "PlatAdp", width=80, format="%.2f",
            help="Where your league's platform has him going, as ROUND.PICK. "
                 "Blank if that platform doesn't rank him.",
        ),
        "SimAdp": st.column_config.NumberColumn(
            "SimAdp", width=80, format="%.2f",
            help="The average pick he went at across the saved pre-draft "
                 "simulation, as ROUND.PICK. Does not move as this draft "
                 "unfolds. Blank for a kept player.",
        ),
    }
    if next_pick_column:
        column_config["Avail"] = st.column_config.ProgressColumn(
            "Avail", width=90, min_value=0.0, max_value=1.0, format="percent",
            help=(f"Chance he lasts to your pick at {avail_pick}"
                  + (" — your NEXT one, since you are on the clock now"
                     if state.on_the_clock == board.config.draft_position else "")),
        )
        column_config["Cost"] = st.column_config.NumberColumn(
            "Cost", width=70, format="%+.0f",
            help=("How much better he is than the best you could expect at his "
                  "position if you waited until your next pick. Positive means "
                  "passing costs you that much; negative means the position "
                  "should offer something better later, so spend this pick "
                  "elsewhere."),
        )
    # Headers, tooltips and widths come from presentation/marks.py, so this
    # console cannot disagree with the draft plan board or the team profile
    # editor. No position argument: every category shows on every row, which is
    # what the save loop below assumes.
    for column, settings in mark_column_config(editable=True).items():
        column_config[column] = st.column_config.CheckboxColumn(**settings)

    # The key changes every pick ON PURPOSE. st.data_editor tracks edits by row
    # position, and this table loses a row every pick -- a stale key would apply
    # a checkbox edit to whoever slid into that position.
    edited = st.data_editor(
        grid, hide_index=True, width="stretch", height=console_height,
        num_rows="fixed",              # anything else disables column sorting
        disabled=["Plan", "Tier", "Proj", "PlatAdp", "SimAdp", "Avail", "Cost"],
        column_config=column_config,
        key=f"dr_console_{len(state.picks)}",
    )

    # Save any marking checkbox the user just changed. Compared against what we
    # passed in, because the changing key above means edits cannot accumulate.
    for position, row in edited.iterrows():
        canonical_id = st.session_state["dr_rows"].iloc[position]["canonical_id"]
        # Markings are stored against a canonical id, so a player without one --
        # every team defense -- simply cannot carry marks. Skipping is what stops
        # a NaN being written into the markings collection as if it were an id.
        if pd.isna(canonical_id):
            continue
        chosen = {c for c in MARKING_CATEGORIES if bool(row[c])}
        if chosen != cats_by_id.get(canonical_id, set()):
            ctx.player_markings_service.save(
                draft["draft_id"], canonical_id, sorted(chosen),
                notes_by_id.get(canonical_id, ""),
            )

with rosters:
    # One dropdown decides what this column IS: a roster, or one of the two
    # strength views. A selectbox rather than a segmented control because three
    # labels will not sit across a quarter-width column.
    panel = st.selectbox(
        "Panel", ["Team", "Team Ratings", "By Category"],
        key="dr_rosters_panel",
    )

    if panel == "Team":
        team = st.selectbox("Team", range(1, board.config.num_teams + 1),
                            index=board.config.draft_position - 1,
                            format_func=lambda t: f"Team {t}"
                            + (" (you)" if t == board.config.draft_position else ""))
        lineup = slot_roster(team_players(state, board, team),
                             board.config.starting_slots)
        st.dataframe(roster_frame(lineup), hide_index=True, width="stretch",
                     height=console_height, column_config=ROSTER_COLUMNS)
    else:
        # Which rosters the numbers describe. Only means anything to the two
        # strength views, so it lives in this branch rather than above the split.
        # The explanation is a tooltip rather than a caption: in a column this
        # narrow, two sentences of prose cost more height than the table.
        fill_mode = st.selectbox(
            "Rosters", ["Projected final", "As drafted"],
            key="dr_strength_fill",
            help="**Projected final** fills every roster out by simulating the "
                 "rest of the draft — where each team is HEADING. Early on that "
                 "is mostly simulation, so it largely reflects draft slot.\n\n"
                 "**As drafted** counts only players actually taken so far — "
                 "what each team HAS. Empty lineup slots count at replacement "
                 "level, not zero.",
        )
        projected = fill_mode == "Projected final"

        # `roster` carries UDK's risk and upside ratings, joined on canonical id.
        # Passing it in is what adds the risk and upside rows; leave it out and
        # the rest of the panel is unaffected.
        #
        # Built inside this branch, so selecting "Team" skips the work entirely.
        strengths = team_strength_table(state, board, picks, projected=projected,
                                        ratings=roster)
        num_teams = board.config.num_teams

        if panel == "Team Ratings":
            st.dataframe(
                my_team_frame(strengths, board.config.draft_position)
                .style.apply(shade_ranks(num_teams), axis=None),
                hide_index=True, width="stretch", height=console_height,
                column_config=STRENGTH_COLUMNS,
            )
            st.caption(
                f"Rank is out of {num_teams}, 1 being best.",
                help="Best and worst are the league's, so you can see whether a "
                     "rank is a real gap or a crowd. Lower is better for "
                     "Replacement — the drop from your worst starter to your "
                     "best backup, so small means depth — and for Risk.\n\n"
                     "Risk and upside are measured against players projected "
                     "alike, so 0 is typical for the position and they say "
                     "something the projection does not. Upside counts your best "
                     f"{BEST_N_UPSIDE} — one real lottery ticket is the point, "
                     "and an average would let a bust cancel a boom. Risk is "
                     "weighted by projection, since a shaky RB1 matters and a "
                     "shaky WR3 does not. There is no bench risk on purpose: a "
                     "bench is where fliers belong.",
            )
        else:
            options = category_options(strengths)
            if not options:
                st.caption("Nothing to compare yet.")
            else:
                group, category = st.selectbox(
                    "Category", options, key="strength_category",
                    format_func=lambda key: category_label(*key),
                )
                ranked = category_frame(strengths, group, category)
                st.dataframe(
                    ranked.drop(columns="slot").style.apply(
                        highlight_my_team(ranked["slot"],
                                          board.config.draft_position),
                        axis=None),
                    hide_index=True, width="stretch", height=console_height,
                    column_config=STRENGTH_COLUMNS,
                )
                st.caption("Your row is shaded. "
                           + ("Lower is better here — it is the drop from the "
                              "worst starter to the best backup, so a small "
                              "number means depth."
                              if group in LOWER_IS_BETTER else
                              "Higher is better."))
