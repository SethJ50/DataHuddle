import pandas as pd
import streamlit as st

from streamlit_state import get_app_context
from scoring import ScoringFormat
from ui_helpers import draft_selector

ctx = get_app_context()

MARKING_OPTIONS = ["Safe", "Upside", "Late", "Early"]
POSITIONS = ["QB", "RB", "WR", "TE"]
BOARD_COLUMNS = ["Player"] # Only the columns the user actually edits
DISPLAY_COLUMNS = ["Player", "ADP"]

# Pretty labels just for showing the draft's saved settings read-only.
PLATFORM_LABELS = {"espn": "ESPN", "yahoo": "Yahoo", "sleeper": "Sleeper"}
FORMAT_LABELS = {ScoringFormat.HALF_PPR.value: "Half PPR", ScoringFormat.FULL_PPR.value: "Full PPR"}

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
    st.caption("Settings (from this draft)")
    st.write(f"**Teams:** {num_teams}")
    st.write(f"**Draft Position:** {draft_position}")
    st.write(f"**Rounds:** {num_rounds}")
    st.write(f"**Platform:** {PLATFORM_LABELS.get(platform, platform)}")
    st.write(f"**Scoring:** {FORMAT_LABELS.get(scoring_format.value, scoring_format.value)}")

top_c1, top_c2 = st.columns([3, 9])

with top_c1:
    # Get Pretty Pick Labels
    pick_labels_list = ctx.draft_plan_service.pick_labels(num_teams, draft_position, num_rounds)
    pick_options = [pick["label"] for pick in pick_labels_list]

    curr_round = st.selectbox("Round:", pick_options)

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

# Persistent store: {(round_label, position): [player_names]}
plans = st.session_state.setdefault("draft_plans", {})

for tab, position in zip(pos_tab, POSITIONS):
    with tab:
        by_name = by_name_by_position[position] # DF of Positional Players / Draft Info

        store_key = (curr_round, position)

        saved = [p for p in plans.get(store_key, []) if p in by_name.index]

        input_col, board_col = st.columns([3, 9])

        with input_col:
            with st.container(border = True, height = 600):
                with st.container(border = False, height = 80):
                    selected = st.multiselect(
                        "Players",
                        options=list(by_name.index),
                        default = saved,
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

                all_players = (
                    by_name["adp"]
                    .sort_values()
                    .reset_index()
                    .rename(columns={"display_name": "Player", "adp": "ADP"})
                )
                st.dataframe(all_players, hide_index=True, use_container_width=True)

        def bump(store_key, direction):
            # Purpose: move one player up or down in the priority list.
            # Reads which row was clicked from session_state, swaps it with its neighbor.
            click = st.session_state[f"reorder_{store_key}"]   # {"row": int, "label": str}
            order = plans[store_key]
            i = click["row"]
            j = i - 1 if "up" in click["label"] else i + 1
            if 0 <= j < len(order):
                order[i], order[j] = order[j], order[i] 

        with board_col:
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

            board["Safe"]   = board["canonical_id"].map(lambda cid: "Safe" in cats_by_id.get(cid, set()))
            board["Upside"] = board["canonical_id"].map(lambda cid: "Upside" in cats_by_id.get(cid, set()))
            board = board.drop(columns="canonical_id")

            st.dataframe(
                board, 
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Move": st.column_config.ButtonColumn(
                        "Move",
                        key=f"reorder_{store_key}",
                        on_click=bump, args=(store_key, None),
                    ),
                    "Player":     st.column_config.TextColumn("Player", width="medium"),
                    "ADP":        st.column_config.NumberColumn("ADP", width="small"),
                    "True Value": st.column_config.NumberColumn("True Value", width="small"),
                    "Diff":       st.column_config.NumberColumn("Diff", width="small"),
                    "Safe":       st.column_config.CheckboxColumn("Safe", width="small"),
                    "Upside":     st.column_config.CheckboxColumn("Upside", width="small"),
                },
            )