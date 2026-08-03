import streamlit as st

from streamlit_state import get_app_context
from scoring import ScoringFormat
from ui_helpers import draft_selector, adp_to_round_pick, load_sim_board
from registry import MARKING_CATEGORIES          # single source of truth for the marks
from presentation.marks import CATEGORY_STYLE, MARK_COLOR_BY_LABEL
from presentation.st_tables import highlight_true
from draft_model.queries import prob_any_available

ctx = get_app_context()

POSITIONS = ["QB", "RB", "WR", "TE"]

# Header overrides for board columns; anything not listed uses its own name.
BOARD_COLUMN_LABELS = {"True Value": "TrVal"}

# Fixed pixel width for the mark (checkbox) columns.
MARK_COLUMN_WIDTH = 45


def board_header(name):
    # Display header for a board column: an override if one exists, else the name.
    return BOARD_COLUMN_LABELS.get(name, name)


def bump(store_key):
    # Purpose: move one player up or down in a board's priority list.
    # Reads which arrow was clicked from session_state and swaps that row with
    # its neighbor. Registered as the Move column's on_click callback.
    click = st.session_state[f"reorder_{store_key}"]   # {"row": int, "label": str}
    order = plans[store_key]
    i = click["row"]
    j = i - 1 if "up" in click["label"] else i + 1
    if 0 <= j < len(order):
        order[i], order[j] = order[j], order[i]

# Place draft selector + display in sidebar
with st.sidebar:
    st.header("Draft")

    draft = draft_selector(ctx, "draft_plan")
    if draft is None:
        st.stop()

    # Settings now LIVE ON the draft -- read them instead of collecting them
    # from live widgets. scoring_format was stored as its .value string, so we
    # rebuild the enum from it; platform is already the "espn"/"yahoo"/... code.
    num_teams = draft["num_teams"]
    draft_position = draft["draft_position"]
    num_rounds = draft["num_rounds"]
    platform = draft["platform"]
    scoring_format = ScoringFormat(draft["scoring_format"])

    st.divider()
    # Save every round/position's selected players for THIS draft to Mongo. The
    # live plan is read from session_state (built as the boards render on each
    # rerun), so all visited rounds are captured -- not just the open one.
    if st.button("Save draft plan", icon=":material/save:", type="primary", width="stretch"):
        ctx.draft_plan_service.save_plan(draft["draft_id"], st.session_state.get("draft_plans", {}))
        st.toast("Draft plan saved.", icon=":material/check_circle:")

top_c1, _ = st.columns([3, 9])

with top_c1:
    # Get Pretty Pick Labels
    pick_labels_list = ctx.draft_plan_service.pick_labels(num_teams, draft_position, num_rounds)
    pick_options = [pick["label"] for pick in pick_labels_list]

    curr_round = st.selectbox("Round:", pick_options)

# The overall pick number for the selected round, and the one after it. Every
# availability question is "will he last until I pick again", so both are needed.
_pick_by_label = {p["label"]: p["overall_pick"] for p in pick_labels_list}
current_pick = _pick_by_label[curr_round]
_later = [p["overall_pick"] for p in pick_labels_list if p["overall_pick"] > current_pick]
next_pick = _later[0] if _later else current_pick


# Shared with the Sim Viewer page, so the two pages reuse one loaded picks
# matrix rather than each holding their own copy.
sim_board, board_error = load_sim_board(ctx, draft, year=2026)

if board_error:
    # A warning rather than an info box, deliberately. The Avail column goes
    # blank when there's no simulation -- but it ALSO goes blank for a player
    # who simply isn't in the simulated pool, which is a normal and different
    # thing. Without a loud banner those two blanks look identical, and you'd
    # have no way to tell "no data yet" from "this player isn't modelled".
    st.warning(
        f"**No availability data for these draft settings.** The columns below "
        f"will be blank until a simulation is run.\n\n"
        f"Changing teams, rounds, scoring format, platform or keepers invalidates "
        f"the saved run — those change how the draft unfolds. (Changing your "
        f"lineup slots or draft position does not.)\n\n"
        f"```\n{board_error}\n```",
        icon=":material/warning:",
    )
    availability_by_id, cost_by_id = {}, {}
else:
    if sim_board.stale:
        st.warning(
            "The player pool has changed since this simulation ran, so availability "
            "numbers may be out of date. Re-run `scripts/run_draft_sim.py`.",
            icon=":material/warning:",
        )

    # Availability at the selected round's pick; cost of waiting measured against
    # the NEXT pick, since that's the actual decision being made.
    _sim = sim_board.availability(target_picks=[current_pick, next_pick]).dropna(subset=["canonical_id"])
    availability_by_id = dict(zip(_sim["canonical_id"], _sim[f"P@{current_pick}"]))
    cost_by_id = dict(zip(_sim["canonical_id"], _sim["cost_of_waiting"]))

    with st.container(border=True):
        st.caption(
            f"Cost of waiting from pick {current_pick} to pick {next_pick} — "
            f"expected points lost by not addressing a position now "
            f"({sim_board.n_sims:,} simulated drafts)"
        )
        costs = sim_board.positional_costs(at_pick=current_pick, next_pick=next_pick)
        with st.container(horizontal=True):
            for _, row in costs.iterrows():
                st.metric(row["position"], f"{row['cost']:.0f}",
                          help=f"Best available VORP at this position: "
                               f"{row['best_available_vorp']:.0f}")

pos_tab = st.tabs(POSITIONS)


@st.cache_data(show_spinner="Ranking candidates...")
def get_candidates_by_position(platform: str, fmt: ScoringFormat) -> dict:
    return {
        position: ctx.draft_plan_service.rank_candidates(position, platform, fmt).set_index("display_name")
        for position in POSITIONS
    }

# {'POS': DF [canonical_id, adp, projected_points, adp_rank, true_value_rank, dif]}
by_name_by_position = get_candidates_by_position(platform, scoring_format)

# All markings for THIS draft, fetched once per rerun
# {canonical_id: set(categories)}
marks = ctx.player_markings_service.all_for_draft(draft["draft_id"])
cats_by_id = {m["canonical_id"]: set(m.get("categories", [])) for m in marks}

# canonical_id -> the player's saved note string ("" when they have none).
# Same shape as cats_by_id, just the free-text notes instead of the categories.
notes_by_id = {m["canonical_id"]: m.get("notes", "") for m in marks}

# Persistent store: {(round_label, position): [player_names]}. Restored from the
# draft's saved plan in Mongo the first time we see this draft (and whenever the
# sidebar switches to a different draft); the sidebar's Save button writes it back.
# On a fresh page load there are no multiselect widget states yet, so the restored
# players flow into each board's `default=saved`.
if st.session_state.get("loaded_plan_draft_id") != draft["draft_id"]:
    st.session_state["draft_plans"] = ctx.draft_plan_service.get_plan(draft["draft_id"])
    st.session_state["loaded_plan_draft_id"] = draft["draft_id"]

plans = st.session_state["draft_plans"]

for tab, position in zip(pos_tab, POSITIONS):
    with tab:
        by_name = by_name_by_position[position]  # DF of Positional Players / Draft Info

        store_key = (curr_round, position)

        saved = [p for p in plans.get(store_key, []) if p in by_name.index]

        input_col, board_col = st.columns([3, 9])

        with input_col:
            with st.container(border=True, height=600):
                with st.container(border=False, height=80):
                    selected = st.multiselect(
                        "Players",
                        options=list(by_name.index),
                        default=saved,
                        key=f"select_{draft['draft_id']}_{curr_round}_{position}"
                    )
                # The multiselect controls WHICH players are on the board (membership).
                # Priority ORDER lives separately in plans[store_key]: keep the existing
                # order for players still selected, then append any newly added picks.
                # This lets the Move arrows reorder players without the multiselect
                # (which keeps its own click order) clobbering our ranking.
                prev_order = plans.get(store_key, [])
                ordered = [p for p in prev_order if p in selected]
                ordered += [p for p in selected if p not in ordered]
                plans[store_key] = ordered

                # canonical_id comes along only to join the simulation on, then
                # gets dropped -- it means nothing to a reader.
                all_players = (
                    by_name[["canonical_id", "adp"]]
                    .sort_values("adp")
                    .reset_index()
                    .rename(columns={"display_name": "Player", "adp": "ADP"})
                )

                all_players["ADP"] = all_players["ADP"].map(
                    lambda a: adp_to_round_pick(a, num_teams)
                )
                # Blank, not zero, for anyone the model has no row for -- a player
                # outside the simulated pool is UNKNOWN, and showing 0% would say
                # "certain to be gone", which is the opposite of what it means.
                all_players["Avail"] = all_players["canonical_id"].map(availability_by_id)
                all_players = all_players.drop(columns="canonical_id")

                st.dataframe(
                    all_players,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        # "%.2f" turns the encoded float 1.04 into the text "1.04".
                        "ADP": st.column_config.NumberColumn("ADP", format="%.2f"),
                        "Avail": st.column_config.ProgressColumn(
                            "Avail", width=90, min_value=0.0, max_value=1.0,
                            format="percent",
                            help=f"Chance he's still on the board at pick {current_pick}",
                        ),
                    },
                )

        with board_col:
            # The players you've shortlisted for this round ARE a tier, so ask the
            # simulation the question that individual percentages cannot answer:
            # will AT LEAST ONE of them get back to you? Four players at 30% each
            # is not a 30% problem, and it is not a 76% one either -- they compete
            # for the same picks, so one going early is what lets another survive.
            if sim_board is not None and ordered:
                tier_ids = [by_name.loc[p, "canonical_id"] for p in ordered]
                tier_rows = sim_board.table.index[
                    sim_board.table["canonical_id"].isin(tier_ids)
                ].tolist()
                if tier_rows:
                    survival = prob_any_available(
                        sim_board.artifact.picks, tier_rows, current_pick
                    )
                    st.caption(
                        f"**{survival:.0%}** chance at least one of these "
                        f"{len(tier_rows)} is available at pick {current_pick}"
                    )

            # Build the board in priority order (ordered), NOT the raw multiselect order.
            board = by_name.loc[
                ordered, ["canonical_id", "adp", "true_value_rank", "diff"]
            ].reset_index()

            board = board.rename(columns={
                "display_name": "Player",
                "adp": "ADP",
                "true_value_rank": "True Value",
                "diff": "Diff",
            })

            board["Move"] = [[":material/arrow_upward: up", ":material/arrow_downward: down"]] * len(board)

            # Show ADP as ROUND.PICK instead of the raw number. Board rows are
            # already in priority order, so ADP isn't used for sorting here.
            board["ADP"] = board["ADP"].map(lambda a: adp_to_round_pick(a, num_teams))

            # Simulation columns. Blank rather than zero for a player the model
            # has no row for -- "unknown" and "certain to be gone" are very
            # different answers and must not look alike.
            board["Avail"] = board["canonical_id"].map(availability_by_id)
            board["Cost"] = board["canonical_id"].map(cost_by_id)

            # Which categories show on THIS position's board. A mark with a
            # `position` only appears on that tab; one without shows everywhere.
            visible_cats = [
                (c, CATEGORY_STYLE.get(c, {}).get("label", c))
                for c in MARKING_CATEGORIES
                if CATEGORY_STYLE.get(c, {}).get("position", position) == position
            ]

            # One read-only checkbox column per visible mark. The lambda checks
            # the full category name against the saved markings; the column is
            # named by the short label.
            for category, label in visible_cats:
                board[label] = board["canonical_id"].map(
                    lambda cid, category=category: category in cats_by_id.get(cid, set())
                )

            # A single Notes column showing each player's saved note ("" if none).
            board["Notes"] = board["canonical_id"].map(lambda cid: notes_by_id.get(cid, ""))

            board = board.drop(columns="canonical_id")

            column_config = {
                "Move": st.column_config.ButtonColumn(
                    board_header("Move"),
                    key=f"reorder_{store_key}",
                    on_click=bump, args=(store_key,),
                    width=60,
                ),
                "Player":     st.column_config.TextColumn(board_header("Player"), width="medium"),
                "ADP":        st.column_config.NumberColumn(board_header("ADP"), width=60, format="%.2f"),
                "True Value": st.column_config.NumberColumn(board_header("True Value"), width=70),
                "Diff":       st.column_config.NumberColumn(board_header("Diff"), width=60),
                # A progress bar reads faster than a number for a probability --
                # you scan for "is this bar short", not for two decimal places.
                "Avail": st.column_config.ProgressColumn(
                    board_header("Avail"), width=90, min_value=0.0, max_value=1.0,
                    format="percent",
                    help=f"Chance he's still on the board at pick {current_pick}",
                ),
                "Cost": st.column_config.NumberColumn(
                    board_header("Cost"), width=70, format="%.0f",
                    help=f"Expected points lost if you pass now and he's gone by "
                         f"pick {next_pick}",
                ),
            }

            # One checkbox column per visible mark, all a fixed narrow width.
            for category, label in visible_cats:
                column_config[label] = st.column_config.CheckboxColumn(label, width=MARK_COLUMN_WIDTH)

            column_config["Notes"] = st.column_config.TextColumn(board_header("Notes"), width="medium")

            # Tint each mark cell by its color when checked. Only style columns
            # present on this tab, or Styler would error on a missing column.
            present_mark_cols = [c for c in MARK_COLOR_BY_LABEL if c in board.columns]
            styled = board.style.apply(
                highlight_true(MARK_COLOR_BY_LABEL), subset=present_mark_cols, axis=None
            )

            st.dataframe(
                styled,
                hide_index=True,
                width="stretch",
                column_config=column_config,
            )
