"""
Season-long page: which kick and punt returners are worth starting
"""

import streamlit as st

from services.return_game_service import (
    CURRENT_RULES_SEASONS, leaderboard, return_plays, week_opponents,
)
from streamlit_state import get_app_context

ctx = get_app_context()

repo = ctx.dfs_read_repo

MIN_TEAM_GAMES = 1

@st.cache_data(show_spinner="Reading the return game…")
def load_plays():
    """Build the punt/kickoff table once, then reuse it across reruns."""
    return return_plays(repo)


@st.cache_data(show_spinner="Simulating returners…")
def load_board(season, through_week, opponents, min_returns):
    """
        Run the simulation once per set of choices.

        Args:
            season: Season to build from.
            through_week: Last week of data to use.
            opponents: Dict of team to opponent, or None for a neutral board.
            min_returns: Minimum expected returns per game to qualify.

        Returns:
            pd.DataFrame: The leaderboard, one row per qualifying returner.
    """
    return leaderboard(load_plays(), season, through_week=through_week,
                       opponents=opponents, min_returns=min_returns)

st.subheader("Return Game")

plays = load_plays()

seasons = sorted(plays["season"].dropna().unique(), reverse=True)

controls, board_area = st.columns([1, 3])

with controls:
    with st.container(border=True):
        season = st.selectbox("Season", seasons, key="return_season")

        weeks = plays.loc[plays["season"] == season, "week"].dropna()
        first, last = int(weeks.min()), int(weeks.max())
        through_week = (st.slider("Data through week", first, last, last,
                                  key=f"return_through_{season}")
                                  if last > first else last)

        use_matchup = st.toggle(
            "Adjust for opponent", value=True, key="return_use_matchup",
            help="Scales each player's kick-return volume by how many returns "
                 "his opponent tends to allow. Worth about 1 point per "
                 "standard deviation, so it reorders the board only slightly.",
        )

        matchup_week = st.number_input(
            "Lineup for week", min_value=first, max_value=last + 1,
            value=min(through_week + 1, last + 1),
            key="return_matchup_week", disabled=not use_matchup,
        )

        min_returns = st.slider(
            "Minimum returns per game", 0.5, 3.0, 1.0, step=0.5,
            key="return_min_returns",
            help="Filters out backups who only return when someone is hurt.",
        )

games_played = (plays[(plays["season"] == season)
                      & (plays["week"] <= through_week)]
                .groupby("return_team")["game_id"].nunique())

if games_played.empty or games_played.median() < MIN_TEAM_GAMES:
    st.warning(
        f"Only {0 if games_played.empty else int(games_played.median())} games "
        f"of {season} played. Return roles reshuffle every offseason -- only "
        f"12% of teams keep the previous year's primary kick returner -- so "
        f"there is nothing reliable to project from yet. Check depth charts "
        f"and preseason usage instead.",
        icon=":material/warning:",
    )
    st.stop()

if season not in CURRENT_RULES_SEASONS:
    st.info(
        "The kickoff rules in this season differ from the current ones, so "
        "return volume is not comparable to today's.",
        icon=":material/info:",
    )

opponents = week_opponents(repo, season, matchup_week) if use_matchup else None

if use_matchup and not opponents:
    st.info(
        f"No week {matchup_week} games in the {season} schedule, so the board "
        f"below is matchup-neutral.",
        icon=":material/info:",
    )

board = load_board(season, through_week, opponents, min_returns)

with board_area:
    with st.container(border=True):
        if board.empty:
            st.caption("No returners clear that workload filter.")
        else:
            st.dataframe(
                board, hide_index=True, width="stretch", height=560,
                column_config={
                    "returner_name": st.column_config.TextColumn(
                        "Returner", width=140, pinned=True),
                    "return_team": st.column_config.TextColumn("Team", width=60),
                    "opponent": st.column_config.TextColumn("Opp", width=60),
                    "exp_ko_returns": st.column_config.NumberColumn(
                        "KR/gm", format="%.1f", width=70,
                        help="Expected kick returns per game."),
                    "exp_pr_returns": st.column_config.NumberColumn(
                        "PR/gm", format="%.1f", width=70,
                        help="Expected punt returns per game."),
                    "exp_points": st.column_config.NumberColumn(
                        "Proj", format="%.1f", width=70,
                        help="Average fantasy points across 10,000 simulated games."),
                    "p90_points": st.column_config.NumberColumn(
                        "Ceiling", format="%.1f", width=80,
                        help="90th percentile. THE COLUMN THAT MATTERS -- "
                             "projections cluster, ceilings separate."),
                    "prob_100": st.column_config.NumberColumn(
                        "100+ yds", format="percent", width=90,
                        help="How often he clears the 100-yard bonus."),
                    "ko_multiplier": st.column_config.NumberColumn(
                        "Matchup", format="%.2f", width=80,
                        help="Above 1.00 means the opponent allows more returns "
                             "than average. Already shrunk by half."),
                    # Kept in the frame for sorting but not worth screen space.
                    "returner_id": None, "ko_share": None, "pr_share": None,
                    "exp_total_returns": None, "median_points": None,
                    "p10_points": None, "prob_td": None,
                },
            )
            
        st.caption(
            "Projections cluster because single-game return volume is close to "
            "pure chance -- the variance in returns per team-game is no larger "
            "than randomness alone would produce. Sort by ceiling, not by "
            "projection."
        )