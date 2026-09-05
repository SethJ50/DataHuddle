"""Pre-draft page: how the twelve teams in a simulated draft compare.

The Draft Runner answers "how do the rosters compare" while a draft is actually
happening. This asks it before one starts, from the saved simulation: run ten
thousand drafts, score every team on every category, and average.

That average is a statement about the DRAFT SLOT, not about a manager. Every
simulated team drafts by the same rules, so the only thing separating team 3
from team 9 is where they sit in the order. Reading it that way is the point --
it is where you find out that picking fourth tends to leave you thin at tight
end, before you are on the clock.

Read-only. Nothing here changes a draft, a plan, or an artifact; it only reads
what scripts/run_draft_sim.py already wrote.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import streamlit as st

from presentation.team_strengths import (
    category_frame, category_label, category_options, highlight_my_team,
    my_team_frame, shade_ranks,
)
from services.draft_runner_service import (
    BEST_N_UPSIDE, LOWER_IS_BETTER, simulated_strength_table,
)
from streamlit_state import get_app_context
from ui_helpers import PLATFORM_LABELS, draft_selector, load_sim_board

YEAR = 2026

TABLE_HEIGHT = 560

# Shared by both views. The two frames have different columns, and a key that is
# absent from a frame is simply ignored, so one mapping covers both.
STRENGTH_COLUMNS = {
    "Category": st.column_config.TextColumn("Category", width=130),
    "Team": st.column_config.TextColumn("Team", width=110),
    "Value": st.column_config.NumberColumn(
        "Value", width=70, format="%.1f",
        help="Projected fantasy points, except for Replacement, which is a gap "
             "in points between two players.",
    ),
    "Rank": st.column_config.NumberColumn(
        "Rank", width=60, format="%.0f",
        help="Where this team places on this category, 1 being best. Blank "
             "means it could not be measured -- not that the team is last.",
    ),
    "Best": st.column_config.NumberColumn(
        "Best", width=70, format="%.1f",
        help="The best any team in the league manages on this category.",
    ),
    "Worst": st.column_config.NumberColumn(
        "Worst", width=70, format="%.1f",
        help="The worst any team in the league manages on this category.",
    ),
}

ctx = get_app_context()

st.title("Team Comparison")
st.caption("Pre-Draft")

with st.sidebar:
    st.header("Draft")
    draft = draft_selector(ctx, "team_comparison")
    if draft is None:
        st.stop()

# Shared cache entry with the Draft Plan and Sim Viewer pages, so the picks
# matrix is loaded once for all three rather than copied per page.
board, board_error = load_sim_board(ctx, draft, year=YEAR)

if board_error:
    st.warning(
        f"**No simulation for this draft's current settings.**\n\n"
        f"```\n{board_error}\n```",
        icon=":material/warning:",
    )
    st.stop()

if board.stale:
    st.warning(
        "The player pool has changed since this simulation ran, so the numbers "
        "below describe a slightly different set of players. Re-run "
        "`scripts/run_draft_sim.py`.",
        icon=":material/warning:",
    )

config = board.config
num_teams = config.num_teams
my_slot = config.draft_position

st.caption(
    f"{board.n_sims:,} simulated drafts · {num_teams} teams, pick {my_slot}, "
    f"{config.num_rounds} rounds · "
    f"drafting on {PLATFORM_LABELS.get(config.platform, config.platform)} · "
    f"{'calibrated' if board.calibrated else 'NOT calibrated (raw ADP)'}"
)

# UDK's risk and upside ratings, keyed by canonical id. Passing them in is what
# adds the risk and upside rows; leave them out and the rest is unaffected.
ratings = ctx.roster_service.roster()[["canonical_id", "risk", "upside"]]


@st.cache_data(show_spinner="Scoring every team…")
def load_strengths(_board, _ratings, signature):
    """Score every team on every category, cached so a dropdown is instant.

    The work is one pass over the whole picks matrix per team, which is fast but
    not free, and neither dropdown on this page changes the answer -- both only
    change which slice of it is shown. So it is computed once per draft.

    Steps:
        1. Call `simulated_strength_table` from
           services/draft_runner_service.py, which builds each team's roster
           from the saved picks matrix and averages every category across the
           simulations.

    Args:
        _board: The DraftBoard. The leading underscore keeps Streamlit from
            trying to hash it -- it holds a multi-million-entry numpy array, and
            `signature` below already identifies it exactly.
        _ratings: The risk and upside frame, excluded from the key for the same
            reason.
        signature: The board signature string, which changes whenever the draft
            settings or the artifact do. This is the real cache key.

    Returns:
        pd.DataFrame: One row per category, one column per team slot, with a
            two-level (group, category) row index.
    """
    return simulated_strength_table(_board, ratings=_ratings)


strengths = load_strengths(board, ratings,
                           ctx.draft_sim_service.board_signature(draft, year=YEAR))

# ---------------------------------------------------------------------------
# Controls on the left, the table on the right
# ---------------------------------------------------------------------------
controls, output = st.columns([1, 2])

with controls:
    view = st.segmented_control(
        "View", ["Team Ratings", "By Category"], default="Team Ratings",
        key="tc_view", width="stretch",
        help="**Team Ratings** puts one team against the field on every "
             "category at once — the view for spotting *1st at WR, 11th at "
             "TE*.\n\n**By Category** ranks every team on one category, which "
             "is where you see who those ten teams ahead of you are.",
    )

    if view == "Team Ratings":
        team = st.selectbox(
            "Team", range(1, num_teams + 1),
            index=my_slot - 1, key="tc_team",
            format_func=lambda slot: f"Team {slot}"
            + (" (you)" if slot == my_slot else ""),
        )
    else:
        options = category_options(strengths)
        chosen = st.selectbox(
            "Category", options, key="tc_category",
            format_func=lambda key: category_label(*key),
        ) if options else None

with output:
    if view == "Team Ratings":
        # One row per category: this team's value, its rank, and the league's
        # best and worst so a rank comes with a sense of the spread behind it.
        st.dataframe(
            my_team_frame(strengths, team)
            .style.apply(shade_ranks(num_teams), axis=None),
            hide_index=True, width="stretch", height=TABLE_HEIGHT,
            column_config=STRENGTH_COLUMNS,
        )
        st.caption(
            f"Team {team} against the other {num_teams - 1}. Rank is out of "
            f"{num_teams}, 1 being best.",
            help="Best and worst are the league's, so you can see whether a "
                 "rank is a real gap or a crowd. Lower is better for "
                 "Replacement — the drop from the worst starter to the best "
                 "backup, so small means depth — and for Risk.\n\n"
                 "Risk and upside are measured against players projected "
                 "alike, so 0 is typical for the position and they say "
                 "something the projection does not. Upside counts the best "
                 f"{BEST_N_UPSIDE} — one real lottery ticket is the point, and "
                 "an average would let a bust cancel a boom. Risk is weighted "
                 "by projection, since a shaky RB1 matters and a shaky WR3 "
                 "does not.",
        )
    elif not options:
        st.info("Nothing to compare in this simulation.",
                icon=":material/filter_alt_off:")
    else:
        # One row per team on the chosen category, best first.
        group, category = chosen
        ranked = category_frame(strengths, group, category)
        st.dataframe(
            ranked.drop(columns="slot").style.apply(
                highlight_my_team(ranked["slot"], my_slot), axis=None),
            hide_index=True, width="stretch", height=TABLE_HEIGHT,
            column_config=STRENGTH_COLUMNS,
        )
        st.caption(
            "Your row is shaded. "
            + ("Lower is better here — it is the drop from the worst starter "
               "to the best backup, so a small number means depth."
               if group in LOWER_IS_BETTER else "Higher is better."))

st.caption(
    f"Every number is an average across all {board.n_sims:,} simulated drafts, "
    f"so it describes what a draft slot TENDS to produce rather than any one "
    f"roster. Each simulated team drafts by the same rules — the only thing "
    f"separating them is where they pick.")
