"""Daily Fantasy page: one team's offence, and what their defense gives up.

Two tabs, because there are two reasons to look a team up. You check their
offence when deciding whether to buy its players, and you check their defense
when deciding whether to buy the players facing it.

Nearly every number here comes from a service built in an earlier phase; this
page is mostly a matter of arranging them and putting each one next to the
league placing that makes it readable.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import numpy as np
import streamlit as st

from presentation.dfs_charts import weekly_tendency_chart
from presentation.dfs_gamelog import ordinal
from services.dfs_player_service import player_weeks, team_usage
from services.dfs_scoring import DfsScoring
from services.dfs_team_service import (
    defensive_allowances, implied_totals, league_ranks,
    neutral_script_description, offensive_tendencies, weekly_tendencies,
)
from streamlit_state import get_app_context

ctx = get_app_context()
repo = ctx.dfs_read_repo

st.title("Team Profile")
st.caption("Daily Fantasy")


@st.cache_data(show_spinner="Joining player data…")
def load_players(scoring):
    """Build the player-week table once per scoring choice."""
    return player_weeks(repo, scoring)


# ---------------------------------------------------------------------------
# Which team, when, and in whose scoring
# ---------------------------------------------------------------------------
tendencies = offensive_tendencies(repo, repo.pbp()["season"].max())
if tendencies.empty:
    st.warning("No play-by-play loaded.", icon=":material/warning:")
    st.stop()

seasons = sorted(repo.pbp()["season"].dropna().unique(), reverse=True)
controls = st.columns([2, 2, 3, 2])

with controls[1]:
    season = st.selectbox("Season", seasons, key="dfs_team_season")
with controls[0]:
    teams = sorted(offensive_tendencies(repo, season)["team"].dropna().unique())
    team = st.selectbox("Team", teams, key="dfs_team_team")
with controls[2]:
    weeks_available = repo.pbp().loc[repo.pbp()["season"] == season, "week"]
    first, last = int(weeks_available.min()), int(weeks_available.max())
    weeks = (st.slider("Weeks", first, last, (first, last),
                       key=f"dfs_team_weeks_{season}")
             if last > first else (first, last))
with controls[3]:
    scoring = st.segmented_control(
        "Scoring", list(DfsScoring),
        default=DfsScoring.FANDUEL, key="dfs_team_scoring", required=True,
        label_visibility="collapsed",
    )
    scoring = scoring if scoring in tuple(DfsScoring) else DfsScoring.FANDUEL

# ---------------------------------------------------------------------------
# Header: who this is
# ---------------------------------------------------------------------------
reference = repo.teams()
badge = reference[reference["team_abbr"] == team]

crest, title = st.columns([1, 11])
with crest:
    if not badge.empty and badge["team_logo_espn"].notna().any():
        st.image(badge["team_logo_espn"].iloc[0], width=64)
with title:
    st.subheader(badge["team_name"].iloc[0] if not badge.empty else team)

offence_tab, defense_tab = st.tabs(["Offence", "Defense"])


def strip(figures):
    """Draw a row of headline numbers, each with its league placing beneath.

    Steps:
        1. Give each figure its own column.
        2. Draw a dash where there is no number, rather than formatting a blank
           into something that looks like a value.
        3. Show the placing as the caption -- a rank is what makes a raw number
           mean anything, so the two always travel together.

    Args:
        figures: A sequence of `(label, value, format, rank, out_of, help)`
            tuples. The value stays a NUMBER here and is formatted inside, so
            that "is there a value at all" can be asked before it becomes text
            -- a formatted blank is just a string and looks fine.

    Returns:
        None. The work is what is drawn.
    """
    cells = st.columns(len(figures))
    for cell, (label, value, spec, rank, out_of, note) in zip(cells, figures):
        with cell:
            missing = value is None or not np.isfinite(value)
            st.metric(label, "—" if missing else spec.format(value),
                      help=note or None)
            if rank is not None and not missing:
                st.caption(f"{ordinal(rank)} of {out_of}")


# ---------------------------------------------------------------------------
# Offence
# ---------------------------------------------------------------------------
with offence_tab:
    offence = league_ranks(
        offensive_tendencies(repo, season, weeks),
        ["proe", "pass_rate", "seconds_per_play", "plays_per_game",
         "red_zone_trips_per_game"],
        # Fewer seconds between snaps is a faster offence, which is the good
        # news. Everything else here is better when it is larger.
        lower_is_better=("seconds_per_play",),
    )
    market = implied_totals(repo, season, weeks)
    offence = offence.merge(market, on="team", how="left")
    offence = league_ranks(offence, ["implied_total"])

    if team not in set(offence["team"]):
        st.info("This team has no plays in the weeks selected.",
                icon=":material/filter_alt_off:")
    else:
        row = offence.set_index("team").loc[team]
        out_of = len(offence)

        strip([
            ("Pass rate over expected", row["proe"], "{:+.1f}",
             row["proe_rank"], out_of,
             "Percentage points above what a typical team would throw in the "
             "same spots. The measure that separates an offence that LIKES "
             "throwing from one that is always behind."),
            ("Neutral pass rate", row["pass_rate"], "{:.0%}",
             row["pass_rate_rank"], out_of, None),
            ("Pace", row["seconds_per_play"], "{:.1f}s",
             row["seconds_per_play_rank"], out_of,
             "Seconds between snaps on neutral plays. Fewer is faster, and a "
             "faster offence runs more plays for its players to score on."),
            ("Plays per game", row["plays_per_game"], "{:.1f}",
             row["plays_per_game_rank"], out_of, None),
            ("Red-zone trips", row["red_zone_trips_per_game"], "{:.2f}",
             row["red_zone_trips_per_game_rank"], out_of,
             "Drives per game reaching inside the twenty."),
            ("Implied total", row.get("implied_total", np.nan), "{:.1f}",
             row.get("implied_total_rank"), out_of,
             "Points the betting market expected this team to score, averaged "
             "over the weeks selected."),
        ])
        st.caption(f"Neutral script means {neutral_script_description()}.")

        st.divider()
        st.markdown("**Who gets the ball**")
        usage = team_usage(load_players(scoring), team, season, weeks)

        if usage.empty:
            st.caption("No players recorded for this team in these weeks.")
        else:
            st.dataframe(
                usage, hide_index=True, width="stretch", height=430,
                column_config={
                    "name": st.column_config.TextColumn("Player", width=170),
                    "position": st.column_config.TextColumn("Pos", width=55),
                    "games": st.column_config.NumberColumn("G", width=45, format="%d"),
                    "snap_share": st.column_config.NumberColumn(
                        "Snap%", format="percent",
                        help="Share of the offence's snaps, averaged over his games."),
                    "targets": st.column_config.NumberColumn("Tgt", format="%d"),
                    "target_share": st.column_config.NumberColumn(
                        "Tgt%", format="percent",
                        help="His share of the team's targets over these weeks."),
                    "carries": st.column_config.NumberColumn("Car", format="%d"),
                    "carry_share": st.column_config.NumberColumn(
                        "Car%", format="percent"),
                    "red_zone_touches": st.column_config.NumberColumn(
                        "RZ", format="%d"),
                    "red_zone_share": st.column_config.NumberColumn(
                        "RZ%", format="percent",
                        help="His share of the team's work inside the twenty, "
                             "which is where touchdowns come from."),
                    "air_yards": st.column_config.NumberColumn(
                        "aDOT", format="%.1f",
                        help="Average depth of target. Blank for players the "
                             "tracking data does not cover."),
                    "points_per_game": st.column_config.NumberColumn(
                        "FP/g", format="%.1f"),
                    "expected_points_per_game": st.column_config.NumberColumn(
                        "xFP/g", format="%.1f"),
                },
            )
            st.caption("Shares are of the team's totals over the weeks "
                       "selected, not an average of the weekly shares — a "
                       "player who saw 40% of the targets in his only game did "
                       "not command 40% of the offence.")

        st.divider()
        st.markdown("**Play-calling week by week**")
        trend = weekly_tendencies(repo, season, weeks)
        if trend.empty:
            st.caption("Not enough neutral plays to plot.")
        else:
            st.altair_chart(weekly_tendency_chart(trend, team, "proe"),
                            width="stretch", theme=None)
            st.caption("The solid line is this team; the dashed one is the "
                       "league average that week. A gap is a bye.")

# ---------------------------------------------------------------------------
# Defense
# ---------------------------------------------------------------------------
with defense_tab:
    st.caption("What this defense allows. Everything here is better when it is "
               "SMALL, so a 1st placing means the stingiest in the league.")

    for kind, label, positions in (("rush", "Against the run", ["RB"]),
                                   ("pass", "Through the air", ["WR", "TE"])):
        allowed = league_ranks(
            defensive_allowances(repo, season, weeks, positions=positions,
                                 play_kind=kind, scoring=scoring),
            ["epa_per_play", "points_per_play", "plays_faced"],
            lower_is_better=("epa_per_play", "points_per_play"),
        )
        st.markdown(f"**{label}** — points allowed to "
                    f"{' and '.join(positions)}")

        if team not in set(allowed["team"]):
            st.caption("Nothing recorded for this team in these weeks.")
            continue

        row = allowed.set_index("team").loc[team]
        noun = "rush" if kind == "rush" else "pass attempt"
        strip([
            (f"Fantasy points per {noun}", row["points_per_play"], "{:.3f}",
             row["points_per_play_rank"], len(allowed),
             "What a play of this kind against them tends to be worth to the "
             "players you are choosing between."),
            (f"EPA allowed per {noun}", row["epa_per_play"], "{:+.3f}",
             row["epa_per_play_rank"], len(allowed),
             "Expected points added. How well the defense plays, which is not "
             "the same question as how much it pays out."),
            (f"{noun.capitalize()}s faced", row["plays_faced"], "{:.0f}",
             row["plays_faced_rank"], len(allowed),
             "Volume. A defense can be sound and still concede a lot simply by "
             "being on the field."),
            ("Fantasy points allowed", row["points_allowed"], "{:.0f}",
             None, None, None),
        ])
        st.divider()
