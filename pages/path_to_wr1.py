"""Pre-draft page: which receivers could make the jump to a WR1 season.

A WR1 season is not a mystery -- it has a shape, and the players who produce one
were mostly not doing it the year before. This page puts the two facts side by
side: where a receiver actually finished last season, and where this season's
projections put him. The gap between those two numbers is the claim, and the
counting stats beside it are what that claim rests on.

Deliberately narrow. It does not score anybody, rank the candidates or say who
to draft -- it lays the evidence out in one table and leaves the judgement where
it belongs.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import pandas as pd
import streamlit as st

from presentation.colors import hex_to_rgba
from presentation.marks import MARK_COLORS, mark_header
from presentation.st_tables import highlight_where
from scoring import ScoringFormat, points_per_passing_td
from services.season_finish_service import latest_season, season_finishes
from streamlit_state import get_app_context
from ui_helpers import (adp_to_round_pick, draft_selector, load_platform_adp,
                        load_sim_board)

POSITION = "WR"

T12_CATEGORY = "New Top 12 Receiver"

T12_WASH = hex_to_rgba(MARK_COLORS[T12_CATEGORY], 0.35)

CRITERIA = """
- Finished **outside the top 12** receivers last year
- Drafted **outside the top 12** receivers this year
- **Past WR1 averages** — Targets 146 · Receptions 97.2 · Yards 1,305 · TDs 9.3
- **Lowest marks** — Yards 1,000 · TDs 6
"""

ctx = get_app_context()

st.title("Path to WR1")
st.caption("Pre-Draft")

with st.sidebar:
    st.header("Draft")
    draft = draft_selector(ctx, "path_to_wr1")
    if draft is None:
        st.stop()

platform = draft["platform"]
fmt_value = draft["scoring_format"]
fmt = ScoringFormat(fmt_value)
pass_td = points_per_passing_td(draft)
points_column = f"fantasy_points_{fmt_value}_season"


@st.cache_data(show_spinner="Ranking last season…")
def load_finishes(fmt_value, pass_td):
    """Work out where every receiver finished in the most recent season.

    Cached because it totals several seasons of game rows to answer a question
    that only changes when the scoring does.

    Steps:
        1. Read the season-long stat rows.
        2. Find the most recent season with games, since the season being
           drafted for has none yet.
        3. Rank that season's receivers with `season_finishes` from
           services/season_finish_service.py.

    Args:
        fmt_value: The scoring format as its stored string, such as "half_ppr".
            Also the cache key.

    Returns:
        tuple: `(finishes, season)` -- one row per receiver who appeared, and
            which season it describes. `(empty, None)` when nothing is loaded.
    """
    stats = ctx.nfl_read_repo.player_stats()
    season = latest_season(stats)
    if season is None:
        return pd.DataFrame(), None
    return season_finishes(stats, season, POSITION,
                           ScoringFormat(fmt_value), pass_td), season


@st.cache_data(show_spinner="Loading projections…")
def load_projected(fmt_value, pass_td):
    """Get every receiver the Fantasy Footballers project, ranked by points.

    Steps:
        1. Load the blended projections.
        2. Keep the receivers, and the columns this page shows.
        3. Rank them by projected points, which is what "projected finish"
           means.

    Args:
        fmt_value: The scoring format as its stored string. Also the cache key.

    Returns:
        pd.DataFrame: One row per projected receiver, with `canonical_id`,
            `name`, `team`, `receptions`, `receiving_yards`, `receiving_tds`,
            `projected_points`, `projected_finish` and
            `projected_finish_label`.
    """
    points = f"fantasy_points_{fmt_value}_season"
    projected = ctx.projections_service.get_own_projections(
        passing_td_points=pass_td)
    projected = projected[projected["position"] == POSITION].copy()

    projected["projected_finish"] = projected[points].rank(method="min",
                                                           ascending=False)
    projected["projected_finish_label"] = (
        POSITION + projected["projected_finish"].astype(int).astype(str))

    return projected.rename(columns={points: "projected_points"})[[
        "canonical_id", "name", "team", "receptions", "receiving_yards",
        "receiving_tds", "projected_points", "projected_finish",
        "projected_finish_label",
    ]]


projected = load_projected(fmt_value, pass_td)
if projected.empty:
    st.warning("No receiver projections loaded.", icon=":material/warning:")
    st.stop()

finishes, last_season = load_finishes(fmt_value, pass_td)

# Your own tags for THIS draft. Fetched fresh each rerun, which is one indexed
# read -- so a receiver tagged on another page shows up here without a reload.
t12_ids = {mark["canonical_id"]
           for mark in ctx.player_markings_service.all_for_draft(draft["draft_id"])
           if T12_CATEGORY in mark.get("categories", [])}

# ---------------------------------------------------------------------------
# Who to look at, and what you are looking for
# ---------------------------------------------------------------------------
picker, criteria = st.columns([7, 5])

with picker:
    names = list(projected.sort_values("projected_finish")["name"])
    chosen = st.multiselect(
        "Receivers", names, default=names, key="wr1_players",
        help="Every receiver the Fantasy Footballers project. All of them start "
             "selected; narrow it to compare a shortlist.",
    )

with criteria:
    with st.container(border=True):
        st.markdown("**Criteria**")
        st.markdown(CRITERIA)

if not chosen:
    st.info("Select a receiver to build the table.",
            icon=":material/filter_alt_off:")
    st.stop()

# ---------------------------------------------------------------------------
# The evidence
# ---------------------------------------------------------------------------
# Where the market has each receiver going, and what that makes him in draft
# order -- WR15 is the fifteenth receiver off the board.
#
# RANKED OVER EVERY PROJECTED RECEIVER, BEFORE THE SELECTION IS APPLIED. "The
# fifteenth receiver drafted" is a fact about the market, so narrowing the list
# above must not renumber it; ranking the selection would make a shortlist of
# three read WR1, WR2, WR3.
ranked = projected.copy()
ranked["plat_adp"] = ranked["canonical_id"].map(
    load_platform_adp(ctx, platform, fmt_value))

# Ranked on the RAW pick number, before the ROUND.PICK encoding below. Lower is
# earlier, so no descending flag.
ranked["drafted_finish"] = ranked["plat_adp"].rank(method="min")
ranked["drafted_label"] = ranked["drafted_finish"].map(
    lambda place: f"{POSITION}{int(place)}" if pd.notna(place) else None)

board = ranked[ranked["name"].isin(chosen)].copy()

# Where he finished last season. A receiver with no row simply did not play --
# a rookie, or a year lost to injury -- and a blank says that better than a
# number would.
board = board.merge(finishes[["canonical_id", "finish", "finish_label"]],
                    on="canonical_id", how="left")

board["PlatAdp"] = board["plat_adp"]

sim_board, sim_error = load_sim_board(ctx, draft, year=2026)
if sim_error:
    board["SimAdp"] = float("nan")
else:
    sim_adp = {canonical_id: mean_pick
               for canonical_id, mean_pick in zip(sim_board.table["canonical_id"],
                                                  sim_board.simulated_adp)
               if isinstance(canonical_id, str)}
    board["SimAdp"] = board["canonical_id"].map(sim_adp)

# Both ADPs in the same ROUND.PICK encoding, so they read against each other.
for column in ("PlatAdp", "SimAdp"):
    board[column] = board[column].map(
        lambda pick: adp_to_round_pick(pick, draft["num_teams"]))

# Draft order: the sequence you will actually face. `na_position="last"` keeps
# the unranked at the bottom rather than floating them to the top as blanks.
board = board.sort_values("PlatAdp", na_position="last")

table = pd.DataFrame({
    "Player": board["name"],
    "Team": board["team"],
    "Last yr": board["finish_label"],
    "Proj": board["projected_finish_label"],
    "Drafted": board["drafted_label"],
    "Rec": board["receptions"],
    "ReYd": board["receiving_yards"],
    "ReTD": board["receiving_tds"],
    "PlatAdp": board["PlatAdp"],
    "SimAdp": board["SimAdp"],
})

# Highlight the receivers you have already tagged, so a shortlist you built
# elsewhere is visible against the evidence rather than held in your head.
styled = table.style.apply(
    highlight_where("Player", board["canonical_id"].isin(t12_ids), T12_WASH),
    axis=None)

st.dataframe(
    styled, hide_index=True, width="stretch", height=620,
    column_config={
        "Player": st.column_config.TextColumn("Player", width="medium"),
        "Team": st.column_config.TextColumn("Team", width=60),
        "Last yr": st.column_config.TextColumn(
            f"Last yr", width=75,
            help=f"Where he finished among receivers in {last_season}, by "
                 f"points actually scored in this draft's scoring. Blank means "
                 f"he did not play."),
        "Proj": st.column_config.TextColumn(
            "Proj", width=70,
            help="Where the Fantasy Footballers' blended projection puts him "
                 "among receivers this season."),
        "Drafted": st.column_config.TextColumn(
            "Drafted", width=80,
            help="Which receiver off the board he is by platform ADP -- WR15 "
                 "means fourteen receivers are drafted before him. Ranked over "
                 "every projected receiver, so narrowing the list above does "
                 "not renumber it. Blank if the platform does not rank him."),
        "Rec": st.column_config.NumberColumn("Rec", width=65, format="%.1f"),
        "ReYd": st.column_config.NumberColumn("ReYd", width=70, format="%.0f"),
        "ReTD": st.column_config.NumberColumn("ReTD", width=65, format="%.1f"),
        # "%.2f" turns the encoded float 1.04 into the text "1.04".
        "PlatAdp": st.column_config.NumberColumn(
            "PlatAdp", width=80, format="%.2f",
            help=f"Where {platform.upper()} has him going, as ROUND.PICK."),
        "SimAdp": st.column_config.NumberColumn(
            "SimAdp", width=80, format="%.2f",
            help="The average pick he went at across the simulated drafts, as "
                 "ROUND.PICK. Blank when no simulation has been run for this "
                 "draft's settings."),
    },
)

if t12_ids:
    st.caption(f"A shaded name is one you tagged **{mark_header(T12_CATEGORY)} — "
               f"{T12_CATEGORY}** on this draft. {len(t12_ids & set(board['canonical_id']))} "
               f"of the receivers shown.")
else:
    st.caption(f"Tag a receiver **{T12_CATEGORY}** on the Player Profile or "
               f"Draft Plan page and his name is shaded here.")

st.caption(f"{len(table)} receivers · last year is {last_season} · "
           f"projections and finishes both scored in "
           f"{'Full PPR' if fmt is ScoringFormat.FULL_PPR else 'Half PPR' if fmt is ScoringFormat.HALF_PPR else 'Standard'}.")
