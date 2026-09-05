"""Compare where players go against every opinion about where they should.

Four sources of truth about draft position, side by side per player:

    Plat ADP / Plat Rk   what drafters on the platforms actually did
    Board Rk             the ranked list your platform SHOWS a drafter
    Sim ADP / Sim Rk     what this league's simulation produced
    FFB Rk               the Fantasy Footballers' overall board

The five coloured columns on the right are the point. Each is one source minus
another, and each answers a different draft-day question -- see the caption
above the table. Green means a player goes LATER than that source rates him,
which is where value hides; red means he goes EARLIER, so you would have to
reach for him.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import streamlit as st

from streamlit_state import get_app_context
from registry import Position
from presentation.adp_analysis_view import DIFF_COLUMNS, KEEPER_ADJUSTED, shape, style
from ui_helpers import draft_selector, load_sim_board, PLATFORM_LABELS

ctx = get_app_context()

YEAR = 2026

# What each difference column is actually asking. Shown on the page because the
# headings have to stay short enough that fifteen columns fit across a screen.
DIFF_QUESTIONS = {
    "Mkt vs Board": "who the platform's board pushes harder than drafters actually take",
    "Sim vs Board": "who might go earlier than the simulation expects",
    "Sim vs Tgt": "where the simulation missed the ADP it was aiming at",
    "Mkt vs FFB": "who is cheap (or expensive) at market price, against FFB's board",
    "Sim vs FFB": "who goes late (or early) in THIS draft, against FFB's board",
    "Board vs FFB": "where the platform's board and FFB's board simply disagree, "
                    "with nobody's drafting behaviour in between",
}

st.title("ADP analysis")

with st.sidebar:
    st.header("Draft")
    draft = draft_selector(ctx, "adp_analysis")
    if draft is None:
        st.stop()

# Filters live in the page body rather than the sidebar. They are the controls
# this page is actually used through, and behind a collapsible sidebar they are
# easy to miss entirely.
with st.container(border=True):
    filter_row = st.columns([5, 2], vertical_alignment="bottom")

    with filter_row[0]:
        # A segmented control rather than a dropdown: seven options fit on one
        # line, and every choice is visible without opening anything.
        position = st.segmented_control(
            "Position",
            ["All"] + [p.value for p in Position],
            default="All",
            key="adp_analysis_position",
        ) or "All"          # deselecting the active pill returns None

    with filter_row[1]:
        search = st.text_input(
            "Search player",
            placeholder="Search by name...",
            key="adp_analysis_search",
        )

# Shared with the Draft Plan and Sim Viewer pages, so the picks matrix is loaded
# once for all three rather than per page.
board, board_error = load_sim_board(ctx, draft, year=YEAR)

if board_error:
    st.warning(
        f"**No simulation for this draft's current settings.**\n\n"
        f"```\n{board_error}\n```\n\n"
        f"Run `python scripts/run_draft_sim.py --all` to build one.",
        icon=":material/warning:",
    )
    st.stop()

if board.stale:
    st.warning(
        "The player pool has changed since this simulation ran, so the "
        "simulated columns describe a slightly different set of players.",
        icon=":material/warning:",
    )


@st.cache_data(show_spinner="Lining up market, board, simulation and rankings...")
def get_analysis(signature: str, _config, _board):
    """Build the comparison table, reusing the result between reruns.

    Streamlit re-runs this whole file on every widget change, so without the
    `@st.cache_data` decorator above, typing a letter into the search box would
    rebuild the entire table from the database.

    Steps:
        1. Hand the config and the loaded board to the analysis service.

    Args:
        signature: The board's cache signature. Not read inside, but it IS the
            cache key -- it changes whenever the underlying board would differ,
            which is what makes the two arguments below safe to ignore.
        _config: The league's DraftConfig. The leading underscore tells
            Streamlit not to try to hash it.
        _board: The loaded DraftBoard, also unhashable.

    Returns:
        pd.DataFrame: One row per player in the simulation pool, with every
            ADP, rank, and difference column.
    """
    return ctx.adp_analysis_service.build(_config, _board)


analysis = get_analysis(
    ctx.draft_sim_service.board_signature(draft, YEAR), board.config, board)

has_keepers = bool(board.config.keepers)
platform_label = PLATFORM_LABELS.get(board.config.platform, board.config.platform)

# --- what the reader needs to know before trusting the numbers ---
notes = [
    f"**Board Rk** is {platform_label}'s"
    + (" published draft board." if board.config.platform in ("yahoo", "espn")
       else " ADP order, used as a stand-in — Sleeper publishes no board."),
    "**FFB Rk** is full PPR with 6-point passing TDs, the only version published, "
    "so it does not change with this league's scoring.",
    "**Kickers and defenses** have no market, board, or FFB columns — none of "
    "those sources cover them. Their simulated and target ADP are still real.",
]
if has_keepers:
    notes.insert(0,
        f"This league has **{len(board.config.keepers)} keepers**, who are hidden "
        f"here since nobody can draft them. {', '.join(KEEPER_ADJUSTED)} are "
        f"**adjusted** for them: everyone else moves up into the vacated slots. "
        f"**Tgt ADP** is adjusted too — it is what the simulation was built to "
        f"reproduce.")

st.caption("  \n".join(f"• {note}" for note in notes))

display_df = shape(analysis, position, search)

if display_df.empty:
    st.info("No players match these filters.", icon=":material/search_off:")
    st.stop()

# Narrow, fixed-width numeric columns are what let fifteen columns fit across a
# screen; the player name is pinned so it stays visible while scrolling right.
column_config = {
    "Player": st.column_config.TextColumn("Player", pinned=True, width=170),
    "Pos": st.column_config.TextColumn("Pos", width=52),
}
for name in ["Plat ADP", "Blend ADP", "Tgt ADP", "Sim ADP"]:
    column_config[name] = st.column_config.NumberColumn(name, format="%.1f", width=65)
for name in ["Plat Rk", "Board Rk", "Blend Rk", "Tgt Rk", "Sim Rk", "FFB Rk"]:
    column_config[name] = st.column_config.NumberColumn(name, format="%d", width=65)
# Two decimals on purpose: at the tail, 0.60% and 0.06% are a meaningful
# difference and both round to "1%".
column_config["Drafted %"] = st.column_config.NumberColumn(
    "Drafted %", format="percent", width=80,
    help="Share of simulated drafts he was taken in at all. Sim ADP is the "
         "average pick GIVEN that he went, so a low number here means the Sim "
         "columns describe a handful of drafts and should be read as noise.")

for name in DIFF_COLUMNS:
    column_config[name] = st.column_config.NumberColumn(
        name, format="%+d", width=88, help=DIFF_QUESTIONS.get(name))

st.dataframe(style(display_df), column_config=column_config,
             hide_index=True, height=620)

st.caption(
    f"{len(display_df)} players · "
    ":green[Green] = goes later than that source rates him (value) · "
    ":red[Red] = goes earlier (a reach). Each column is shaded on its own scale."
)

with st.expander("How the simulation gets from ADP to a board"):
    st.markdown(
        f"The ADP columns read left to right as the pipeline that actually runs:\n\n"
        f"1. **Plat ADP** — the three platforms' ADP blended together, weighted "
        f"`{board.config.drafting_platform_weight:.0%}` toward {platform_label} "
        f"because that is where this league drafts.\n"
        f"2. **Board Rk** — {platform_label}'s ranked list, a separate signal from "
        f"what drafters did.\n"
        f"3. **Blend ADP** — the same blend with each platform's board mixed into "
        f"its own share at `{board.config.board_rank_weight:.0%}`. **This is the "
        f"number the model receives.** It differs from Plat ADP only by the "
        f"board.\n"
        f"4. **Tgt ADP** — FFC's ADP shifted "
        f"`{board.config.platform_weight:.0%}` toward Blend ADP, then adjusted for "
        f"keepers and squeezed onto the pick numbers this draft can actually hand "
        f"out. **This is what the simulation was told to reproduce.**\n"
        f"5. **Sim ADP** — where players actually went across "
        f"{board.n_sims:,} simulated drafts. The gap back to Tgt ADP is "
        f"`Sim vs Tgt`, and it is the only column that measures the model rather "
        f"than the market."
    )

    st.markdown("---")
    for name, question in DIFF_QUESTIONS.items():
        st.markdown(f"**{name}** — {question}")

    unresolved = ctx.adp_analysis_service.unresolved_ffb()
    if unresolved:
        st.markdown(
            f"---\n**{len(unresolved)} Fantasy Footballers names could not be "
            f"matched to a player**, so they show no FFB Rk. Add them to "
            f"`player_id_map` to fix: {', '.join(sorted(unresolved)[:15])}"
            + (" …" if len(unresolved) > 15 else "")
        )
