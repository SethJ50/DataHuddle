"""Daily Fantasy page: a menu of exploratory plots built from nflreadpy data.

One page holding several unrelated charts, chosen from a dropdown, because each
one is a quick look rather than a workspace of its own. The plots answer
different questions -- who is outproducing expectation, which offences throw when
the score is not forcing them to, and which defences give fantasy points away --
and share only their filters and their data sources.

The page draws whatever `presentation/dfs_plot_registry.py` lists. It has no
knowledge of any particular plot, so adding one never means editing this file.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import streamlit as st

from draft_model.config import POSITIONS
from presentation.dfs_plot_registry import (
    MEASURE, MEASURE_OPTIONS, MINIMUM_GAMES, PLOTS, PLOTS_BY_LABEL,
    POSITION_FILTER, SEASON, SPLIT, SPLIT_OPTIONS, WEEKS,
)
from services.dfs_opportunity_service import seasons_available, week_range
from services.dfs_scoring import DfsScoring
from streamlit_state import get_app_context

ctx = get_app_context()
repo = ctx.dfs_read_repo

st.title("Basic Plots")
st.caption("Daily Fantasy")

# ---------------------------------------------------------------------------
# Which plot, and the scoring every number on it is in
# ---------------------------------------------------------------------------
picker, scoring_picker = st.columns([3, 2])

with picker:
    chosen = st.selectbox("Plot", [plot.label for plot in PLOTS],
                          label_visibility="collapsed")
    plot = PLOTS_BY_LABEL[chosen]

with scoring_picker:
    # Scoring changes every number on the page, so it sits beside the plot
    # picker rather than among the filters. `required` stops a second click on
    # the active option clearing the choice and returning None.
    #
    # Hidden for plots with no fantasy points in them at all -- a pass rate is
    # the same number whatever the contest pays for a catch, and offering the
    # choice would suggest otherwise.
    if plot.uses_scoring:
        scoring = st.segmented_control(
            "Scoring", [DfsScoring.FANDUEL, DfsScoring.PPR],
            default=DfsScoring.FANDUEL, key="dfs_scoring", required=True,
            label_visibility="collapsed",
        )
        scoring = scoring if scoring in tuple(DfsScoring) else DfsScoring.FANDUEL
    else:
        scoring = None

st.caption(plot.question)

# ---------------------------------------------------------------------------
# Filters -- only the ones this plot asked for
# ---------------------------------------------------------------------------
seasons = seasons_available(repo)
if not seasons:
    st.warning("No expected-points data loaded.", icon=":material/warning:")
    st.stop()

# Widget keys carry the plot's name so switching plots does not leave one plot's
# week range applied to another's filter.
def key(name):
    """Build a widget key unique to this plot and filter."""
    return f"dfs_{plot.key}_{name}"


selected = {}
controls = st.columns(max(len(plot.filters), 1))

for column, name in zip(controls, plot.filters):
    with column:
        if name == SEASON:
            selected[SEASON] = st.selectbox("Season", seasons, key=key(SEASON))

        elif name == WEEKS:
            # Drawn after the season so the range matches the season picked --
            # a completed year runs past week 18 into the playoffs, and one in
            # progress stops early.
            season = selected.get(SEASON, seasons[0])
            first, last = week_range(repo, season)

            # The season is IN THE KEY on purpose. A slider remembers its value
            # under its key, and switching to a season with a shorter run of
            # weeks would leave a remembered value outside the new limits, which
            # Streamlit refuses rather than quietly clamping. A per-season key
            # gives each season its own remembered range instead.
            selected[WEEKS] = (
                st.slider("Weeks", first, last, (first, last),
                          key=key(f"weeks_{season}"))
                if last > first else (first, last)
            )

        elif name == POSITION_FILTER:
            picked = st.multiselect(
                "Positions", [p for p in POSITIONS if p not in ("K", "DST")],
                default=list(plot.default_positions), key=key("positions"),
            )
            selected[POSITION_FILTER] = picked or None

        elif name == SPLIT:
            selected[SPLIT] = st.selectbox("Measuring", SPLIT_OPTIONS,
                                           key=key(SPLIT))

        elif name == MEASURE:
            chosen_measure = st.selectbox("Rank by", list(MEASURE_OPTIONS),
                                          key=key(MEASURE))
            selected[MEASURE] = MEASURE_OPTIONS[chosen_measure]

        elif name == MINIMUM_GAMES:
            selected[MINIMUM_GAMES] = st.number_input(
                "Min games", min_value=1, max_value=25, value=4, step=1,
                key=key(MINIMUM_GAMES),
                help="Players with fewer games than this are hidden. A short "
                     "sample sits further from the line than a long one purely "
                     "because it is short, which crowds out the players worth "
                     "looking at.",
            )

# ---------------------------------------------------------------------------
# The plot
# ---------------------------------------------------------------------------
# Filters route three ways: most describe WHICH DATA to fetch and go to the
# service, a few describe HOW TO DRAW IT and go to the chart, and one or two are
# needed by both -- `split` picks the columns AND titles the axes.
for_chart = {name: value for name, value in selected.items()
             if name in plot.chart_filters or name in plot.shared_filters}
for_build = {name: value for name, value in selected.items()
             if name not in plot.chart_filters}
if plot.uses_scoring:
    for_build["scoring"] = scoring

frame = plot.build(repo, **for_build)

if frame.empty:
    st.info("Nothing matches those filters.", icon=":material/filter_alt_off:")
    st.stop()

# `theme=None` MATTERS. Streamlit restyles Altair charts with its own theme by
# default, which overrides styling the chart set for itself -- and controlling
# exactly that is the only reason to use Altair here rather than st.scatter_chart.
st.altair_chart(plot.chart(frame, **for_chart), width="stretch", theme=None)

if plot.reading:
    st.caption(plot.reading)

# The table under the chart is not decoration: a chart shows you the shape and
# hides the ordering, and "who are the top ten" is the question people ask next.
#
# Written to suit ANY plot, like the rest of the page. Columns are dropped only
# if present, and Streamlit ignores column settings for columns a frame does not
# have -- so one config covers every plot and none of them needs a special case.
ID_COLUMNS = ("player_id",)

with st.expander(f"The numbers — {len(frame)} rows", expanded=False):
    st.dataframe(
        frame.drop(columns=[c for c in ID_COLUMNS if c in frame.columns]),
        hide_index=True, width="stretch", height=420,
        column_config={
            "name": st.column_config.TextColumn("Player", width=160),
            "position": st.column_config.TextColumn("Pos", width=55),
            "team": st.column_config.TextColumn("Team", width=60),
            "games": st.column_config.NumberColumn("G", width=45, format="%d"),
            "actual": st.column_config.NumberColumn("Actual", format="%.1f"),
            "expected": st.column_config.NumberColumn("Expected", format="%.1f"),
            "actual_per_game": st.column_config.NumberColumn("Act/g", format="%.1f"),
            "expected_per_game": st.column_config.NumberColumn("Exp/g", format="%.1f"),
            "gap_per_game": st.column_config.NumberColumn(
                "Gap/g", format="%+.1f",
                help="Actual minus expected, per game. Positive means he scored "
                     "more than his opportunities were worth.",
            ),
            # Team tendencies
            "pass_rate": st.column_config.NumberColumn("Pass%", format="%.1f%%"),
            "proe": st.column_config.NumberColumn(
                "PROE", format="%+.1f",
                help="Pass rate over expected, in percentage points. Positive "
                     "means they throw more than a typical team would in the "
                     "same spots."),
            "seconds_per_play": st.column_config.NumberColumn("Sec/play", format="%.1f"),
            "plays_per_game": st.column_config.NumberColumn("Plays/g", format="%.1f"),
            "red_zone_trips_per_game": st.column_config.NumberColumn(
                "RZ/g", format="%.2f", help="Drives reaching inside the 20."),
            "neutral_plays": st.column_config.NumberColumn("Neutral", format="%d"),
            # Defensive allowances
            "plays_faced": st.column_config.NumberColumn("Faced", format="%d"),
            "epa_per_play": st.column_config.NumberColumn(
                "EPA/play", format="%+.3f",
                help="Expected points added allowed. Higher means the defence "
                     "gives up more ground."),
            "points_allowed": st.column_config.NumberColumn("FP allowed", format="%.0f"),
            "points_per_play": st.column_config.NumberColumn("FP/play", format="%.3f"),
        },
    )
