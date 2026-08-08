"""Daily Fantasy page: one player, his price, his form, and his game log.

The Daily Fantasy counterpart to pages/player_profile.py. That page is built for
drafting a player for a season; this one is built for buying him for a single
week, so it leads with salary and recent form rather than notes and tags.

Two columns. The narrow left one identifies the player and holds what he costs;
the wide right one holds a rolling summary and five game logs, each answering a
different question about him.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import numpy as np
import pandas as pd
import streamlit as st

from presentation.dfs_gamelog import shape, tabs_for
from services.dfs_player_service import player_weeks, rolling_form
from services.dfs_scoring import DfsScoring
from streamlit_state import get_app_context

FORM_GAMES = 5

ctx = get_app_context()
repo = ctx.dfs_read_repo
salaries = ctx.dfs_salary_repo

st.title("Player Profile")
st.caption("Daily Fantasy")


@st.cache_data(show_spinner="Reading salaries…")
def load_prices():
    """Read the most recent slate's prices, both sites.

    Steps:
        1. Ask which weeks have prices loaded.
        2. Read the newest of them.

    Returns:
        tuple: `(frame, season, week)`, or `(None, None, None)` if no slate has
            ever been loaded.
    """
    loaded = salaries.available_slates()
    if loaded.empty:
        return None, None, None

    newest = loaded.iloc[0]
    season, week = int(newest["season"]), int(newest["week"])
    return salaries.slate(season, week), season, week


@st.cache_data(show_spinner="Joining player data…")
def load(scoring):
    """Build the player-week table once per scoring choice.

    Six sources get joined to make this, which is slow enough to be worth
    caching and small enough to keep. Cached on the scoring alone, because
    nothing else about it varies.

    Args:
        scoring: Which scoring system the points columns should be in.

    Returns:
        pd.DataFrame: The table from `player_weeks`.
    """
    return player_weeks(repo, scoring)


# ---------------------------------------------------------------------------
# Which player, and in whose scoring
# ---------------------------------------------------------------------------
heading = st.columns([3, 2, 3])

with heading[1]:
    scoring = st.segmented_control(
        "Scoring", list(DfsScoring),
        default=DfsScoring.FANDUEL, key="dfs_profile_scoring", required=True,
        label_visibility="collapsed",
    )
    scoring = scoring if scoring in tuple(DfsScoring) else DfsScoring.FANDUEL

frame = load(scoring)
if frame.empty:
    st.warning("No player data loaded.", icon=":material/warning:")
    st.stop()

seasons = sorted(frame["season"].dropna().unique(), reverse=True)
with heading[2]:
    season = st.selectbox("Season", seasons, key="dfs_profile_season")

season_rows = frame[frame["season"] == season]

# Ordered by how much the player actually played, so the names worth looking at
# are near the top of a dropdown holding a few thousand of them.
busiest = (season_rows.groupby(["canonical_id", "name"], as_index=False)
           .agg(snaps=("offense_snaps", "sum"))
           .sort_values("snaps", ascending=False))
names = list(busiest["name"])
id_by_name = dict(zip(busiest["name"], busiest["canonical_id"]))

left, right = st.columns([3, 9])

with left:
    image_slot = st.empty()
    chosen_name = st.selectbox("Player", names, index=0,
                               label_visibility="collapsed",
                               key="dfs_profile_player")
    canonical_id = id_by_name[chosen_name]
    mine = season_rows[season_rows["canonical_id"] == canonical_id]

    headshots = mine["headshot_url"].dropna()
    with image_slot.container(horizontal_alignment="center"):
        if not headshots.empty:
            st.image(headshots.iloc[-1], width=220)

    latest = mine.sort_values("week").iloc[-1]
    st.caption(f"{latest['position']} · {latest['team']}")

    # ---------------------------------------------------------------------
    # What he costs on the most recently loaded slate
    # ---------------------------------------------------------------------
    st.markdown("**Salaries**")
    prices, price_season, price_week = load_prices()

    if prices is None:
        st.caption("No slate loaded. Run scripts/load_salaries.py after "
                   "downloading the exports.")
    else:
        his = prices[prices["canonical_id"] == canonical_id]
        if his.empty:
            # He is not on the slate, or his name never resolved to an id --
            # both are worth saying, because they mean different things.
            st.caption(f"Not priced on the week {price_week}, {price_season} "
                       "slate.")
        else:
            st.caption(f"Week {price_week}, {price_season}")
            for row in his.itertuples():
                cost = f"${row.salary:,.0f}" if pd.notna(row.salary) else "—"
                st.metric(row.site, cost,
                          help=f"{row.site} lists him at {row.position} "
                               f"({row.roster_positions}) against "
                               f"{row.opponent}.")

            flags = {row.injury_status for row in his.itertuples()
                     if isinstance(row.injury_status, str) and row.injury_status}
            if flags:
                st.warning(f"Injury flag: {', '.join(sorted(flags))}",
                           icon=":material/medical_services:")

    st.markdown("**Season so far**")
    played = mine[mine["offense_snaps"].fillna(0) > 0]
    st.metric("Games", len(played))
    if "total_fantasy_points" in mine.columns:
        points = pd.to_numeric(mine["total_fantasy_points"], errors="coerce")
        if points.notna().any():
            st.metric(f"{scoring} points per game", f"{points.mean():.1f}")

with right:
    # ---------------------------------------------------------------------
    # Form: is he trending? A rolling answer, so nobody has to read the rows
    # below and work it out.
    # ---------------------------------------------------------------------
    form = rolling_form(mine, canonical_id, games=FORM_GAMES)

    if form["games"]:
        with st.container(border=True):
            st.caption(f"Last {form['games']} games")
            cells = st.columns(5)

            def show(cell, label, value, fmt="{:.1f}", help=None):
                """Write one form figure, or a dash where there is nothing."""
                with cell:
                    text = "—" if value is None or not np.isfinite(value) \
                        else fmt.format(value)
                    st.metric(label, text, help=help)

            show(cells[0], "FP / game", form["points"])
            show(cells[1], "xFP / game", form["expected_points"],
                 help="What his opportunities were worth on average.")
            show(cells[2], "Gap", form["gap"], "{:+.1f}",
                 help="Points scored above or below what his chances were "
                      "worth. Sustained and positive is either skill or luck "
                      "about to run out; sustained and negative is a player "
                      "getting the work without the results yet.")
            show(cells[3], "Snap share", form["snap_share"] * 100
                 if np.isfinite(form["snap_share"]) else float("nan"),
                 "{:.0f}%")
            show(cells[4], "Target share", form["target_share"] * 100
                 if np.isfinite(form["target_share"]) else float("nan"),
                 "{:.0f}%")

    # ---------------------------------------------------------------------
    # The game logs, split by the question each answers
    # ---------------------------------------------------------------------
    tabs = tabs_for(latest["position"])
    for tab, (name, columns) in zip(st.tabs(list(tabs)), tabs.items()):
        with tab:
            table, present = shape(mine, columns)
            if table.empty or not present:
                st.caption("Nothing recorded for this view.")
                continue

            st.dataframe(
                table, hide_index=True, width="stretch", height=430,
                column_config={
                    column.field: st.column_config.NumberColumn(
                        column.label, format=column.format,
                        help=column.help or None)
                    if column.format else st.column_config.TextColumn(
                        column.label, help=column.help or None)
                    for column in present
                },
            )

    st.caption("Blank cells are missing data, not zeroes. Tracking and charting "
               "numbers only cover players above their sources' volume "
               "thresholds, so a quiet week often has none.")
