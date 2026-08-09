"""Daily Fantasy page: one team's offence, and what their defense gives up.

Three tabs, because there are three reasons to look a team up. You check their
OFFENSE when deciding whether to buy its players, their DEFENSE when deciding
whether to buy the players facing it, and their PLAY CALLING when you want to
know whether what the season averages say is still true this month.

The crest and name sit across the top, the controls down the left third, and the
tabs fill the remaining two thirds.

Nearly every number here comes from a service built in an earlier phase; this
page is mostly a matter of arranging them and putting each one next to the
league placing that makes it readable.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import numpy as np
import streamlit as st

from presentation.dfs_charts import player_trend_chart, weekly_tendency_chart
from presentation.dfs_gamelog import (FANTASY_RECEIVING, FANTASY_RUSHING,
                                      OPPS_RECEIVING, OPPS_RUSHING, ordinal)
from services.dfs_player_service import player_weeks, team_player_stats
from services.dfs_scoring import DfsScoring
from services.dfs_team_service import (
    defensive_allowances, implied_totals, league_ranks,
    neutral_script_description, offensive_tendencies, weekly_tendencies,
)
from streamlit_state import get_app_context

SITES = (DfsScoring.FANDUEL, DfsScoring.DRAFTKINGS)

ctx = get_app_context()
repo = ctx.dfs_read_repo


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

# The crest and name sit at the very top but need the team, which is chosen
# further down. Reserving the row first is what lets both be true.
header_slot = st.container()

controls_col, content_col = st.columns([1, 2])

with controls_col:
    with st.container(border=True):
        team_slot = st.container()
        season_slot = st.container()
        weeks_slot = st.container()
        scoring_slot = st.container()

# SEASON FIRST, even though it sits second on screen: the team list and the week
# range are both drawn from whichever season is chosen.
with season_slot:
    season = st.selectbox("Season", seasons, key="dfs_team_season")

with team_slot:
    teams = sorted(offensive_tendencies(repo, season)["team"].dropna().unique())
    team = st.selectbox("Team", teams, key="dfs_team_team")

with weeks_slot:
    weeks_available = repo.pbp().loc[repo.pbp()["season"] == season, "week"]
    first, last = int(weeks_available.min()), int(weeks_available.max())
    weeks = (st.slider("Weeks", first, last, (first, last),
                       key=f"dfs_team_weeks_{season}")
             if last > first else (first, last))

with scoring_slot:
    scoring = st.segmented_control(
        "Site", SITES, default=DfsScoring.FANDUEL,
        key="dfs_team_scoring", required=True,
    )
    scoring = scoring if scoring in SITES else DfsScoring.FANDUEL

# ---------------------------------------------------------------------------
# Header: who this is
# ---------------------------------------------------------------------------
reference = repo.teams()
badge = reference[reference["team_abbr"] == team]

with header_slot:
    crest, title = st.columns([1, 11], vertical_alignment="center")
    with crest:
        if not badge.empty and badge["team_logo_espn"].notna().any():
            st.image(badge["team_logo_espn"].iloc[0], width=64)
    with title:
        st.subheader(badge["team_name"].iloc[0] if not badge.empty else team)

with content_col:
    with st.container(border=True):
        offence_tab, defense_tab, playcalling_tab = st.tabs(
            ["Offense", "Defense", "Play Calling"])


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


# ---------------------------------------------------------------------------
# Play calling
# ---------------------------------------------------------------------------
with playcalling_tab:
    st.markdown("**Play-calling week by week**")
    trend = weekly_tendencies(repo, season, weeks)
    if trend.empty:
        st.caption("Not enough neutral plays to plot.")
    else:
        st.altair_chart(weekly_tendency_chart(trend, team, "proe"),
                        width="stretch", theme=None)
        st.caption("The solid line is this team; the dashed one is the "
                   "league average that week. A gap is a bye.")
        st.caption(f"Neutral script means {neutral_script_description()}.")

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


# ---------------------------------------------------------------------------
# Who does what: the same numbers per player, and how they moved
# ---------------------------------------------------------------------------
# The two halves of the offence, each pairing what a player DID with how much of
# the offence ran through him -- the Fantasy and Opportunities categories of the
# player profile's game log, so a stat means the same thing on both pages.
SIDES = {
    "Rushing": {
        "columns": FANTASY_RUSHING + OPPS_RUSHING,
        # Nobody else carries the ball on purpose. A tight end with one end-around
        # all season is noise in a table about who gets the carries.
        "positions": ("QB", "RB", "WR"),
        "volume": "carries",
    },
    "Receiving": {
        "columns": FANTASY_RECEIVING + OPPS_RECEIVING,
        # Quarterbacks are left out: their receiving line is a trick play.
        "positions": ("RB", "WR", "TE"),
        "volume": "targets",
    },
}

# Every stat either side can show, deduplicated -- several appear in both
# categories and are the same column each time.
PLOTTABLE = {}
for _side in SIDES.values():
    for _column in _side["columns"]:
        PLOTTABLE.setdefault(_column.field, _column.label)

SIDE_KEY = "dfs_team_side"

players = load_players(scoring)

tables_col, plot_col = st.columns([2, 1])

with tables_col:
    with st.container(border=True):
        # KEYED, and rerunning on change. Streamlit switches tabs in the browser
        # without re-running the script by default, so the plot beside them would go
        # on showing the previous tab's stat until something else forced a rerun.
        side_tabs = st.tabs(list(SIDES), key=SIDE_KEY, on_change="rerun")

        for side_tab, (side, spec) in zip(side_tabs, SIDES.items()):
            with side_tab:
                columns = spec["columns"]

                # Computed over the WHOLE team, then filtered. A share is a share of
                # the team's total, so narrowing the rows first would rebase it on
                # the handful of players left and make every number too big.
                stats = team_player_stats(players, team,
                                        [column.field for column in columns],
                                        season, weeks)
                if not stats.empty:
                    stats = stats[stats["position"].isin(spec["positions"])
                                & (stats[spec["volume"]].fillna(0) > 0)]
                    # By the tab's own headline number rather than by snaps, so the
                    # players the tab is ABOUT come first -- a receiver with one
                    # end-around should not head a table of carries.
                    stats = stats.sort_values(spec["volume"], ascending=False)

                if stats.empty:
                    st.caption("No players recorded for this team in these weeks.")
                    continue

                # A share arrives as a FRACTION and its format carries a per-cent
                # sign, so it has to be multiplied out before drawing -- 0.61
                # through "%.0f%%" renders as "1%", and anything under half a
                # per cent as "0%". The same step `shape` in
                # presentation/dfs_gamelog.py takes before it draws a game log.
                for column in columns:
                    if column.scale != 1.0 and column.field in stats.columns:
                        stats[column.field] = stats[column.field] * column.scale

                st.dataframe(
                    stats, hide_index=True, width="stretch", height=430,
                    column_config={
                        "name": st.column_config.TextColumn("Player", width=150),
                        "position": st.column_config.TextColumn("Pos", width=50),
                        "games": st.column_config.NumberColumn("G", width=45,
                                                            format="%d"),
                        **{column.field: st.column_config.NumberColumn(
                            column.label, format=column.format,
                            help=column.help or None, width=column.width)
                        for column in columns},
                    },
                )

        st.caption("Counts are PER GAME, so a player who missed half the range is "
                "still comparable. Shares are his total over the team's total "
                "over the same weeks — never an average of the weekly shares.")

# Which tab is showing. `st.tabs` records it under its key; falling back to
# the first side covers the very first render, before anything is stored.
open_side = st.session_state.get(SIDE_KEY) or next(iter(SIDES))
if open_side not in SIDES:
    open_side = next(iter(SIDES))

with plot_col:
    with st.container(border=True):
        st.markdown(f"**Week by week** — {open_side.lower()}")

        roster = team_player_stats(players, team, ["carries"], season, weeks)
        names = list(roster["name"])

        picked = st.multiselect(
            "Players", names,
            # The three busiest, since team_player_stats orders by snaps. A plot
            # that starts empty asks for a click before it says anything.
            default=names[:3], key=f"dfs_team_plot_players_{team}_{season}",
        )
        # Whichever tab is open decides which stat the plot opens on -- carries
        # beside the rushing table, targets beside the receiving one.
        #
        # The KEY carries the side, which is what makes the default reapply: a
        # Streamlit widget ignores its default once its key has a stored value, so
        # sharing one key between the two sides would strand you on whichever stat
        # you last chose.
        options = list(PLOTTABLE)
        stat = st.selectbox("Stat", options,
                            index=options.index(SIDES[open_side]["volume"]),
                            format_func=lambda field: PLOTTABLE[field],
                            key=f"dfs_team_plot_stat_{open_side}")

        first, last = weeks
        lines = players[(players["team"] == team)
                        & (players["season"] == season)
                        & (players["week"].between(first, last))
                        & (players["name"].isin(picked))]

        if not picked:
            st.caption("Pick a player to plot.")
        elif stat not in lines.columns or lines[stat].notna().sum() == 0:
            st.caption("Nothing recorded for that stat in these weeks.")
        else:
            drawn = lines[["name", "week", stat]].rename(columns={stat: "value"})
            st.altair_chart(player_trend_chart(drawn, PLOTTABLE[stat]),
                            width="stretch", theme=None)
